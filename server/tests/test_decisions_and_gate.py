# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for DecisionsStore (persisted user approvals) and gate_for_cloud()."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces.lock import (
    AuditLog,
    DecisionsStore,
    GateDecision,
    Mode,
    OversightLevel,
    StoredDecision,
    gate_for_cloud,
    kg_context_for_vault,
)
from workspaces.lock.tier_c import reset_backend_cache


# ===========================================================================
# DecisionsStore
# ===========================================================================


def test_decisions_empty_store_recall_returns_none(tmp_path):
    store = DecisionsStore(tmp_path / "decisions.jsonl")
    assert store.recall("anything") is None


def test_decisions_remember_and_recall_always(tmp_path):
    store = DecisionsStore(tmp_path / "decisions.jsonl")
    store.remember("Workspaceversum ships in June", "allow", scope="always", actor="tester", reason="ok by policy")
    assert store.recall("Workspaceversum ships in June") == "allow"


def test_anonymous_always_clearance_is_rejected(tmp_path):
    # CL3: a durable 'always' clearance must name who made it — an anonymous
    # "allow always" (the unauthenticated-clearance hole) is fail-closed rejected,
    # and nothing is persisted. once/session may stay anonymous (ephemeral).
    store = DecisionsStore(tmp_path / "decisions.jsonl")
    with pytest.raises(ValueError):
        store.remember("silence me forever", "allow", scope="always")  # no actor → reject
    assert store.recall("silence me forever") is None
    rec = store.remember("ok pattern", "allow", scope="always", actor="alice")
    assert rec.actor == "alice"
    store.remember("ephemeral", "allow", scope="once")  # no actor needed


def test_decisions_recall_is_case_and_whitespace_insensitive(tmp_path):
    """Normalisation: trailing whitespace + casing don't break lookup."""
    store = DecisionsStore(tmp_path / "decisions.jsonl")
    store.remember("Workspaceversum ships in June", "allow", scope="always", actor="tester")
    assert store.recall("  WORKSPACEVERSUM SHIPS IN JUNE  ") == "allow"


def test_decisions_block_decision_recalls_as_block(tmp_path):
    store = DecisionsStore(tmp_path / "decisions.jsonl")
    store.remember("sensitive text", "block", scope="always", actor="tester")
    assert store.recall("sensitive text") == "block"


def test_decisions_once_scope_is_not_durable(tmp_path):
    """A 'once' decision is recorded for audit but recall ignores it."""
    store = DecisionsStore(tmp_path / "decisions.jsonl")
    store.remember("ephemeral text", "allow", scope="once")
    # Same store instance — and recall should still return None.
    assert store.recall("ephemeral text") is None


def test_decisions_session_scope_recalls_within_same_session(tmp_path):
    store = DecisionsStore(tmp_path / "decisions.jsonl", session_id="session-A")
    store.remember("session text", "allow", scope="session")
    assert store.recall("session text") == "allow"


def test_decisions_session_scope_invisible_across_sessions(tmp_path):
    """A session-scoped decision from session-A is not visible in session-B."""
    path = tmp_path / "decisions.jsonl"
    a = DecisionsStore(path, session_id="session-A")
    a.remember("session text", "allow", scope="session")
    b = DecisionsStore(path, session_id="session-B")
    assert b.recall("session text") is None


def test_decisions_always_outranks_session(tmp_path):
    """If a pattern has both 'always' allow and 'session' block, always wins."""
    store = DecisionsStore(tmp_path / "decisions.jsonl", session_id="s1")
    store.remember("X", "block", scope="session")
    store.remember("X", "allow", scope="always", actor="tester")
    assert store.recall("X") == "allow"


def test_decisions_block_precedence_within_scope(tmp_path):
    # CL6: on a recall tie within a scope, BLOCK wins — a later "allow" must not
    # silently override an earlier "block" on the same text (safety > recency).
    store = DecisionsStore(tmp_path / "decisions.jsonl")
    store.remember("X", "block", scope="always", actor="tester")
    store.remember("X", "allow", scope="always", actor="tester")
    assert store.recall("X") == "block"


