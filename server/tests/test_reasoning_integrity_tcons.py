# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""T-cons — the reasoning-integrity acceptance (work/rvnd-tcons-wiring-spec.md).

Session-scoped Versum working memory + solver consistency, fail-closed:
C → CONSISTENT; C ∧ ¬C → INCONSISTENT (⊥, clashing atom carried); ungrounded
or uncheckable → OPEN and NEVER consistent; sessions are isolated stores.
"""
from __future__ import annotations

import pytest

from workspaces.reasoning_integrity import (
    CONSISTENT, INCONSISTENT, OPEN, Claim, check_session, record_claim,
    session_store,
)

G = "span:doc-1:12-40"   # any Versum span marker — grounded is what matters


def test_single_grounded_claim_is_consistent(tmp_path):
    log = str(tmp_path)
    record_claim("S", Claim(atom="c", grounding=G, ts="t0"), log_root=log)
    v = check_session("S", log_root=log)
    assert v.verdict == CONSISTENT, v


def test_contradiction_is_inconsistent_and_carries_the_clash(tmp_path):
    log = str(tmp_path)
    record_claim("S", Claim(atom="c", grounding=G, ts="t0"), log_root=log)
    record_claim("S", Claim(atom="c", polarity="-", grounding=G, ts="t1"), log_root=log)
    v = check_session("S", log_root=log)
    assert v.verdict == INCONSISTENT, v
    assert "c" in v.clashing, v
    assert any("clash" in r for r in v.reasons), v


def test_ungrounded_claim_is_open_never_consistent(tmp_path):
    log = str(tmp_path)
    record_claim("S", Claim(atom="c", grounding=None, ts="t0"), log_root=log)
    v = check_session("S", log_root=log)
    assert v.verdict == OPEN, v
    assert v.verdict != CONSISTENT
    assert "c" in v.open_claims, v


def test_empty_session_is_open_not_consistent(tmp_path):
    # audit() over an empty fact set says consistent=True (verified live) —
    # the seam must own this boundary: nothing recorded → OPEN.
    v = check_session("S-empty", log_root=str(tmp_path))
    assert v.verdict == OPEN, v


def test_grounded_clash_dominates_open(tmp_path):
    # A real ⊥ among grounded claims is reported even while others are open.
    log = str(tmp_path)
    record_claim("S", Claim(atom="c", grounding=G, ts="t0"), log_root=log)
    record_claim("S", Claim(atom="c", polarity="-", grounding=G, ts="t1"), log_root=log)
    record_claim("S", Claim(atom="d", grounding=None, ts="t2"), log_root=log)
    v = check_session("S", log_root=log)
    assert v.verdict == INCONSISTENT, v
    assert "d" in v.open_claims, v


def test_sessions_are_isolated(tmp_path):
    log = str(tmp_path)
    record_claim("S", Claim(atom="c", grounding=G, ts="t0"), log_root=log)
    record_claim("S2", Claim(atom="c", polarity="-", grounding=G, ts="t0"), log_root=log)
    assert session_store("S", log_root=log) != session_store("S2", log_root=log)
    assert check_session("S", log_root=log).verdict == CONSISTENT
    assert check_session("S2", log_root=log).verdict == CONSISTENT
    record_claim("S2", Claim(atom="c", grounding=G, ts="t1"), log_root=log)
    assert check_session("S2", log_root=log).verdict == INCONSISTENT
    assert check_session("S", log_root=log).verdict == CONSISTENT  # untouched


def test_notation_forging_atom_is_refused_at_record_time(tmp_path):
    # An atom must not be able to smuggle extra "fact:" lines into the solver
    # notation — eager refusal, not check-time sanitisation.
    with pytest.raises(ValueError, match="refused"):
        record_claim("S", Claim(atom="c\nfact: -x", grounding=G), log_root=str(tmp_path))
    with pytest.raises(ValueError, match="polarity"):
        record_claim("S", Claim(atom="c", polarity="±", grounding=G), log_root=str(tmp_path))


def test_reachable_through_the_workspace_workflow_facade(tmp_path, monkeypatch):
    # Route evidence: the op dispatches through the workspace_workflow facade
    # (keyword form binds the facade to the op for the register's evidence
    # matcher). Append-then-check with claim, pure read without.
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path))
    from workspaces.mcp_server import workspace_workflow
    r1 = workspace_workflow(op="reasoning_check",
                            params={"session_id": "S-op",
                                    "claim": {"atom": "c", "polarity": "+",
                                              "grounding": G, "ts": "t0"}})
    assert r1.get("ok") and r1["verdict"] == CONSISTENT, r1
    r2 = workspace_workflow(op="reasoning_check",
                            params={"session_id": "S-op",
                                    "claim": {"atom": "c", "polarity": "-",
                                              "grounding": G, "ts": "t1"}})
    assert r2["verdict"] == INCONSISTENT and "c" in r2["clashing"], r2
    r3 = workspace_workflow(op="reasoning_check", params={"session_id": "S-op"})
    assert r3["verdict"] == INCONSISTENT, r3   # pure read, state unchanged
