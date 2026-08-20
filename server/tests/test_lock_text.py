# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for lock_text() — the document/KG-triple approval entry point.

lock_text wraps Tier B (regex) + Tier C (semantic with confidential context)
without the ToolCall machinery. It is the surface a cloud-LLM boundary calls
when a document or KG triple is about to leave local context.
"""

from __future__ import annotations

import json

from workspaces.lock import (
    AuditLog,
    Mode,
    lock_text,
)
from workspaces.lock.tier_c import reset_backend_cache


# ---------------------------------------------------------------------------
# Clear-info passes
# ---------------------------------------------------------------------------


def test_lock_text_allows_clear_info(monkeypatch):
    """Text with no PII regex match and no confidential context = allow."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text("the build pipeline finishes in twelve minutes.")
    assert decision.action == "allow"
    assert decision.findings == []
    assert decision.redacted_text is None


# ---------------------------------------------------------------------------
# Tier B (regex) — high-severity refuses in STANDARD mode
# ---------------------------------------------------------------------------


def test_lock_text_refuses_email_in_standard_mode(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text("contact me at alex\x40example.com about it")
    assert decision.action == "refuse"
    assert any(f.tier == "B" for f in decision.findings)
    assert "high-severity" in decision.reason.lower()


def test_lock_text_refuses_iban_in_standard_mode(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text("transfer to DE89370400440532013000 today.")
    assert decision.action == "refuse"
    assert any(f.tier == "B" and "iban" in f.detail for f in decision.findings)


# ---------------------------------------------------------------------------
# Confidential context (KG-sourced) — Tier C high-severity refuses
# ---------------------------------------------------------------------------


def test_lock_text_refuses_when_confidential_term_present(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text(
        "the project atlas architecture ships phase 3 in june.",
        context="- acme corp\n- project atlas\n- northwind",
    )
    assert decision.action == "refuse"
    assert any(f.tier == "C" and "confidential" in f.detail.lower() for f in decision.findings)


def test_lock_text_no_refuse_when_confidential_term_absent(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text(
        "the build pipeline finishes in twelve minutes.",
        context="- acme corp\n- brand",
    )
    assert decision.action == "allow"


# ---------------------------------------------------------------------------
# Mode behaviour
# ---------------------------------------------------------------------------


def test_lock_text_audit_only_never_refuses(monkeypatch):
    """AUDIT_ONLY records findings but always allows."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text(
        "contact me at alex\x40example.com",
        mode=Mode.AUDIT_ONLY,
    )
    assert decision.action == "allow"
    assert any(f.tier == "B" for f in decision.findings)
    assert "audit-only" in decision.reason.lower()


def test_lock_text_permissive_never_refuses(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text(
        "contact me at alex\x40example.com",
        mode=Mode.PERMISSIVE,
    )
    assert decision.action == "allow"
    assert any(f.tier == "B" for f in decision.findings)


def test_lock_text_strict_refuses_any_finding(monkeypatch):
    """STRICT mode refuses on any finding, regardless of severity."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text(
        "contact me at alex\x40example.com",
        mode=Mode.STRICT,
    )
    assert decision.action == "refuse"


# ---------------------------------------------------------------------------
# Source tagging (document | triple | freeform)
# ---------------------------------------------------------------------------


def test_lock_text_source_tag_default(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text("clean text")
    assert decision.source == "document"


def test_lock_text_source_tag_triple(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text("(workspaceversum, ships, phase3)", source="triple", context="workspaceversum")
    assert decision.source == "triple"
    assert decision.action == "refuse"


# ---------------------------------------------------------------------------
# Audit log writes
# ---------------------------------------------------------------------------


def test_lock_text_writes_audit_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLog(audit_path)
    lock_text("contact me at alex\x40example.com", audit=audit, source="document")
    lines = audit_path.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "text"
    assert entry["source"] == "document"
    assert entry["action"] == "refuse"
    assert entry["findings_count"] >= 1
    # Audit must NOT contain the raw text (only its length).
    assert "alex\x40example.com" not in lines[0]
    assert entry["text_length"] > 0


def test_lock_text_audit_includes_task_id(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLog(audit_path)
    lock_text("clean text", audit=audit, task_id="task-42")
    entry = json.loads(audit_path.read_text().strip())
    assert entry["task_id"] == "task-42"


# ---------------------------------------------------------------------------
# Minimise mode — redaction
# ---------------------------------------------------------------------------


def test_lock_text_redaction_helper_strips_emails():
    """Direct test of the redaction helper: regex matches become typed placeholders."""
    from workspaces.lock.core import _redact_text_with_regex
    out = _redact_text_with_regex("write to alice\x40example.com or bob\x40example.org")
    assert "alice\x40example.com" not in out
    assert "bob\x40example.org" not in out
    assert "[REDACTED-EMAIL]" in out


def test_lock_text_redaction_helper_strips_iban():
    """A structurally valid IBAN hits the strict per-country pattern, which
    outranks the permissive AA00... one in _TIER_B_PATTERNS."""
    from workspaces.lock.core import _redact_text_with_regex
    out = _redact_text_with_regex("IBAN DE89370400440532013000 needs processing")
    assert "DE89370400440532013000" not in out
    assert "[REDACTED-IBAN-FULL]" in out


def test_lock_text_redaction_helper_covers_full_tier_b_set():
    """Minimise-path redaction applies the full Tier B label set, not a subset."""
    from workspaces.lock.core import _redact_text_with_regex
    out = _redact_text_with_regex(
        "SSN 078-05-1120, key sk-abc123DEF456ghi789, card 4111 1111 1111 1111")
    assert "078-05-1120" not in out
    assert "sk-abc123DEF456ghi789" not in out
    assert "4111 1111 1111 1111" not in out
    assert "[REDACTED-US-SSN]" in out
    assert "[REDACTED-API-KEY]" in out
    assert "[REDACTED-CREDIT-CARD]" in out


def test_lock_text_redaction_helper_credit_card_stays_luhn_gated():
    """A 16-digit string that fails the Luhn checksum is not labelled a card."""
    from workspaces.lock.core import _redact_text_with_regex
    out = _redact_text_with_regex("order ref 1234 5678 9012 3456")
    assert "[REDACTED-CREDIT-CARD]" not in out


# ---------------------------------------------------------------------------
# Defensive: never raises
# ---------------------------------------------------------------------------


def test_lock_text_handles_empty_string(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text("")
    assert decision.action == "allow"
    assert decision.findings == []


def test_lock_text_handles_unicode(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = lock_text("café résumé naïve façade — multi-byte text")
    # No regex match, no confidential context = allow.
    assert decision.action == "allow"