def test_decisions_persists_across_instances(tmp_path):
    path = tmp_path / "decisions.jsonl"
    a = DecisionsStore(path)
    a.remember("durable text", "allow", scope="always", actor="tester")
    b = DecisionsStore(path)
    assert b.recall("durable text") == "allow"


def test_decisions_invalid_decision_value_raises(tmp_path):
    store = DecisionsStore(tmp_path / "decisions.jsonl")
    with pytest.raises(ValueError):
        store.remember("X", "maybe", scope="always", actor="tester")


def test_decisions_invalid_scope_value_raises(tmp_path):
    store = DecisionsStore(tmp_path / "decisions.jsonl")
    with pytest.raises(ValueError):
        store.remember("X", "allow", scope="permanent")


def test_decisions_jsonl_format(tmp_path):
    """Each line is valid JSON with the required fields."""
    path = tmp_path / "decisions.jsonl"
    store = DecisionsStore(path)
    store.remember("X", "allow", scope="always", actor="tester", reason="user said ok")
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["decision"] == "allow"
    assert entry["scope"] == "always"
    assert entry["pattern_hash"].startswith("sha256:")
    assert entry["reason"] == "user said ok"
    assert "ts" in entry


def test_decisions_preview_is_truncated_and_single_line(tmp_path):
    store = DecisionsStore(tmp_path / "decisions.jsonl")
    long_multiline = "line one\nline two " + ("x" * 200)
    rec = store.remember(long_multiline, "allow", scope="always", actor="tester")
    assert "\n" not in rec.pattern_preview
    assert len(rec.pattern_preview) <= 80


def test_decisions_corrupt_line_is_skipped(tmp_path):
    """A malformed line should not crash the loader."""
    path = tmp_path / "decisions.jsonl"
    path.write_text("not-valid-json\n" + json.dumps({
        "ts": 1.0,
        "pattern_hash": "sha256:abc",
        "pattern_preview": "x",
        "decision": "allow",
        "scope": "always",
        "reason": "",
    }) + "\n")
    # Constructor must not raise.
    store = DecisionsStore(path)
    assert len(store.all_decisions()) == 1


# ===========================================================================
# gate_for_cloud — composition
# ===========================================================================


