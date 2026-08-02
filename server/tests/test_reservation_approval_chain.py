# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The Loomground reservation → live approval handoff.

`test_reservation_approval_bridge.py` pins the PURE mapping. This pins the WIRED chain:
`request_from_reservation` opens a real signed request, and `resolve_approval` enforces
the m-of-n quorum (two distinct competent hands), timeout-is-deny (`halt`), and the opt-in
fail-open `proceed`.
"""
from __future__ import annotations

import os

import pytest

from workspaces.approvals import (
    decide_approval, request_from_reservation, resolve_approval)
from workspaces.parties import register_party

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

T0 = 1_900_000_000.0
DAY = 86400.0


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    lr = str(tmp_path / "logs")
    for pid, comp in (("lara", ["legal"]), ("finn", ["finance"]),
                      ("rita", ["risk"]), ("mara", ["marketing"])):
        register_party(str(ws), pid, "human", name=pid.title(),
                       competences=comp, log_root=lr)
    register_party(str(ws), "agent-x", "agent", owner="lara",
                   purpose="propose", grade="L2", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _open(env, reservation, rid="r1", requester="agent-x"):
    return request_from_reservation(env["ws"], rid, reservation,
                                    requester=requester, now=T0, log_root=env["lr"])


def _decide(env, rid, decision, actor, now=T0 + 10):
    return decide_approval(env["ws"], rid, decision, actor=actor, now=now,
                           log_root=env["lr"])


def _resolve(env, rid, now):
    return resolve_approval(env["ws"], rid, now=now, log_root=env["lr"])


QUORUM = {"kind": "loans", "by": "2 of { legal, finance, risk }"}


def test_quorum_needs_two_distinct_competent_hands(env):
    _open(env, QUORUM)
    assert _resolve(env, "r1", now=T0 + 60)["needed"] == 2
    _decide(env, "r1", "approve", "lara")                 # legal
    assert _resolve(env, "r1", now=T0 + 60)["state"] == "pending"
    _decide(env, "r1", "approve", "finn")                 # finance — second distinct
    r = _resolve(env, "r1", now=T0 + 60)
    assert r["state"] == "granted" and set(r["approvers"]) == {"lara", "finn"}


def test_one_hand_cannot_fill_two_slots(env):
    _open(env, QUORUM)
    _decide(env, "r1", "approve", "lara", now=T0 + 10)
    _decide(env, "r1", "approve", "lara", now=T0 + 20)    # same hand again
    assert _resolve(env, "r1", now=T0 + 60)["state"] == "pending"


def test_out_of_set_competence_does_not_count(env):
    _open(env, QUORUM)
    _decide(env, "r1", "approve", "lara")                 # counts (legal)
    _decide(env, "r1", "approve", "mara")                 # marketing ∉ {legal,finance,risk}
    assert _resolve(env, "r1", now=T0 + 60)["state"] == "pending"


def test_requester_hand_never_counts(env):
    # an agent can't sign anyway, but the requester exclusion is the invariant
    _open(env, {"kind": "loans", "by": "legal"}, requester="lara")
    _decide(env, "r1", "approve", "lara")                 # lara is the requester here
    assert _resolve(env, "r1", now=T0 + 60)["state"] == "pending"


def test_halt_denies_on_elapse(env):
    _open(env, {"kind": "loans", "by": "legal", "duration": "30d", "on_elapse": "halt"})
    assert _resolve(env, "r1", now=T0 + DAY)["state"] == "pending"
    r = _resolve(env, "r1", now=T0 + 31 * DAY)
    assert r["state"] == "denied" and r["reason"] == "timeout"


def test_proceed_grants_on_elapse(env):
    _open(env, {"kind": "summary", "by": "legal", "duration": "3d", "on_elapse": "proceed"})
    assert _resolve(env, "r1", now=T0 + DAY)["state"] == "pending"     # before the deadline
    r = _resolve(env, "r1", now=T0 + 4 * DAY)                          # after, no sign-off
    assert r["state"] == "granted" and r["reason"] == "elapsed-proceed"


def test_duration_sets_the_deadline_from_the_token(env):
    _open(env, {"kind": "loans", "by": "legal", "duration": "2h"})
    assert _resolve(env, "r1", now=T0 + 3600)["deadline"] == T0 + 7200


def test_inbox_lists_resolved_requests_with_meter_data(env):
    from workspaces.approvals import list_approvals
    _open(env, QUORUM, rid="r1")
    _open(env, {"kind": "exports", "by": "legal", "duration": "30d", "on_elapse": "halt"}, rid="r2")
    inbox = list_approvals(env["ws"], now=T0 + 60, log_root=env["lr"])["approvals"]
    assert {a["request_id"] for a in inbox} == {"r1", "r2"}
    r1 = next(a for a in inbox if a["request_id"] == "r1")
    # the role-based quorum surfaces its meter (needed + the role set), no identities
    assert r1["needed"] == 2 and set(r1["competences"]) == {"legal", "finance", "risk"}
    assert r1["state"] == "pending"
    r2 = next(a for a in inbox if a["request_id"] == "r2")
    assert r2["on_elapse"] == "halt"
    # state filter
    pend = list_approvals(env["ws"], now=T0 + 60, state="pending", log_root=env["lr"])["approvals"]
    assert len(pend) == 2
