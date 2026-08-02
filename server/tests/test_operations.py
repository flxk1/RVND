# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Operations core — the governed step executor that drives one
use case through the solver, composing the session's primitives into one
deterministic decision. It does NOT re-implement the queue/worker (those
exist); it is the governed core the worker calls per unit.

Per detected issue it decides a disposition — auto / human / reserved /
refused — by composing: the use case's contract (earned autonomy grade), the
completeness band (confidence), and the reservations (law/policy). The key
governance property: a confident node auto-runs ONLY if the contract grants
enough autonomy — earned by precedent — so a brand-new use case sends
everything to humans, a hardened one auto-runs the confident nodes. Human
nodes carry a timed-override deadline; the contract's on_timeout resolves
silence by risk. Every step threads the ids (use_case_id, contract_id) — the
join.

Claims under test (written BEFORE the logic):
  O1  operate returns one step per issue, each with a disposition + the ids
  O2  confident node + high-autonomy contract → 'auto'
  O3  confident node + LOW-autonomy contract → 'human' (contract gate beats
      confidence — earned autonomy, not assumed)
  O4  low-completeness node → 'human' with deadline = now + override window
  O5  reserved issue → 'reserved', never auto whatever the grade
  O6  agent not permitted on the use case → whole run 'refused', no steps run
  O7  resolve_timeout: past deadline → proceed (low risk) / halt (high risk)
  O8  ids threaded on every step (the join)
  O9  deterministic
