# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for 0.6.8 B3 — lock refusal per-incident remediation block.

Every Tier B / B+ finding now carries a canonical three-action remediation
block (``redact_and_retry``, ``bypass_once``, ``disable_lock``). The
surfaces (CLI / chat / generic MCP) read the same block; they don't invent
their own next-step suggestions.

These tests pin down remediation payloads and lock threshold behavior.
"""
from __future__ import annotations

import pytest

from workspaces.lock.core import (
    Finding,
    RemediationAction,
    _detect_confusable_bypass,
    tier_b_scan_dict,
    tier_b_scan_text,
)


# ---------------------------------------------------------------------------
# Tier B — every finding carries the canonical three-action block
# ---------------------------------------------------------------------------


def test_tier_b_finding_includes_three_remediation_actions():
    """An email-pattern Tier B finding must carry exactly the three canonical
    actions, in the documented order (redact / bypass / disable)."""
    findings = tier_b_scan_text("Contact me at j\x40a.com please")
    assert findings, "Tier B should match the email pattern"
    f = findings[0]

    assert isinstance(f.remediation_actions, list)
    assert len(f.remediation_actions) == 3, (
        f"expected three canonical actions; got {[a.kind for a in f.remediation_actions]}"
    )
    kinds = [a.kind for a in f.remediation_actions]
    assert kinds == ["redact_and_retry", "bypass_once", "disable_lock"], (
        f"action order is part of the contract; got {kinds}"
    )
    # Each is the right dataclass.
    for a in f.remediation_actions:
        assert isinstance(a, RemediationAction)
        assert a.label, "every action needs a human-readable label"
        assert isinstance(a.payload, dict)


def test_redact_and_retry_payload_replaces_match():
    """The redact-and-retry payload must carry a ``redacted_text`` with the
    matched span swapped for a ``[REDACTED:<label>]`` placeholder.

    Surface (CLI / chat) re-submits this in place of the original on user
    consent — so the redaction has to actually neutralise the match.
    """
    text = "Contact me at j\x40a.com please"
    findings = tier_b_scan_text(text)
    f = next(x for x in findings if "email" in x.detail)
    action = f.remediation_actions[0]
    assert action.kind == "redact_and_retry"
    redacted = action.payload.get("redacted_text", "")
    assert redacted, "redact_and_retry must carry redacted_text"
    assert "j\x40a.com" not in redacted, (
        f"original PII must be gone from redacted_text; got {redacted!r}"
    )
    assert "[REDACTED:email]" in redacted, (
        f"placeholder must mark the type that was redacted; got {redacted!r}"
    )
    # Surrounding text is preserved.
    assert "Contact me at" in redacted
    assert "please" in redacted


def test_bypass_once_requires_ack():
    """The bypass action declares an explicit acknowledgement requirement so
    surfaces know not to auto-send — a per-prompt consent must be captured."""
    findings = tier_b_scan_text(
        "api key " "sk_" "live_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa here"
    )
    assert findings
    f = findings[0]
    bypass = f.remediation_actions[1]
    assert bypass.kind == "bypass_once"
    assert bypass.payload.get("acknowledgement_required") is True


def test_disable_lock_action_has_cli_and_mcp():
    """The disable action surfaces both the CLI invocation AND the MCP tool
    name AND the disclaimer URL — three surface targets, one payload."""
    findings = tier_b_scan_text("My SSN is 123-45-6789, do not share")
    assert findings
    f = findings[0]
    disable = f.remediation_actions[2]
    assert disable.kind == "disable_lock"
    payload = disable.payload
    assert "cli" in payload, "disable_lock must offer a CLI invocation"
    assert "mcp" in payload, "disable_lock must offer an MCP tool name"
    assert "disclaimer_url" in payload, (
        "disable_lock must link the disclaimer the user has to read"
    )
    # The CLI must point at the right verb so users can copy-paste.
    assert "workspaces policy disable-lock" in payload["cli"]
    assert "--i-accept-the-risk" in payload["cli"]
    # The MCP tool name aligns with the canonical mcp_server registration.
    assert payload["mcp"] == "policy_disable_lock"


# ---------------------------------------------------------------------------
# Tier B+ — confusable-bypass findings ALSO carry the block
# ---------------------------------------------------------------------------


def test_confusable_bypass_finding_has_remediation_actions():
    """B+ findings (the homoglyph-bypass detector) must surface the same
    three-action block — same shape, same order, so surfaces don't have to
    fork their rendering between B and B+ findings.

    The redact-and-retry payload for B+ should carry the ASCII-folded
    text so the user can resubmit a homoglyph-free version.
    """
    cyrillic_a = "а"  # U+0430, NOT ASCII a
    text = f"{cyrillic_a}dmin@ex{cyrillic_a}mple.com sent the file"
    findings = _detect_confusable_bypass(text)
    assert findings, "B+ should detect the confusable bypass"
    f = findings[0]
    assert f.tier == "B+"
    assert isinstance(f.remediation_actions, list)
    assert len(f.remediation_actions) == 3
    kinds = [a.kind for a in f.remediation_actions]
    assert kinds == ["redact_and_retry", "bypass_once", "disable_lock"]
    # Each is the right dataclass.
    for a in f.remediation_actions:
        assert isinstance(a, RemediationAction)
        assert a.label
        assert isinstance(a.payload, dict)
    # The B+ redact_and_retry payload should carry redacted text that no
    # longer contains the Cyrillic homoglyphs (folded to ASCII first).
    redacted = f.remediation_actions[0].payload.get("redacted_text", "")
    assert redacted
    assert cyrillic_a not in redacted, (
        "redacted_text for a B+ finding must be ASCII-folded so the bypass "
        "isn't simply resubmitted"
    )


# ---------------------------------------------------------------------------
# tier_b_scan_dict propagates the same action block
# ---------------------------------------------------------------------------


def test_tier_b_scan_dict_findings_carry_remediation_actions():
    """Findings emitted via tier_b_scan_dict (the dict/recursive entry point
    used by egress + ingress) must carry the same action block — the dict
    walker just relabels f.field; the actions ride along."""
    findings = tier_b_scan_dict({"comment": "send to j\x40a.com asap"})
    assert findings
    f = findings[0]
    assert f.field == "comment"
    assert len(f.remediation_actions) == 3
    assert f.remediation_actions[0].kind == "redact_and_retry"
