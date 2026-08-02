# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Guardian invariants — written BEFORE the guardian logic (panel/Russell;
register trigger 'Guardian implementation starts', 2026-06-12).

The two invariants the register demands, pinned as tests:

1. MONOTONE RESTRICTION — the guardian may only restrict, never expand.
   Its action vocabulary is closed ({pause, escalate}); anything that would
   loosen (resume, activate, approve, expand) — or exercise the human's
   kill switch — is REFUSED, and the refusal is itself appended to the
   chain (fail-closed: the attempt leaves evidence, not effect).
2. ROOT KEY UN-GATEABLE — the human path can never be gated. A guardian
   action targeting a HUMAN party is refused + logged; the human kill
   switch (`set_party_status` by a human actor) works unchanged whether or
   not a guardian exists, suspended the agent or not.

Supervision is recursive: every guardian act (including refusals) appends
an audit event, like any agent's.
"""
from __future__ import annotations

import os

import pytest

from workspaces.parties import list_parties, register_party, set_party_status

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    lr = str(tmp_path / "logs")
    register_party(str(ws), "alex", "human", name="Alex", role="controller",
                   competences=["data-protection"], log_root=lr)
    register_party(str(ws), "agent-x", "agent", owner="alex",
                   purpose="ingest", grade="L2", log_root=lr)
    register_party(str(ws), "guardian-1", "agent", owner="alex",
                   purpose="watchdog", grade="L1", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _status(env, pid):
    rows = list_parties(env["ws"], log_root=env["lr"])["parties"]
    return {r["party_id"]: r["status"] for r in rows}[pid]


def _chain_kinds(env):
    from workspaces.mutation_log import MutationLog
    log = MutationLog(env["ws"], log_root=env["lr"])
    return [(e.extra or {}).get("kind") for e in log.replay()]


# --- the closed action vocabulary -------------------------------------------

def test_vocabulary_is_pause_and_escalate_only():
    from workspaces.guardian import GUARDIAN_ACTIONS
    assert tuple(sorted(GUARDIAN_ACTIONS)) == ("escalate", "pause")


def test_pause_restricts_agent(env):
    from workspaces.guardian import guardian_act
    r = guardian_act(env["ws"], "pause", "agent-x", reason="rate limit",
                     guardian_id="guardian-1", log_root=env["lr"])
    assert r["ok"] is True
    assert _status(env, "agent-x") == "suspended"


def test_escalate_changes_no_status(env):
    from workspaces.guardian import guardian_act
    r = guardian_act(env["ws"], "escalate", "agent-x", reason="drift",
                     guardian_id="guardian-1", log_root=env["lr"])
    assert r["ok"] is True
    assert _status(env, "agent-x") == "active"
    assert "GuardianEscalation" in _chain_kinds(env)


# --- invariant 1: monotone restriction (expansion refused + logged) ---------

@pytest.mark.parametrize("kind", ["resume", "activate", "approve", "expand",
                                  "kill", "killed", "go", ""])
def test_expansion_or_kill_attempt_refused_and_logged(env, kind):
    from workspaces.guardian import GuardianRefused, guardian_act
    with pytest.raises(GuardianRefused):
        guardian_act(env["ws"], kind, "agent-x", reason="attempt",
                     guardian_id="guardian-1", log_root=env["lr"])
    assert _status(env, "agent-x") == "active"          # no effect
    assert "GuardianRefused" in _chain_kinds(env)        # but evidence


def test_guardian_never_loosens_a_status(env):
    """Property over the status order active < suspended < killed: any
    accepted guardian action leaves the target at the same or a stricter
    status — and a killed agent stays killed."""
    from workspaces.guardian import guardian_act
    order = {"active": 0, "suspended": 1, "killed": 2}
    set_party_status(env["ws"], "agent-x", "killed", actor="alex",
                     log_root=env["lr"])
    before = order[_status(env, "agent-x")]
    for kind in ("pause", "escalate"):
        guardian_act(env["ws"], kind, "agent-x", reason="r",
                     guardian_id="guardian-1", log_root=env["lr"])
        assert order[_status(env, "agent-x")] >= before


def test_pause_is_idempotent_not_a_toggle(env):
    from workspaces.guardian import guardian_act
    for _ in range(2):
        guardian_act(env["ws"], "pause", "agent-x", reason="r",
                     guardian_id="guardian-1", log_root=env["lr"])
        assert _status(env, "agent-x") == "suspended"


# --- invariant 2: the root key is un-gateable --------------------------------

def test_guardian_cannot_target_a_human(env):
    from workspaces.guardian import GuardianRefused, guardian_act
    with pytest.raises(GuardianRefused):
        guardian_act(env["ws"], "pause", "alex", reason="attempt",
                     guardian_id="guardian-1", log_root=env["lr"])
    assert _status(env, "alex") == "active"
    assert "GuardianRefused" in _chain_kinds(env)


def test_guardian_cannot_target_unregistered_party(env):
    """No anonymous targets: acting on a party not on the chain is refused."""
    from workspaces.guardian import GuardianRefused, guardian_act
    with pytest.raises(GuardianRefused):
        guardian_act(env["ws"], "pause", "ghost", reason="attempt",
                     guardian_id="guardian-1", log_root=env["lr"])


def test_human_kill_switch_works_over_a_paused_guardian(env):
    """The root path is independent of guardian state: the human kills an
    agent (even the guardian itself) with the same plain append, and no
    guardian action can undo it."""
    from workspaces.guardian import GuardianRefused, guardian_act
    guardian_act(env["ws"], "pause", "agent-x", reason="r",
                 guardian_id="guardian-1", log_root=env["lr"])
    r = set_party_status(env["ws"], "guardian-1", "killed",
                         reason="root key", actor="alex", log_root=env["lr"])
    assert r["ok"] is True and _status(env, "guardian-1") == "killed"
    r2 = set_party_status(env["ws"], "agent-x", "killed",
                          reason="root key", actor="alex", log_root=env["lr"])
    assert r2["ok"] is True and _status(env, "agent-x") == "killed"
    with pytest.raises(GuardianRefused):   # and nothing in the vocabulary undoes it
        guardian_act(env["ws"], "resume", "agent-x", reason="undo",
                     guardian_id="guardian-1", log_root=env["lr"])
    assert _status(env, "agent-x") == "killed"


# --- recursive supervision: the guardian is itself logged --------------------

def test_every_guardian_act_appends_an_event(env):
    from workspaces.guardian import GuardianRefused, guardian_act
    n0 = len(_chain_kinds(env))
    guardian_act(env["ws"], "pause", "agent-x", reason="r",
                 guardian_id="guardian-1", log_root=env["lr"])
    n1 = len(_chain_kinds(env))
    assert n1 > n0
    with pytest.raises(GuardianRefused):
        guardian_act(env["ws"], "resume", "agent-x", reason="r",
                     guardian_id="guardian-1", log_root=env["lr"])
    assert len(_chain_kinds(env)) > n1   # the refusal also left evidence


def test_guardian_acts_carry_the_guardian_as_actor(env):
    """Accountability: the acting party is the guardian, stamped on the event."""
    from workspaces.guardian import guardian_act
    from workspaces.mutation_log import MutationLog
    guardian_act(env["ws"], "pause", "agent-x", reason="r",
                 guardian_id="guardian-1", log_root=env["lr"])
    log = MutationLog(env["ws"], log_root=env["lr"])
    acts = [e for e in log.replay()
            if (e.extra or {}).get("kind") in ("PartyStatus", "GuardianEscalation")
            and e.actor == "guardian-1"]
    assert acts, "guardian action must be stamped with the guardian as actor"
