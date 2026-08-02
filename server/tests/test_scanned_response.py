# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the Cleartext / ScannedResponse type discipline.

Invariant (MCP security gate A17): MCP transport
handlers only accept ScannedResponse as a return type. A function
returning raw Cleartext fails type-check at build, and the runtime
guard refuses it at the boundary.
"""

from __future__ import annotations

import pytest

from workspaces.lock.scanned_response import (
    Cleartext,
    CleartextEgressError,
    ScannedResponse,
    LockAudit,
    assert_scanned,
    scan_payload,
)


# ---------------------------------------------------------------------------
# Wrapper construction + serialisation
# ---------------------------------------------------------------------------


def test_scanned_response_to_mcp_payload_dict_value():
    """Dict-valued response should flow through unchanged, plus lock audit."""
    sr = ScannedResponse(
        value={"folder": "/foo", "count": 3},
        audit=LockAudit(tier="A", total_findings=2),
    )
    out = sr.to_mcp_payload()
    assert out["folder"] == "/foo"
    assert out["count"] == 3
    assert "_lock_egress" in out
    assert out["_lock_egress"]["tier"] == "A"
    assert out["_lock_egress"]["total_findings"] == 2


def test_scanned_response_to_mcp_payload_scalar_value():
    """Non-dict value gets wrapped under 'value' key."""
    sr = ScannedResponse(value=42, audit=LockAudit())
    out = sr.to_mcp_payload()
    assert out["value"] == 42
    assert "_lock_egress" in out


def test_scanned_response_preserves_existing_lock_block():
    """If the payload already has a 'lock' key, we don't clobber it —
    we annotate. The egress audit is added under a separate key."""
    sr = ScannedResponse(
        value={"lock": {"existing_field": "kept"}, "data": 1},
        audit=LockAudit(tier="A", total_findings=5),
    )
    out = sr.to_mcp_payload()
    assert out["lock"]["existing_field"] == "kept"
    # The egress-tier annotation gets merged into the existing block
    assert out["lock"]["egress_tier"] == "A"
    assert out["lock"]["egress_total_findings"] == 5


# ---------------------------------------------------------------------------
# Runtime egress guard
# ---------------------------------------------------------------------------


def test_assert_scanned_accepts_scanned_response():
    """Happy path: the guard passes for ScannedResponse."""
    sr = ScannedResponse(value={"ok": True})
    assert_scanned(sr)   # no raise


def test_assert_scanned_refuses_cleartext():
    """Cleartext crossing the egress boundary is the named build/runtime error."""
    ct = Cleartext(value={"pii": "alex\x40example.com"})
    with pytest.raises(CleartextEgressError) as exc_info:
        assert_scanned(ct)
    assert "Cleartext crossed an egress boundary" in str(exc_info.value)


def test_assert_scanned_refuses_raw_dict():
    """The most common 'forgot to scan' bug: returning a plain dict.
    Runtime guard catches it."""
    with pytest.raises(CleartextEgressError) as exc_info:
        assert_scanned({"plain": "dict"})
    assert "expected ScannedResponse" in str(exc_info.value)


def test_assert_scanned_refuses_string():
    """Any non-ScannedResponse type is refused."""
    with pytest.raises(CleartextEgressError):
        assert_scanned("just a string")


def test_assert_scanned_refuses_none():
    """None is not a ScannedResponse either."""
    with pytest.raises(CleartextEgressError):
        assert_scanned(None)


# ---------------------------------------------------------------------------
# Cleartext unwrap (legitimate pre-lock path)
# ---------------------------------------------------------------------------


def test_cleartext_unwrap_for_lock():
    """Cleartext.unwrap_for_lock() is the sanctioned way to access the
    pre-lock value from inside the lock itself. Not for egress."""
    ct = Cleartext(value={"raw": "data"})
    assert ct.unwrap_for_lock() == {"raw": "data"}


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def test_scan_payload_factory():
    """scan_payload() wraps a value with default audit."""
    sr = scan_payload({"hello": "world"})
    assert isinstance(sr, ScannedResponse)
    assert sr.value == {"hello": "world"}
    assert sr.audit.tier == "A"
    assert sr.audit.total_findings == 0


def test_scan_payload_with_custom_audit():
    """Custom audit gets persisted on the response."""
    audit = LockAudit(tier="C", total_findings=7, refused=1)
    sr = scan_payload({"x": 1}, audit=audit)
    assert sr.audit.refused == 1
    assert sr.audit.total_findings == 7
    out = sr.to_mcp_payload()
    assert out["_lock_egress"]["refused"] == 1


# ---------------------------------------------------------------------------
# End-to-end: MCP tool response shape
# ---------------------------------------------------------------------------


def test_pair_safe_context_returns_scanned_payload(tmp_path, monkeypatch):
    """Integration check: pair_safe_context produces a wrapped response
    with the _lock_egress audit block. Anything else means egress
    discipline broke."""
    log_root = tmp_path / "log"
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    folder = tmp_path / "vault"
    folder.mkdir()

    from workspaces.memory import WorkspaceMemory
    WorkspaceMemory(str(folder), log_root=str(log_root), actor="test").remember({
        "id": "sha256:test-pair-1",
        "problem": {
            "id": "sha256:test-pair-1-p",
            "scope": "test", "type": "rule",
            "summary": "a rule",
            "facets": {"domain": "test", "subject": "actor"},
            "source_document": str(folder / "doc.txt"),
        },
        "solution": {
            "id": "sha256:test-pair-1",
            "problem_id": "sha256:test-pair-1-p",
            "body": "body", "body_format": "prose",
            "authority_tier": 1, "confidence": 1.0,
        },
    }, channel="document")

    from workspaces.mcp_server import pair_safe_context
    response = pair_safe_context(
        folder_context=str(folder),
        pair_id="sha256:test-pair-1",
        mode="safe_minimal",
    )
    # The response IS a dict (FastMCP transport), but it carries the
    # egress audit block as proof it went through the wrap.
    assert isinstance(response, dict)
    assert "_lock_egress" in response, \
        f"egress audit missing — response wasn't wrapped: {response}"
    assert response["_lock_egress"]["tier"] in {"A", "ingest-cached"}
