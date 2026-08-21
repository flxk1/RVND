# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Gateway wiring: pack-declared control forms get OPERATIONAL meaning in
the workflow runner — written before the logic (closes the decision
recorded at the pack-binding slice).

Rule: a step whose footprint the folder's PACK STACK governs with a form
demanding human hands (pre_approval / two_approvers / competent_approver)
proceeds only on a GRANTED approvals projection. This binds by ACTION
CLASS — pack demands only ever tighten, independent of the gate verdict.
The runner opens the request itself with the deterministic id
"<workflow>:step<i>" (re-runs resume the same request — the release model
is re-run-after-decision); denied (incl. timeout-deny) blocks the step;
pending holds the run. Folders without pack demands keep the exact legacy
behaviour. Every artefact is a chain event.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from rvnd.parties import register_party

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    lr = str(tmp_path / "logs")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", lr)
    ws = tmp_path / "org"
    ws.mkdir()
    for pid in ("anna", "ben"):
        register_party(str(ws), pid, "human",
                       competences=["personal-data"], log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _setup(env, *, packs):
    from rvnd.juris_packs import set_folder_packs
    from rvnd.workflows import Workflow, WorkflowStep, define_workflow
    if packs:
        set_folder_packs(env["ws"], packs, log_root=env["lr"])
    wf = Workflow(name="wf", steps=[
        WorkflowStep(skill_id="noop-skill", query="q",
                     footprint=("personal-data",))])
    define_workflow(env["ws"], wf, log_root=Path(env["lr"]))


def _run(env, approvals=None):
    from rvnd.workflows import run_workflow
    return run_workflow(env["ws"], "wf", actor="agent-x",
                        log_root=Path(env["lr"]),
                        step_approvals=approvals)


def _events(env, kind):
    from rvnd.mutation_log import MutationLog
    log = MutationLog(env["ws"], log_root=env["lr"])
    return [e.extra for e in log.replay()
            if (e.extra or {}).get("kind") == kind]


# --- the form gate ------------------------------------------------------------

def test_governed_step_opens_request_and_holds(env):
    _setup(env, packs=["eu-base", "de-overlay"])
    r = _run(env, approvals={0: "looks fine"})   # rationale alone ≠ enough
    assert r["final_state"] == "held"
    assert r["held"]["kind"] == "form-approval-pending"
    reqs = _events(env, "ApprovalRequested")
    assert len(reqs) == 1 and reqs[0]["request_id"] == "wf:step0"
    assert reqs[0]["competence"] == "personal-data"


def test_competent_grant_releases_the_step(env):
    from rvnd.approvals import decide_approval
    _setup(env, packs=["eu-base", "de-overlay"])
    _run(env)
    decide_approval(env["ws"], "wf:step0", "approve", actor="anna",
                    now=time.time(), log_root=env["lr"])
    r = _run(env)
    assert r["final_state"] != "held"
    rel = [e for e in _events(env, None) if False] or \
        [s for s in r["steps"] if s.get("state") != "step-held"]
    assert rel, "step must proceed past the form gate"


def test_denied_request_blocks_the_step(env):
    from rvnd.approvals import decide_approval
    _setup(env, packs=["eu-base", "de-overlay"])
    _run(env)
    decide_approval(env["ws"], "wf:step0", "deny", actor="ben",
                    now=time.time(), log_root=env["lr"])
    r = _run(env)
    assert r["final_state"] == "failed"
    blocked = [s for s in r["steps"] if s.get("state") == "step-blocked"]
    assert blocked and "denied" in blocked[0]["error"]


def test_rerun_reuses_the_same_request(env):
    _setup(env, packs=["eu-base", "de-overlay"])
    _run(env)
    _run(env)
    assert len(_events(env, "ApprovalRequested")) == 1


def test_noncompetent_hand_does_not_release(env):
    from rvnd.approvals import decide_approval
    from rvnd.parties import register_party as _reg
    _reg(env["ws"], "carl", "human", competences=["finance"],
         log_root=env["lr"])
    _setup(env, packs=["eu-base", "de-overlay"])
    _run(env)
    decide_approval(env["ws"], "wf:step0", "approve", actor="carl",
                    now=time.time(), log_root=env["lr"])
    r = _run(env)
    assert r["final_state"] == "held"        # carl lacks the competence


# --- back-compat ---------------------------------------------------------------

def test_step_without_pack_demand_keeps_legacy_behaviour(env):
    _setup(env, packs=None)
    r = _run(env)
    assert _events(env, "ApprovalRequested") == []
    assert r["final_state"] != "held" or \
        r["held"]["kind"] != "form-approval-pending"