"""
from __future__ import annotations

import os

import pytest

from workspaces.operations import operate, resolve_timeout
from workspaces.parties import register_party, set_party_status
from workspaces.use_case import register_use_case

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def _fp(itype="liability_cap"):
    return {"issue_type": itype, "profile": "legal-de", "rooms": ["§ 309"]}


def _register(env, *, risk, approvals, agents=("bot-7",), window=120, uid="uc1"):
    register_use_case(env["ws"], use_case_id=uid, name=uid,
                      fingerprint=_fp(), risk=risk, allowed_agents=list(agents),
                      actor="alex", prior_approvals=approvals,
                      override_window_seconds=window, log_root=env["lr"])


def _issues():
    return [{"issue_id": "i1", "issue_type": "liability_cap",
             "completeness": "high"},
            {"issue_id": "i2", "issue_type": "data_processing",
             "completeness": "low"}]


def test_operate_returns_steps_with_ids(env):                     # O1 + O8
    _register(env, risk="low", approvals=20)
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert len(run["steps"]) == 2
    for s in run["steps"]:
        assert s["use_case_id"] == "uc1" and s["contract_id"]
        assert s["disposition"] in ("auto", "human", "reserved")


def test_confident_node_high_autonomy_runs_auto(env):             # O2
    _register(env, risk="low", approvals=20)        # → L4
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=[{"issue_id": "i1", "issue_type": "formatting_fix",
                           "completeness": "high"}],
                  now_epoch=1000, log_root=env["lr"])
    assert run["steps"][0]["disposition"] == "auto"


def test_confident_node_low_autonomy_goes_human(env):             # O3
    _register(env, risk="low", approvals=0)         # new → L0
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=[{"issue_id": "i1", "issue_type": "formatting_fix",
                           "completeness": "high"}],
                  now_epoch=1000, log_root=env["lr"])
    assert run["steps"][0]["disposition"] == "human"   # contract gate beats confidence


def test_missing_contract_grade_is_human_fail_closed(env, monkeypatch):  # O3b
    # Defensive: a (malformed/legacy) use case whose contract carries NO grade must
    # NOT auto-run — an unknown earned autonomy is ungraded, and ungraded never meets
    # the AUTO_GRADE_MIN threshold. Force the gap by doctoring the projection.
    import workspaces.operations as ops
    _register(env, risk="low", approvals=20)          # would be L4 → auto if grade kept
    real = ops.get_use_case(env["ws"], "uc1", log_root=env["lr"])
    real["contract"] = {k: v for k, v in (real.get("contract") or {}).items()
                        if k != "grade"}              # contract present, grade absent
    monkeypatch.setattr(ops, "get_use_case", lambda *a, **k: real)
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=[{"issue_id": "i1", "issue_type": "formatting_fix",
                           "completeness": "high"}],
                  now_epoch=1000, log_root=env["lr"])
    assert run["steps"][0]["disposition"] == "human"  # ungraded → fail-closed to human
    assert run["grade"] is None                       # surfaced as ungraded, not "L0"


def test_low_completeness_is_human_with_deadline(env):            # O4
    _register(env, risk="low", approvals=20, window=90)
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=[{"issue_id": "i2", "issue_type": "formatting_fix",
                           "completeness": "low"}],
                  now_epoch=1000, log_root=env["lr"])
    s = run["steps"][0]
    assert s["disposition"] == "human"
    assert s["deadline"] == 1090 and s["on_timeout"] == "proceed"


def test_reserved_issue_never_auto(env):                          # O5
    # G2 (2026-06-25): reservations now authored/ingested, not from a legal enum.
    # The "reserved never auto" property is the same — now driven by an AUTHORED
    # policy reservation (reserved_to works-council) instead of the co_determination
    # enum. Even at max autonomy, a use case carrying a reserved act reserves its run.
    register_use_case(env["ws"], use_case_id="uc1", name="uc1",
                      fingerprint=_fp(), risk="low",
                      allowed_agents=["bot-7"], actor="alex",
                      prior_approvals=1000,           # max autonomy
                      override_window_seconds=120,
                      policy_reservations={"uc1": {
                          "reserved_to": "works-council",
                          "act_type": "co-determine",
                          "source": "company policy — co-determination"}},
                      log_root=env["lr"])
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=[{"issue_id": "i1", "issue_type": "liability_cap",
                           "completeness": "high"}],
                  now_epoch=1000, log_root=env["lr"])
    s = run["steps"][0]
    assert s["disposition"] == "reserved"
    assert s["reserved_to"] == "works-council"      # routed to the competence


def test_agent_not_permitted_refuses_run(env):                   # O6
    _register(env, risk="low", approvals=20, agents=("bot-7",))
    run = operate(env["ws"], use_case_id="uc1", agent_id="intruder",
                  issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert run["final"] == "refused"
    assert run["steps"] == []


@pytest.mark.parametrize("status", ["suspended", "killed"])
def test_registered_inactive_agent_cannot_operate(env, status):
    _register(env, risk="low", approvals=20)
    register_party(
        env["ws"], "bot-7", "agent", actor="user", log_root=env["lr"]
    )
    set_party_status(
        env["ws"], "bot-7", status, actor="user", log_root=env["lr"]
    )

    run = operate(
        env["ws"], use_case_id="uc1", agent_id="bot-7",
        issues=_issues(), now_epoch=1000, log_root=env["lr"],
    )

    assert run["final"] == "refused"
    assert run["reason"] == f"agent is {status}"
    assert run["steps"] == []


def test_registered_active_agent_can_operate(env):
    _register(env, risk="low", approvals=20)
    register_party(
        env["ws"], "bot-7", "agent", actor="user", log_root=env["lr"]
    )

    run = operate(
        env["ws"], use_case_id="uc1", agent_id="bot-7",
        issues=_issues(), now_epoch=1000, log_root=env["lr"],
    )

    assert run["final"] != "refused"


def test_agent_registry_read_failure_refuses(env, monkeypatch):
    _register(env, risk="low", approvals=20)
    import workspaces.parties as parties

    def _unreadable(*args, **kwargs):
        raise OSError("registry unavailable")

    monkeypatch.setattr(parties, "list_parties", _unreadable)
    run = operate(
        env["ws"], use_case_id="uc1", agent_id="bot-7",
        issues=_issues(), now_epoch=1000, log_root=env["lr"],
    )

    assert run["final"] == "refused"
    assert run["reason"] == "agent status unavailable: OSError"


def test_resolve_timeout_by_risk():                               # O7
    low = {"deadline": 100, "on_timeout": "proceed"}
    high = {"deadline": 100, "on_timeout": "halt"}
    assert resolve_timeout(low, now_epoch=200) == "proceed"
    assert resolve_timeout(high, now_epoch=200) == "halt"
    assert resolve_timeout(low, now_epoch=50) == "pending"   # not yet due


def test_deterministic(env):                                     # O9
    _register(env, risk="medium", approvals=5)
    a = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                issues=_issues(), now_epoch=1000, log_root=env["lr"])
    b = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                issues=_issues(), now_epoch=1000, log_root=env["lr"])
    # disposition + ids are deterministic (ignore any per-call audit ids)
    assert [(_s["issue_id"], _s["disposition"]) for _s in a["steps"]] == \
           [(_s["issue_id"], _s["disposition"]) for _s in b["steps"]]
