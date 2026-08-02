# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Guardian watchdog, QUEUE half — written before the logic.

Two rules over the run queue (concept § 1.1: "subscribes to the live audit
stream AND THE RUN QUEUE"):

- ``queue_stuck``  — delegates detection to ``queue.inspect_stuck_runs``
  (never re-implemented) and ESCALATES ONLY: resume / mark-failed are the
  human's documented choices on the crash-resume panel, so a stuck rule
  asking for pause is refused like drift+pause.
- ``queue_flood``  — an agent with more than ``limit`` non-terminal queue
  entries is paused (the § 1.1 budget case at the queue). Humans are out
  of scope; the entry's ``enqueued_by`` is the attribution.

The watchdog still only ever acts through ``guardian_act``; it never
resumes, cancels, or marks runs — the queue mutations stay human.
"""
from __future__ import annotations

import os
import time

import pytest

from workspaces.guardian import GuardianRefused
from workspaces.mutation_log import MutationLog
from workspaces.parties import list_parties, register_party

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    lr = str(tmp_path / "logs")
    register_party(str(ws), "alex", "human", competences=["ops"],
                   log_root=lr)
    register_party(str(ws), "agent-x", "agent", owner="alex",
                   purpose="ingest", grade="L2", log_root=lr)
    register_party(str(ws), "guardian-1", "agent", owner="alex",
                   purpose="watchdog", grade="L1", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _enqueue(env, n, by="agent-x"):
    from pathlib import Path

    from workspaces.queue import enqueue_run
    for i in range(n):
        enqueue_run(env["ws"], f"wf-{by}-{i}", enqueued_by=by,
                    log_root=Path(env["lr"]))


def _status(env, pid):
    rows = list_parties(env["ws"], log_root=env["lr"])["parties"]
    return {r["party_id"]: r["status"] for r in rows}[pid]


def _chain_kinds(env):
    log = MutationLog(env["ws"], log_root=env["lr"])
    return [(e.extra or {}).get("kind") for e in log.replay()]


def _tick(env, rules, now=None):
    from workspaces.guardian_watch import watch_tick
    return watch_tick(env["ws"], rules, guardian_id="guardian-1",
                      log_root=env["lr"], now=now)


# --- queue_stuck: detect via the existing primitive, escalate only -----------

def test_pending_stale_run_escalates_and_is_not_mutated(env):
    from pathlib import Path

    from workspaces.guardian_watch import WatchRule
    from workspaces.queue import list_queue
    _enqueue(env, 1)
    # inspect_stuck_runs floors BOTH timestamps to whole seconds, so the
    # boundary has up to 2s of slack — sleep past the worst case (the 300s
    # production threshold makes that granularity irrelevant there).
    time.sleep(2.2)
    r = _tick(env, [WatchRule(kind="queue_stuck", window_seconds=1)])
    f = [x for x in r["findings"] if x["rule"] == "queue_stuck"]
    assert f and f[0]["action"] == "escalate"
    assert "GuardianEscalation" in _chain_kinds(env)
    entries = list_queue(log_root=Path(env["lr"]))
    assert all(e.state == "pending" for e in entries), \
        "the watchdog must never resume/cancel/mark a run"


def test_fresh_pending_run_is_not_stuck(env):
    from workspaces.guardian_watch import WatchRule
    _enqueue(env, 1)
    r = _tick(env, [WatchRule(kind="queue_stuck", window_seconds=300)])
    assert [x for x in r["findings"] if x["rule"] == "queue_stuck"] == []


def test_queue_stuck_with_pause_is_refused(env):
    from workspaces.guardian_watch import WatchRule
    with pytest.raises(GuardianRefused):
        _tick(env, [WatchRule(kind="queue_stuck", window_seconds=1,
                              action="pause")])
    assert "GuardianRefused" in _chain_kinds(env)


# --- queue_flood: budget at the queue, attributed via enqueued_by -------------

def test_agent_flooding_the_queue_is_paused(env):
    from workspaces.guardian_watch import WatchRule
    _enqueue(env, 3, by="agent-x")
    r = _tick(env, [WatchRule(kind="queue_flood", limit=2)])
    f = [x for x in r["findings"] if x["rule"] == "queue_flood"]
    assert f and f[0]["party_id"] == "agent-x" and f[0]["count"] == 3
    assert _status(env, "agent-x") == "suspended"


def test_flood_under_limit_is_quiet(env):
    from workspaces.guardian_watch import WatchRule
    _enqueue(env, 2, by="agent-x")
    r = _tick(env, [WatchRule(kind="queue_flood", limit=2)])
    assert [x for x in r["findings"] if x["rule"] == "queue_flood"] == []
    assert _status(env, "agent-x") == "active"


def test_human_enqueuer_never_flagged(env):
    from workspaces.guardian_watch import WatchRule
    _enqueue(env, 4, by="alex")
    r = _tick(env, [WatchRule(kind="queue_flood", limit=1)])
    assert [x for x in r["findings"] if x["rule"] == "queue_flood"] == []
    assert _status(env, "alex") == "active"


def test_empty_queue_is_quiet(env):
    from workspaces.guardian_watch import WatchRule
    r = _tick(env, [WatchRule(kind="queue_stuck", window_seconds=1),
                    WatchRule(kind="queue_flood", limit=1)])
    assert r["findings"] == [] and r["ok"] is True
