# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B2 (0.6.8): ``audit_verify_chain`` as MCP tool.

Surface parity test: the chain-integrity primitive (CLI's
``workspaces status`` calls ``MutationLog.verify_chain()``) is now reachable
via MCP so non-Python clients (Cursor, Cline, Continue, custom hosts) can
verify a folder's tamper-evidence state without shelling out.

Tests cover:

- Happy path on a fresh folder (no events).
- Reports the events accumulated after ingest.
- Detects tampering (mismatched prev_hash → ok=False).
- Reports ``purged_with_tombstone`` after a controller-signed B1 purge.
- Surfaces operator pubkey fingerprint + host_id.
- D8: each call self-logs an audit breadcrumb (audit-of-audit).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces import mcp_server
from workspaces.mutation_log import MutationLog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Bind log root + key dir to per-test tmp dirs and initialise both
    keypairs (so a purge in one test won't be refused for lack of a
    controller key).
    """
    log_root = tmp_path / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))

    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    from workspaces import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    return {"log_root": log_root, "keydir": keydir, "workspace": workspace}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_audit_verify_chain_happy_path(isolated_env):
    """A fresh folder with no events: ok=True, total_events=0."""
    workspace = isolated_env["workspace"]
    result = mcp_server.audit_verify_chain(folder_context=str(workspace))

    assert result["ok"] is True
    assert result["total_events"] == 0
    assert result["broken_links"] == []
    assert result["signature_failures"] == []
    assert result["malformed_lines"] == 0
    assert result["purged_with_tombstone"] == 0
    assert result["folder_context"] == str(workspace.resolve())


def test_audit_verify_chain_after_ingest(isolated_env):
    """After three appends, total_events should reflect at least those three.

    (May be higher because audit_verify_chain itself self-logs — D8 — so
    repeated test runs would see more events. Use >= to be robust.)
    """
    workspace = isolated_env["workspace"]
    log = MutationLog(workspace, log_root=isolated_env["log_root"])
    log.append_raw(event="ingest", pair_id="sha256:a",
                    lifecycle_state="ingested", actor="t")
    log.append_raw(event="ingest", pair_id="sha256:b",
                    lifecycle_state="ingested", actor="t")
    log.append_raw(event="ingest", pair_id="sha256:c",
                    lifecycle_state="ingested", actor="t")

    result = mcp_server.audit_verify_chain(folder_context=str(workspace))
    assert result["ok"] is True
    assert result["total_events"] >= 3, (
        f"expected at least 3 events, got {result['total_events']}"
    )


def test_audit_verify_chain_detects_tampering(isolated_env):
    """Corrupt one event's prev_hash — verify must report ok=False."""
    workspace = isolated_env["workspace"]
    log = MutationLog(workspace, log_root=isolated_env["log_root"])
    log.append_raw(event="ingest", pair_id="sha256:p1",
                    lifecycle_state="live", actor="t")
    log.append_raw(event="ingest", pair_id="sha256:p2",
                    lifecycle_state="live", actor="t")
    log.append_raw(event="ingest", pair_id="sha256:p3",
                    lifecycle_state="live", actor="t")

    raw = log.log_file.read_text().splitlines()
    middle = json.loads(raw[1])
    middle["prev_hash"] = "deadbeef" * 8  # bogus
    raw[1] = json.dumps(middle)
    log.log_file.write_text("\n".join(raw) + "\n")

    result = mcp_server.audit_verify_chain(folder_context=str(workspace))
    assert result["ok"] is False
    assert len(result["broken_links"]) >= 1
    # The corrupted event's audit_id should appear in broken_links.
    bad_audit_ids = {b.get("audit_id") for b in result["broken_links"]}
    assert middle.get("audit_id") in bad_audit_ids


def test_audit_verify_chain_after_purge(isolated_env):
    """After a B1 purge: ok=True (tombstone-authorised), purged_with_tombstone>=1."""
    workspace = isolated_env["workspace"]
    log = MutationLog(workspace, log_root=isolated_env["log_root"])
    log.append_raw(event="ingest", pair_id="sha256:victim",
                    lifecycle_state="ingested", actor="t")
    log.append_raw(event="ingest", pair_id="sha256:victim",
                    lifecycle_state="live", actor="t")
    log.append_raw(event="ingest", pair_id="sha256:keeper",
                    lifecycle_state="ingested", actor="t")

    n = log.purge(
        "sha256:victim",
        legal_basis="art_17_1_b",
        requester_ref="req:42",
        reason="subject withdrew consent",
    )
    assert n == 2

    result = mcp_server.audit_verify_chain(folder_context=str(workspace))
    assert result["ok"] is True, (
        f"chain should remain ok post-purge: broken={result['broken_links']} "
        f"sig_fail={result['signature_failures']}"
    )
    assert result["purged_with_tombstone"] >= 1


def test_audit_verify_chain_returns_fingerprints(isolated_env):
    """Operator pubkey fingerprint + host_id must be present in the response."""
    workspace = isolated_env["workspace"]
    result = mcp_server.audit_verify_chain(folder_context=str(workspace))

    assert "public_key_fingerprint" in result
    assert isinstance(result["public_key_fingerprint"], str)
    assert len(result["public_key_fingerprint"]) == 16  # 16 hex chars per spec
    assert "host_id" in result
    assert isinstance(result["host_id"], str)
    assert len(result["host_id"]) == 12  # 12 hex chars per spec
    # Controller fingerprint also exposed (None if not initialised).
    assert "controller_key_fingerprint" in result
    # In this fixture we initialised it, so it should be a 16-hex string.
    assert isinstance(result["controller_key_fingerprint"], str)
    assert len(result["controller_key_fingerprint"]) == 16


def test_audit_verify_chain_self_logs(isolated_env):
    """D8 audit-of-audit: each call writes a verify_chain_read breadcrumb,
    so the second call sees one more event than the first.
    """
    workspace = isolated_env["workspace"]
    # Seed with a single event so we have a baseline.
    log = MutationLog(workspace, log_root=isolated_env["log_root"])
    log.append_raw(event="ingest", pair_id="sha256:p1",
                    lifecycle_state="ingested", actor="t")

    first = mcp_server.audit_verify_chain(folder_context=str(workspace))
    second = mcp_server.audit_verify_chain(folder_context=str(workspace))

    # The second call's total_events should be > the first's because the
    # first call's self-log breadcrumb is now in the chain.
    assert second["total_events"] > first["total_events"], (
        f"expected self-log breadcrumb to add at least one event between "
        f"calls: first={first['total_events']} second={second['total_events']}"
    )

    # Inspect the actual chain to confirm the breadcrumb event kind.
    events = list(log.replay())
    breadcrumb_events = [
        e for e in events
        if e.event == "system" and isinstance(e.extra, dict)
        and e.extra.get("kind") == "verify_chain_read"
    ]
    assert len(breadcrumb_events) >= 1, (
        "expected at least one verify_chain_read breadcrumb in the chain"
    )
