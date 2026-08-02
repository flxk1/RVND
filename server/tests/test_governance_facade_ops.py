# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Facade ops for the party registry + approval semantics — written before
the wiring. § 1.6 budget honoured: ops on EXISTING facades, no new tool
names (the surface stays 23; test_mcp_facades pins that separately).

Placement decision (recorded): parties are governance declarations →
`workspace_policy` (beside juris_packs / tdm / oversight); approvals govern the
action/run lifecycle → `workspace_workflow`. Every op routes to the tested
module functions — the facade adds reachability, never new semantics.
"""
from __future__ import annotations

import os

import pytest

mcp_server = pytest.importorskip("workspaces.mcp_server")

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

T0 = 1_900_000_000.0


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    ws = tmp_path / "org"
    ws.mkdir()
    return str(ws)


def _pol(op, p):
    return mcp_server.workspace_policy(op, p)


def _wf(op, p):
    return mcp_server.workspace_workflow(op, p)


# --- party ops on workspace_policy --------------------------------------------------

def test_party_register_list_route_round_trip(env):
    r = _pol("party_register", {"folder_context": env, "party_id": "anna",
                                "kind": "human", "name": "Anna",
                                "competences": ["data-protection"],
                                "actor": "alex"})
    assert r["ok"] is True
    _pol("party_register", {"folder_context": env, "party_id": "agent-x",
                            "kind": "agent", "owner": "anna",
                            "grade": "L2", "actor": "alex"})
    ls = _pol("party_list", {"folder_context": env})
    assert {p["party_id"] for p in ls["parties"]} == {"anna", "agent-x"}
    rt = _pol("party_route", {"folder_context": env,
                              "competence": "data-protection"})
    assert [a["party_id"] for a in rt["approvers"]] == ["anna"]


def test_party_status_kill_switch_via_facade(env):
    _pol("party_register", {"folder_context": env, "party_id": "agent-x",
                            "kind": "agent", "actor": "alex"})
    r = _pol("party_status", {"folder_context": env, "party_id": "agent-x",
                              "status": "killed", "reason": "root key",
                              "actor": "alex"})
    assert r["ok"] is True and r["status"] == "killed"
    ls = _pol("party_list", {"folder_context": env, "kind": "agent"})
    assert ls["parties"][0]["status"] == "killed"


def test_actor_stamps_via_facade(env):
    _pol("party_register", {"folder_context": env, "party_id": "alex",
                            "kind": "human", "actor": "alex"})
    r = _pol("actor_stamps", {"folder_context": env})
    assert r["ok"] is True and r["total"] >= 1 and r["unstamped"] == 0


def test_party_bad_kind_is_clean_error(env):
    r = _pol("party_register", {"folder_context": env, "party_id": "x",
                                "kind": "robot"})
    assert "error" in r


# --- approval ops on workspace_workflow ----------------------------------------------

def _seed_parties(env):
    for pid, comp in (("anna", ["data-protection"]),
                      ("ben", ["data-protection"])):
        _pol("party_register", {"folder_context": env, "party_id": pid,
                                "kind": "human", "competences": comp,
                                "actor": "alex"})


def test_approval_lifecycle_via_facade(env):
    _seed_parties(env)
    r = _wf("approval_request", {"folder_context": env, "request_id": "r1",
                                 "form": "four_eyes",
                                 "competence": "data-protection",
                                 "requester": "agent-x",
                                 "timeout_seconds": 3600, "now": T0})
    assert r["ok"] is True
    _wf("approval_decide", {"folder_context": env, "request_id": "r1",
                            "decision": "approve", "actor": "anna",
                            "now": T0 + 10})
    mid = _wf("approval_resolve", {"folder_context": env, "request_id": "r1",
                                   "now": T0 + 20})
    assert mid["state"] == "pending"          # four eyes: one hand so far
    _wf("approval_decide", {"folder_context": env, "request_id": "r1",
                            "decision": "approve", "actor": "ben",
                            "now": T0 + 30})
    done = _wf("approval_resolve", {"folder_context": env, "request_id": "r1",
                                    "now": T0 + 40})
    assert done["state"] == "granted" and done["approvers"] == ["anna", "ben"]


def test_approval_timeout_denies_via_facade(env):
    _seed_parties(env)
    _wf("approval_request", {"folder_context": env, "request_id": "r2",
                             "form": "single_approver",
                             "competence": "data-protection",
                             "requester": "agent-x",
                             "timeout_seconds": 60, "now": T0})
    r = _wf("approval_resolve", {"folder_context": env, "request_id": "r2",
                                 "now": T0 + 61})
    assert r["state"] == "denied" and r["reason"] == "timeout"


def test_approval_delegate_via_facade(env):
    _seed_parties(env)
    _pol("party_register", {"folder_context": env, "party_id": "carl",
                            "kind": "human", "competences": ["finance"],
                            "actor": "alex"})
    d = _wf("approval_delegate", {"folder_context": env,
                                  "competence": "data-protection",
                                  "from_party": "anna", "to_party": "carl",
                                  "actor": "anna", "now": T0})
    assert d["ok"] is True
    _wf("approval_request", {"folder_context": env, "request_id": "r3",
                             "form": "expert_review",
                             "competence": "data-protection",
                             "requester": "agent-x",
                             "timeout_seconds": 3600, "now": T0})
    _wf("approval_decide", {"folder_context": env, "request_id": "r3",
                            "decision": "approve", "actor": "carl",
                            "now": T0 + 5})
    r = _wf("approval_resolve", {"folder_context": env, "request_id": "r3",
                                 "now": T0 + 10})
    assert r["state"] == "granted"


def test_approval_unknown_request_is_clean_error(env):
    r = _wf("approval_resolve", {"folder_context": env,
                                 "request_id": "ghost", "now": T0})
    assert "error" in r


# --- help parity ---------------------------------------------------------------

def test_new_ops_self_describe():
    pol_ops = {o["op"] for o in mcp_server.workspace_policy("help")["ops"]}
    assert {"party_register", "party_status", "party_list", "party_route",
            "actor_stamps"} <= pol_ops
    wf_ops = {o["op"] for o in mcp_server.workspace_workflow("help")["ops"]}
    assert {"approval_request", "approval_decide", "approval_delegate",
            "approval_resolve"} <= wf_ops
