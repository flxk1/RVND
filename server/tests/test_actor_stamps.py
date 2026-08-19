# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Acting-party stamps on chain events (§ 1.5) — written before the fix.

The TASKS verification line: a test asserting the stamp on EVERY
state-changing event. Measured gap driving this slice: painting the policy
matrix (`save_own_matrix` / `clear_own_matrix`) wrote the policy file with
NO chain event — a governance state change invisible to the audit chain.
After this slice: every governance surface appends an event, every event
carries a non-empty actor, and the caller's actor arrives verbatim (no
surface silently drops it to a default). `actor_stamp_report` is the
measurement projection: attributed (registered party) / builtin / unknown.
"""
from __future__ import annotations

import os

import pytest

from rvnd import policy_matrix as pm
from rvnd.mutation_log import MutationLog
from rvnd.parties import register_party, set_party_status

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

T0 = 1_900_000_000.0


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    lr = str(tmp_path / "logs")
    # hermetic log root for the FACADE path too (the stream-H rule: never
    # let a test write chain events under the real ~/.workspace)
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", lr)
    register_party(str(ws), "alex", "human", name="Alex",
                   competences=["data-protection"], actor="alex",
                   log_root=lr)
    register_party(str(ws), "anna", "human", competences=["data-protection"],
                   actor="alex", log_root=lr)
    register_party(str(ws), "agent-x", "agent", owner="alex",
                   purpose="ingest", grade="L2", actor="alex", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _events(env):
    return list(MutationLog(env["ws"], log_root=env["lr"]).replay())


# --- the measured gap: matrix paint now lands on the chain --------------------

def test_matrix_set_is_audited_with_actor(env):
    m = pm.recommended_default()
    pm.save_own_matrix(env["ws"], m, actor="alex", log_root=env["lr"])
    hits = [e for e in _events(env)
            if (e.extra or {}).get("policy_change") == "policy_matrix"]
    assert hits and hits[-1].actor == "alex"


def test_matrix_clear_is_audited_with_actor(env):
    pm.save_own_matrix(env["ws"], pm.recommended_default(), actor="alex",
                       log_root=env["lr"])
    pm.clear_own_matrix(env["ws"], actor="alex", log_root=env["lr"])
    hits = [(e.extra or {}) for e in _events(env)
            if (e.extra or {}).get("policy_change") == "policy_matrix"]
    assert hits[-1].get("cleared") is True


def test_workspace_matrix_facade_passes_actor_through(env):
    from rvnd import mcp_server
    mcp_server.workspace_matrix("set", {
        "folder_context": env["ws"], "grade": "L1", "oversight": "approve",
        "light": "block", "actor": "alex"})
    hits = [e for e in _events(env)
            if (e.extra or {}).get("policy_change") == "policy_matrix"]
    assert hits and hits[-1].actor == "alex"


# --- every state-changing surface stamps the GIVEN actor ----------------------

def test_every_governance_surface_stamps_the_given_actor(env):
    from rvnd.approvals import (decide_approval, delegate_competence,
                                 request_approval)
    from rvnd.guardian import guardian_act
    from rvnd.juris_packs import set_folder_packs
    from rvnd.policy import set_ai_training_optout, set_oversight_level

    set_party_status(env["ws"], "agent-x", "suspended", actor="alex",
                     log_root=env["lr"])
    set_party_status(env["ws"], "agent-x", "active", actor="alex",
                     log_root=env["lr"])
    guardian_act(env["ws"], "escalate", "agent-x", reason="drift",
                 guardian_id="guardian-w", log_root=env["lr"])
    request_approval(env["ws"], "r1", form="single_approver",
                     competence="data-protection", requester="agent-x",
                     timeout_seconds=60, now=T0, log_root=env["lr"])
    decide_approval(env["ws"], "r1", "approve", actor="anna", now=T0 + 1,
                    log_root=env["lr"])
    delegate_competence(env["ws"], "data-protection", from_party="anna",
                        to_party="alex", actor="anna", now=T0,
                        log_root=env["lr"])
    set_ai_training_optout(env["ws"], True, actor="alex",
                           log_root=env["lr"])
    set_oversight_level(env["ws"], "review", actor="alex",
                        log_root=env["lr"])
    set_folder_packs(env["ws"], ["eu-base"], actor="alex",
                     log_root=env["lr"])
    pm.save_own_matrix(env["ws"], pm.recommended_default(), actor="alex",
                       log_root=env["lr"])

    evts = _events(env)
    assert all(e.actor for e in evts), "an event with no actor exists"
    stamped = {e.actor for e in evts}
    assert {"alex", "anna", "guardian-w"} <= stamped


def test_no_surface_downgrades_a_given_actor_to_default(env):
    """The caller said 'alex'; no event from these calls may say 'user'."""
    from rvnd.juris_packs import set_folder_packs
    set_folder_packs(env["ws"], ["eu-base"], actor="alex",
                     log_root=env["lr"])
    pm.save_own_matrix(env["ws"], pm.recommended_default(), actor="alex",
                       log_root=env["lr"])
    govern = [e for e in _events(env)
              if (e.extra or {}).get("policy_change") in
              ("juris_packs", "policy_matrix")]
    assert govern and all(e.actor == "alex" for e in govern)


# --- the measurement projection ------------------------------------------------

def test_actor_stamp_report_classifies(env):
    from rvnd.parties import actor_stamp_report
    pm.save_own_matrix(env["ws"], pm.recommended_default(), actor="alex",
                       log_root=env["lr"])
    MutationLog(env["ws"], log_root=env["lr"]).append(
        __import__("rvnd.mutation_log", fromlist=["LogEvent"]).LogEvent(
            event="system", folder_path=env["ws"], pair_id="x",
            channel="system", actor="ghost-9", extra={}))
    r = actor_stamp_report(env["ws"], log_root=env["lr"])
    assert r["total"] >= 5
    assert r["unknown_actors"] == ["ghost-9"]
    assert r["attributed"] >= 4          # alex-stamped events
    assert r["unstamped"] == 0


def test_actor_stamp_report_clean_chain_has_no_unknowns(env):
    from rvnd.parties import actor_stamp_report
    r = actor_stamp_report(env["ws"], log_root=env["lr"])
    assert r["unknown_actors"] == [] and r["unstamped"] == 0
