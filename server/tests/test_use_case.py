# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Use-case registry — the identity hub. Every governed use case
gets a stable use_case_id and binds together the entities that were already
addressable on their own: the problem fingerprint, the step contract (now with
a contract_id), the allowed agents (party_ids), the reserved human acts, and
the risk. This is the join key that makes governance traceable end to end:
agent -> use case -> contract -> problem -> case -> override, all by ID.

Claims under test (written BEFORE the logic):
  U1  register -> get returns the bound record under a stable use_case_id
  U2  the record binds fingerprint + contract(+contract_id) + allowed_agents
      + reserved_acts + risk
  U3  re-registering the same id appends a new version (append-only,
      latest-wins) — never edits
  U4  contract_id is derived from contract content: same contract -> same id;
      change risk -> different id
  U5  list returns every registered use case
  U6  agent_permitted joins agent <-> use case: an agent not in allowed_agents
      is rejected
  U7  fail-closed: register needs actor + name + a valid risk
  U8  deterministic projection
"""
from __future__ import annotations

import os

import pytest

from workspaces.use_case import (
    agent_permitted, contract_id_for, get_use_case, list_use_cases,
    register_use_case,
)

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def _fp(itype="data_transfer"):
    return {"issue_type": itype, "profile": "legal-de", "rooms": ["Art. 46"]}


def test_register_and_get(env):                                   # U1 + U2
    # G2 (2026-06-25): reservations now authored/ingested, not from a legal enum.
    # The fingerprint no longer auto-derives a reserved act; the reservation under
    # test is AUTHORED via policy_reservations and must appear in reserved_acts.
    uid = register_use_case(
        env["ws"], use_case_id="uc-transfer", name="EU->US transfer review",
        fingerprint=_fp(), risk="high", allowed_agents=["bot-7"],
        actor="alex", prior_approvals=5,
        policy_reservations={"uc-transfer": {
            "reserved_to": "controller-representative", "act_type": "sign",
            "source": "company policy — transfer sign-off"}},
        log_root=env["lr"])
    assert uid == "uc-transfer"
    rec = get_use_case(env["ws"], "uc-transfer", log_root=env["lr"])
    assert rec["fingerprint"] == _fp()
    assert rec["risk"] == "high"
    assert rec["allowed_agents"] == ["bot-7"]
    assert rec["contract"]["grade"] >= 0
    assert rec["contract_id"]
    # the AUTHORED reservation is bound onto the record (was the Art.24 enum act)
    sign = [a for a in rec["reserved_acts"] if a["act_type"] == "sign"]
    assert sign and sign[0]["reserved_to"] == "controller-representative"
    assert sign[0]["basis_kind"] == "policy"


def test_reregister_appends_new_version(env):                     # U3
    register_use_case(env["ws"], use_case_id="uc1", name="v1",
                      fingerprint=_fp(), risk="high", allowed_agents=[],
                      actor="alex", log_root=env["lr"])
    register_use_case(env["ws"], use_case_id="uc1", name="v2",
                      fingerprint=_fp(), risk="low", allowed_agents=["a"],
                      actor="alex", log_root=env["lr"])
    rec = get_use_case(env["ws"], "uc1", log_root=env["lr"])
    assert rec["name"] == "v2" and rec["risk"] == "low"   # latest wins
    # both versions are on the chain (append-only) — count distinct ids is 1
    assert len(list_use_cases(env["ws"], log_root=env["lr"])) == 1


def test_contract_id_tracks_content():                            # U4
    a = contract_id_for("high", prior_approvals=5)
    b = contract_id_for("high", prior_approvals=5)
    c = contract_id_for("low", prior_approvals=5)
    assert a == b and a != c


def test_list_all(env):                                           # U5
    register_use_case(env["ws"], use_case_id="uc1", name="a",
                      fingerprint=_fp(), risk="low", allowed_agents=[],
                      actor="alex", log_root=env["lr"])
    register_use_case(env["ws"], use_case_id="uc2", name="b",
                      fingerprint=_fp("co_determination"), risk="medium",
                      allowed_agents=[], actor="alex", log_root=env["lr"])
    ids = {u["use_case_id"] for u in list_use_cases(env["ws"], log_root=env["lr"])}
    assert ids == {"uc1", "uc2"}


def test_agent_permitted_join(env):                               # U6
    register_use_case(env["ws"], use_case_id="uc1", name="a",
                      fingerprint=_fp(), risk="low", allowed_agents=["bot-7"],
                      actor="alex", log_root=env["lr"])
    assert agent_permitted(env["ws"], "uc1", "bot-7", log_root=env["lr"]) is True
    assert agent_permitted(env["ws"], "uc1", "bot-9", log_root=env["lr"]) is False


def test_failclosed(env):                                         # U7
    with pytest.raises(ValueError):
        register_use_case(env["ws"], use_case_id="x", name="",
                          fingerprint=_fp(), risk="low", allowed_agents=[],
                          actor="alex", log_root=env["lr"])
    with pytest.raises(ValueError):
        register_use_case(env["ws"], use_case_id="x", name="ok",
                          fingerprint=_fp(), risk="nope", allowed_agents=[],
                          actor="alex", log_root=env["lr"])
    with pytest.raises(ValueError):
        register_use_case(env["ws"], use_case_id="x", name="ok",
                          fingerprint=_fp(), risk="low", allowed_agents=[],
                          actor="", log_root=env["lr"])


def test_deterministic_projection(env):                           # U8
    register_use_case(env["ws"], use_case_id="uc1", name="a",
                      fingerprint=_fp(), risk="low", allowed_agents=[],
                      actor="alex", log_root=env["lr"])
    a = get_use_case(env["ws"], "uc1", log_root=env["lr"])
    b = get_use_case(env["ws"], "uc1", log_root=env["lr"])
    assert a == b
