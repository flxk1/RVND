# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Guardian watchdog — declarative rules over the chain, acting ONLY through
``guardian_act`` (concept § 1.1/§ 4: budget, rate, loop, drift; two actions).

Written before the watchdog logic. The TASKS verification line is pinned
here: an expansion attempt — a rule configured with an action outside the
monotone vocabulary — is REFUSED and the refusal lands on the chain.
Findings are computed from a replay snapshot (deterministic: same chain +
same rules + same `now` = same findings); absence of evidence is never a
finding (no baseline → no drift finding; empty chain → nothing).
"""
from __future__ import annotations

import os
import time

import pytest

from workspaces.guardian import GuardianRefused
from workspaces.mutation_log import LogEvent, MutationLog
from workspaces.parties import list_parties, register_party, set_party_status

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

NOW = time.time()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    lr = str(tmp_path / "logs")
    register_party(str(ws), "alex", "human", name="Alex",
                   competences=["data-protection"], log_root=lr)
    register_party(str(ws), "agent-x", "agent", owner="alex",
                   purpose="ingest", grade="L2", log_root=lr)
    register_party(str(ws), "guardian-1", "agent", owner="alex",
                   purpose="watchdog", grade="L1", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _emit(env, actor, n, pair_id="p1", ts=None):
    log = MutationLog(env["ws"], log_root=env["lr"])
    for i in range(n):
        log.append(LogEvent(event="system", folder_path=env["ws"],
                            pair_id=pair_id, channel="system", actor=actor,
                            ts=(ts if ts is not None else NOW) + i * 0.01))


def _status(env, pid):
    rows = list_parties(env["ws"], log_root=env["lr"])["parties"]
    return {r["party_id"]: r["status"] for r in rows}[pid]


def _chain_kinds(env):
    log = MutationLog(env["ws"], log_root=env["lr"])
    return [(e.extra or {}).get("kind") for e in log.replay()]


# --- the TASKS verification line: expansion attempt refused + logged --------

def test_rule_with_expansion_action_refused_and_logged(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "agent-x", 3)
    with pytest.raises(GuardianRefused):
        watch_tick(env["ws"], [WatchRule(kind="budget", limit=1,
                                         action="resume")],
                   guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)
    assert _status(env, "agent-x") == "active"      # no effect
    assert "GuardianRefused" in _chain_kinds(env)   # but evidence


def test_unknown_rule_kind_refused(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    with pytest.raises(GuardianRefused):
        watch_tick(env["ws"], [WatchRule(kind="vibes", limit=1)],
                   guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)


# --- budget ------------------------------------------------------------------

def test_budget_violation_pauses_agent_with_evidence(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "agent-x", 5)
    r = watch_tick(env["ws"], [WatchRule(kind="budget", limit=3)],
                   guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)
    assert _status(env, "agent-x") == "suspended"
    f = [x for x in r["findings"] if x["party_id"] == "agent-x"]
    assert f and f[0]["rule"] == "budget" and f[0]["count"] > f[0]["limit"]


def test_budget_under_limit_no_finding(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "agent-x", 2)
    r = watch_tick(env["ws"], [WatchRule(kind="budget", limit=3)],
                   guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)
    assert r["findings"] == []
    assert _status(env, "agent-x") == "active"


# --- rate --------------------------------------------------------------------

def test_rate_window_bounds_the_count(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "agent-x", 5, ts=NOW)
    # wide window sees all 5 -> violation
    r1 = watch_tick(env["ws"], [WatchRule(kind="rate", limit=3,
                                          window_seconds=3600)],
                    guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)
    assert any(x["rule"] == "rate" for x in r1["findings"])
    # window in the future of all events sees none -> no violation
    r2 = watch_tick(env["ws"], [WatchRule(kind="rate", limit=3,
                                          window_seconds=1)],
                    guardian_id="guardian-1", log_root=env["lr"],
                    now=NOW + 7200)
    assert [x for x in r2["findings"] if x["rule"] == "rate"] == []


# --- loop --------------------------------------------------------------------

def test_loop_consecutive_repeats_flagged(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "agent-x", 6, pair_id="same-op")
    r = watch_tick(env["ws"], [WatchRule(kind="loop", limit=4)],
                   guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)
    assert any(x["rule"] == "loop" for x in r["findings"])


def test_alternating_ops_are_not_a_loop(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    for i in range(6):
        _emit(env, "agent-x", 1, pair_id=f"op-{i % 2}", ts=NOW + i)
    r = watch_tick(env["ws"], [WatchRule(kind="loop", limit=4)],
                   guardian_id="guardian-1", log_root=env["lr"], now=NOW + 10)
    assert [x for x in r["findings"] if x["rule"] == "loop"] == []


# --- scope: agents only; the root path stays clear ---------------------------

def test_human_actor_never_flagged_or_paused(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "alex", 10)
    r = watch_tick(env["ws"], [WatchRule(kind="budget", limit=1)],
                   guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)
    assert [x for x in r["findings"] if x["party_id"] == "alex"] == []
    assert _status(env, "alex") == "active"


def test_killed_agent_stays_killed(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "agent-x", 5)
    set_party_status(env["ws"], "agent-x", "killed", actor="alex",
                     log_root=env["lr"])
    watch_tick(env["ws"], [WatchRule(kind="budget", limit=1)],
               guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)
    assert _status(env, "agent-x") == "killed"


# --- escalate action + drift delegation --------------------------------------

def test_escalate_rule_changes_no_status(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "agent-x", 5)
    watch_tick(env["ws"], [WatchRule(kind="budget", limit=1,
                                     action="escalate")],
               guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)
    assert _status(env, "agent-x") == "active"
    assert "GuardianEscalation" in _chain_kinds(env)


def test_drift_without_baseline_is_not_a_finding(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "agent-x", 3)
    r = watch_tick(env["ws"], [WatchRule(kind="drift")],
                   guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)
    assert [x for x in r["findings"] if x["rule"] == "drift"] == []


def test_drift_rule_always_escalates_never_pauses(env):
    """Drift findings route to the human 3-option surface (drift_monitor);
    the watchdog must not preempt it — a drift rule asking for pause is
    refused like any expansion of the watchdog's mandate."""
    from workspaces.guardian_watch import WatchRule, watch_tick
    with pytest.raises(GuardianRefused):
        watch_tick(env["ws"], [WatchRule(kind="drift", action="pause")],
                   guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)