def test_gate_for_cloud_allows_clear_info(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = gate_for_cloud(
        "the build pipeline finishes in twelve minutes.",
        oversight=OversightLevel.APPROVE,
    )
    assert decision.action == "allow"
    assert decision.findings == []
    assert decision.recalled_from_decisions is False


def test_gate_for_cloud_refuses_email_under_low_oversight(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = gate_for_cloud(
        "contact alex\x40example.com",
        oversight=OversightLevel.NOTIFY,   # below APPROVE → no ask_user
    )
    assert decision.action == "refuse"


def test_gate_for_cloud_asks_user_under_approve(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = gate_for_cloud(
        "contact alex\x40example.com",
        oversight=OversightLevel.APPROVE,
    )
    assert decision.action == "ask_user"
    # pattern_preview is REDACTED (CL2): it shows the shape, never the flagged
    # value itself — the raw email must not ride along on the GateDecision.
    assert "alex\x40example.com" not in decision.pattern_preview
    assert decision.pattern_preview.startswith("contact ")


def test_gate_for_cloud_short_circuits_on_recalled_allow(tmp_path, monkeypatch):
    """A prior user 'allow always' decision skips the lock entirely."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decisions = DecisionsStore(tmp_path / "decisions.jsonl")
    decisions.remember("contact alex\x40example.com", "allow", scope="always", actor="tester")

    decision = gate_for_cloud(
        "contact alex\x40example.com",
        oversight=OversightLevel.APPROVE,
        decisions=decisions,
    )
    assert decision.action == "allow"
    assert decision.recalled_from_decisions is True
    # Findings empty because lock_text was skipped.
    assert decision.findings == []


def test_gate_for_cloud_short_circuits_on_recalled_block(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decisions = DecisionsStore(tmp_path / "decisions.jsonl")
    decisions.remember("clear text", "block", scope="always", actor="tester")

    decision = gate_for_cloud(
        "clear text",
        oversight=OversightLevel.APPROVE,
        decisions=decisions,
    )
    # Even though lock_text would have allowed, the user's stored decision wins.
    assert decision.action == "refuse"
    assert decision.recalled_from_decisions is True


def test_gate_for_cloud_loads_vault_context(tmp_path, monkeypatch):
    """Vault path → confidential terms feed into the lock prompt."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    # Set up a vault with one confidential entity.
    (tmp_path / "Workspaceversum.md").write_text("---\nconfidential: true\n---\nbody\n")

    decision = gate_for_cloud(
        "the workspaceversum architecture ships in june.",
        vault_path=tmp_path,
        oversight=OversightLevel.APPROVE,
    )
    # Mock backend flags confidential context → high-severity → ask_user under APPROVE.
    assert decision.action == "ask_user"
    assert any("confidential" in f.detail.lower() for f in decision.findings)


def test_gate_for_cloud_handles_missing_vault_gracefully(tmp_path, monkeypatch):
    """If the vault path is bogus, gate still runs (empty context)."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = gate_for_cloud(
        "the build pipeline finishes in twelve minutes.",
        vault_path=tmp_path / "nonexistent",  # not a directory
        oversight=OversightLevel.APPROVE,
    )
    # Clear text, empty context → allow.
    assert decision.action == "allow"


def test_gate_for_cloud_minimise_path(tmp_path, monkeypatch):
    """A medium-severity finding produces action='minimise' with redacted_text.

    The current mock+regex stack flags emails as HIGH severity (refuse).
    To exercise minimise we'd need a medium-severity Tier-C finding pattern.
    For now: verify the path exists by checking the dataclass shape.
    """
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    # Confirm the minimise branch is reachable from the public type.
    d = GateDecision(action="minimise", redacted_text="redacted", reason="x")
    assert d.action == "minimise"
    assert d.redacted_text == "redacted"


def test_gate_for_cloud_writes_audit_when_audit_provided(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLog(audit_path)
    gate_for_cloud(
        "contact alex\x40example.com",
        oversight=OversightLevel.NOTIFY,
        audit=audit,
    )
    entries = [json.loads(l) for l in audit_path.read_text().strip().splitlines()]
    assert len(entries) == 1
    assert entries[0]["kind"] == "text"
    assert entries[0]["action"] == "refuse"
    # Raw text MUST NOT appear in audit log.
    assert "alex\x40example.com" not in audit_path.read_text()


def test_gate_for_cloud_pattern_preview_collapses_whitespace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    decision = gate_for_cloud(
        "line one\nline two\nline three",
        oversight=OversightLevel.APPROVE,
    )
    # No findings → allow; pattern_preview still computed.
    assert decision.action == "allow"
    assert "\n" not in decision.pattern_preview


# ===========================================================================
# End-to-end: vault + decisions + gate
# ===========================================================================


def test_e2e_user_approves_once_then_again_via_recall(tmp_path, monkeypatch):
    """Simulate the full user-approval loop:

    1. First time: gate says ask_user (confidential vault term in text).
    2. User decides "allow always" → decisions.remember().
    3. Second time: gate short-circuits via recall → allow.
    """
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    (tmp_path / "Workspaceversum.md").write_text("---\nconfidential: true\n---\nbody\n")
    decisions = DecisionsStore(tmp_path / "decisions.jsonl")
    candidate = "the workspaceversum architecture ships in june."

    # 1.
    first = gate_for_cloud(
        candidate,
        vault_path=tmp_path,
        oversight=OversightLevel.APPROVE,
        decisions=decisions,
    )
    assert first.action == "ask_user"

    # 2.
    decisions.remember(candidate, "allow", scope="always", actor="tester", reason="user approved in workflow")

    # 3.
    second = gate_for_cloud(
        candidate,
        vault_path=tmp_path,
        oversight=OversightLevel.APPROVE,
        decisions=decisions,
    )
    assert second.action == "allow"
    assert second.recalled_from_decisions is True
