# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Phase-4 guardrails the strict review flagged as loose ends.

1. ElapsedProceed audit event: resolve_approval is a pure projection (no writes); the
   effecting entry point effect_approval() records a distinct, idempotent ElapsedProceed
   when a deadline elapses into a fail-open `proceed` grant — the trail must show that no
   person decided.
2. Law-basis force-halt as DEFENSE IN DEPTH: the pure bridge (reservation_to_request)
   downgrades proceed->halt for a basis_kind=="law" reservation, so the guarantee holds on
   every path into the bridge, not only the patchbay's requestSignoff.
"""
from __future__ import annotations

import os

import pytest

from rvnd import approvals as AP
from rvnd import reservation_bridge as B
from rvnd.mutation_log import MutationLog
from rvnd.parties import register_party

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
T = 1_900_000_000.0


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"; ws.mkdir(); lr = str(tmp_path / "log")
    register_party(str(ws), "h1", "human", competences=["legal"], log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _count_elapsed(env, rid):
    return sum(1 for e in MutationLog(env["ws"], log_root=env["lr"]).replay()
              if (e.extra or {}).get("kind") == "ElapsedProceed"
              and (e.extra or {}).get("request_id") == rid)


def test_effect_records_elapsed_proceed_once(env):
    AP.request_approval(env["ws"], "p", form="single_approver", competence="legal",
                        on_elapse="proceed", timeout_seconds=100, requester="agent",
                        now=T, log_root=env["lr"])
    # before the deadline: pending, no event
    assert AP.effect_approval(env["ws"], "p", now=T + 50, log_root=env["lr"])["state"] == "pending"
    assert _count_elapsed(env, "p") == 0
    # after: granted by elapse, exactly one ElapsedProceed recorded
    r = AP.effect_approval(env["ws"], "p", now=T + 200, log_root=env["lr"])
    assert r["state"] == "granted" and r["reason"] == "elapsed-proceed"
    assert r.get("recorded") == "ElapsedProceed"
    assert _count_elapsed(env, "p") == 1
    # idempotent: effecting again records no second event
    AP.effect_approval(env["ws"], "p", now=T + 300, log_root=env["lr"])
    assert _count_elapsed(env, "p") == 1


def test_halt_elapse_records_nothing(env):
    AP.request_approval(env["ws"], "h", form="single_approver", competence="legal",
                        on_elapse="halt", timeout_seconds=100, requester="agent",
                        now=T, log_root=env["lr"])
    r = AP.effect_approval(env["ws"], "h", now=T + 200, log_root=env["lr"])
    assert r["state"] == "denied" and r["reason"] == "timeout"
    assert _count_elapsed(env, "h") == 0


def test_human_grant_records_no_elapsed_event(env):
    AP.request_approval(env["ws"], "g", form="single_approver", competence="legal",
                        on_elapse="proceed", timeout_seconds=10000, requester="agent",
                        now=T, log_root=env["lr"])
    AP.decide_approval(env["ws"], "g", "approve", actor="h1", now=T + 1, log_root=env["lr"])
    r = AP.effect_approval(env["ws"], "g", now=T + 2, log_root=env["lr"])
    assert r["state"] == "granted" and r.get("reason") != "elapsed-proceed"
    assert _count_elapsed(env, "g") == 0


def test_bridge_forces_halt_for_law_basis():
    # a law-basis reservation authored ': proceed' is downgraded to halt (fail-closed)
    spec = B.reservation_to_request(
        {"kind": "loans", "by": "legal", "duration": "7d", "on_elapse": "proceed",
         "basis_kind": "law"})
    assert spec["on_elapse"] == "halt"


def test_bridge_keeps_proceed_for_policy_basis():
    spec = B.reservation_to_request(
        {"kind": "memos", "by": "legal", "duration": "3d", "on_elapse": "proceed",
         "basis_kind": "policy"})
    assert spec["on_elapse"] == "proceed"
    spec2 = B.reservation_to_request(
        {"kind": "memos", "by": "legal", "duration": "3d", "on_elapse": "proceed"})
    assert spec2["on_elapse"] == "proceed"   # no basis ⇒ honoured as authored