# --- determinism + snapshot discipline ----------------------------------------

def test_same_chain_same_rules_same_now_same_findings(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "agent-x", 5)
    rules = [WatchRule(kind="budget", limit=3, action="escalate")]
    r1 = watch_tick(env["ws"], rules, guardian_id="guardian-1",
                    log_root=env["lr"], now=NOW + 1)
    r2 = watch_tick(env["ws"], rules, guardian_id="guardian-1",
                    log_root=env["lr"], now=NOW + 1)
    assert r1["findings"] == r2["findings"]


def test_empty_chain_and_no_rules_is_quiet(env):
    from workspaces.guardian_watch import watch_tick
    r = watch_tick(env["ws"], [], guardian_id="guardian-1",
                   log_root=env["lr"], now=NOW + 1)
    assert r["ok"] is True and r["findings"] == [] and r["actions"] == []


def test_actions_go_through_guardian_act_with_guardian_as_actor(env):
    from workspaces.guardian_watch import WatchRule, watch_tick
    _emit(env, "agent-x", 5)
    watch_tick(env["ws"], [WatchRule(kind="budget", limit=1)],
               guardian_id="guardian-1", log_root=env["lr"], now=NOW + 1)
    log = MutationLog(env["ws"], log_root=env["lr"])
    stamps = [e for e in log.replay()
              if (e.extra or {}).get("kind") == "PartyStatus"
              and (e.extra or {}).get("status") == "suspended"
              and e.actor == "guardian-1"]
    assert stamps, "the pause must be guardian_act's append, guardian-stamped"
