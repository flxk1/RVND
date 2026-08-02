# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Operations journalling + live hardening — the binding that
turns the operations core from a calculator into a real audited run.

Two builds:
  * journal — operate writes signed chain events (RunStarted, one RunStep per
    issue with its disposition, RunOutcome; a refused run writes RunRefused).
    So a run is auditable and replayable, and a refusal (agent not permitted)
    is itself a recorded security event. Auto steps are journalled but do NOT
    become human-closed evidence — the calibration separation is preserved.
  * live hardening — hardening_inputs reads the contract's precedent FROM the
    case memory (human-closed cases for the fingerprint) and the calibration
    ledger (disagreement rate), so autonomy is earned from real history, not
    passed in by hand.

Claims under test (written BEFORE the logic):
  J1  operate journals RunStarted + a RunStep per issue + RunOutcome; the run
      has a run_id and each step a receipt
  J2  runs_for projects the run with its steps
  J3  a refused run (agent not permitted) is journalled as RunRefused
  J4  journal=False writes nothing (pure planning)
  J5  auto steps journal but create NO solves-edge (no false evidence)
  L1  hardening_inputs: more human-closed cases for the fingerprint → higher
      prior_approvals
  L2  hardening_inputs: calibration disagreement → higher disagreement_rate
"""
from __future__ import annotations

import os

import pytest

from workspaces.operations import operate, runs_for, hardening_inputs
from workspaces.use_case import register_use_case
from workspaces.case_index import record_case, solves_edges
from workspaces.calibration import log_reuse, judge_sample
from workspaces.parties import register_party

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    lr = str(tmp_path / "logs")
    register_party(str(ws), "alex", "human", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _fp(itype="liability_cap"):
    return {"issue_type": itype, "profile": "legal-de", "rooms": ["§ 309"]}


def _reg(env, *, risk="low", approvals=20, agents=("bot-7",)):
    register_use_case(env["ws"], use_case_id="uc1", name="uc1",
                      fingerprint=_fp(), risk=risk, allowed_agents=list(agents),
                      actor="alex", prior_approvals=approvals,
                      override_window_seconds=60, log_root=env["lr"])


def _issues():
    return [{"issue_id": "i1", "issue_type": "formatting_fix",
             "completeness": "high"},
            {"issue_id": "i2", "issue_type": "data_processing",
             "completeness": "low"}]


def test_run_is_journalled(env):                                  # J1
    _reg(env, risk="low", approvals=20)
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert run["run_id"]
    assert all(s["receipt"] for s in run["steps"])


def test_runs_for_projects_the_run(env):                          # J2
    _reg(env)
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=_issues(), now_epoch=1000, log_root=env["lr"])
    got = runs_for(env["ws"], log_root=env["lr"])
    assert len(got) == 1
    assert got[0]["run_id"] == run["run_id"]
    assert len(got[0]["steps"]) == 2
    assert got[0]["final"] == run["final"]


def test_refused_run_is_journalled(env):                          # J3
    _reg(env, agents=("bot-7",))
    run = operate(env["ws"], use_case_id="uc1", agent_id="intruder",
                  issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert run["final"] == "refused"
    got = runs_for(env["ws"], log_root=env["lr"])
    assert got and got[0]["final"] == "refused"
    assert got[0]["reason"] == "agent not permitted"


def test_journal_false_writes_nothing(env):                       # J4
    _reg(env)
    operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
            issues=_issues(), now_epoch=1000, journal=False,
            log_root=env["lr"])
    assert runs_for(env["ws"], log_root=env["lr"]) == []


def test_auto_step_makes_no_false_evidence(env):                  # J5
    _reg(env, risk="low", approvals=20)             # L4 → formatting auto
    operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
            issues=[{"issue_id": "i1", "issue_type": "formatting_fix",
                     "completeness": "high"}],
            now_epoch=1000, log_root=env["lr"])
    # an auto run does NOT create a human-closed solves-edge
    assert solves_edges(env["ws"], log_root=env["lr"]) == []


def test_hardening_from_case_memory(env):                         # L1
    base = hardening_inputs(env["ws"], _fp(), log_root=env["lr"])
    assert base["prior_approvals"] == 0
    for _ in range(3):
        record_case(env["ws"], {"case": {"problem": {"text": "q"}, "grounds": [],
                    "chain": [], "gaps": [], "resolution": {"type": "residual"},
                    "profile": "legal-de", "facts": [], "actions": [],
                    "contract": {}, "waivers": []},
                    "inputs": {"question": "q", "rooms": ["§ 309"],
                               "profile": "legal-de"}},
                    actor="alex", outcome="ratified", solver="skill:liab",
                    log_root=env["lr"])
    after = hardening_inputs(env["ws"], _fp(), log_root=env["lr"])
    assert after["prior_approvals"] >= 3


def test_hardening_reads_calibration_disagreement(env):           # L2
    rid = log_reuse(env["ws"], fingerprint=_fp(), solver="s",
                    log_root=env["lr"])
    judge_sample(env["ws"], reuse_id=rid, actor="alex", agreed=False,
                 rationale="drifted", log_root=env["lr"])
    h = hardening_inputs(env["ws"], _fp(), log_root=env["lr"])
    assert h["disagreement_rate"] > 0.0
