# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-17: human oversight is EFFECTIVE in-flight (AI Act Art. 14).

The suite already proves the oversight dial *computes* the right level and
that a prohibited act is refused. What it did not prove is the Art. 14
property that matters most: that a human can actually INTERVENE and STOP an
operation that is already under way — not merely gate it at admission time.

These tests exercise the party kill-switch as a live control. An agent that
has demonstrably been operating is stopped by a human between steps: its next
governed step halts immediately, the intervention is on the signed audit
chain, and re-activation restores operation (the override is a two-way live
control, and it is auditable). The passive dead-man's switch (a lapsed
autonomy lease decaying to L0 with no human action) is covered separately by
test_breaker.py; this file is the ACTIVE human intervention.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rvnd.operations import operate
from rvnd.parties import register_party, set_party_status
from rvnd.use_case import register_use_case
from rvnd.mutation_log import MutationLog

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

pytestmark = pytest.mark.security  # governance-integrity


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def _register(env, *, risk="low", approvals=20):
    register_use_case(
        env["ws"], use_case_id="uc1", name="uc1",
        fingerprint={"issue_type": "liability_cap", "profile": "legal-de"},
        risk=risk, allowed_agents=["bot-7"], actor="alex",
        prior_approvals=approvals, override_window_seconds=120,
        log_root=env["lr"])


def _issues():
    return [{"issue_id": "i1", "issue_type": "liability_cap",
             "completeness": "high"}]


def _operate(env):
    return operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                   issues=_issues(), now_epoch=1000, log_root=env["lr"])


@pytest.mark.parametrize("stop_status", ["killed", "suspended"])
def test_human_stop_halts_the_next_step_in_flight(env, stop_status):
    """An agent runs, a human flips the kill-switch, the NEXT step halts.

    This is the distinction from the existing already-inactive test: here the
    agent was ACTIVE and produced real steps first, so the refusal proves the
    switch is a live mid-flight control, not just an admission gate at t=0."""
    _register(env)
    register_party(env["ws"], "bot-7", "agent", actor="user", log_root=env["lr"])

    # The agent is operating: a real run with steps.
    before = _operate(env)
    assert before["final"] != "refused", "agent should be operating before the stop"
    assert before["steps"], "the pre-stop run must have produced steps"

    # A human intervenes mid-task.
    set_party_status(env["ws"], "bot-7", stop_status, reason="operator halt",
                     actor="human-supervisor", log_root=env["lr"])

    # The very next governed step is refused — immediately, no steps run.
    after = _operate(env)
    assert after["final"] == "refused", "human stop did not halt the next step"
    assert after["reason"] == f"agent is {stop_status}"
    assert after["steps"] == [], "a stopped agent must run no further steps"


def test_the_intervention_is_on_the_signed_audit_chain(env):
    """The human's stop is not a silent flag — it is an appended, signed
    PartyStatus event, so the intervention itself is auditable (Art. 12/14)."""
    _register(env)
    register_party(env["ws"], "bot-7", "agent", actor="user", log_root=env["lr"])
    _operate(env)
    set_party_status(env["ws"], "bot-7", "killed", reason="operator halt",
                     actor="human-supervisor", log_root=env["lr"])

    log = MutationLog(env["ws"], log_root=env["lr"])
    events_file = Path(env["lr"]) / log.folder_id / "events.jsonl"
    stops = [
        json.loads(line)
        for line in events_file.read_text().splitlines() if line.strip()
        if json.loads(line).get("extra", {}).get("kind") == "PartyStatus"
        and json.loads(line).get("extra", {}).get("status") == "killed"
    ]
    assert stops, "the human stop left no PartyStatus event on the chain"
    assert stops[-1]["extra"]["party_id"] == "bot-7"
    assert log.verify_chain().ok, "chain broke after the intervention"


def test_reactivation_restores_operation(env):
    """The override is a live two-way control: a human can re-activate a
    suspended agent and it resumes.

    NOTE on 'killed': the status projection is latest-wins, so a later
    'active' event reactivates even a 'killed' agent — the kill-switch is
    currently REVERSIBLE, not terminal, despite the name. Whether a 'killed'
    agent should be permanently terminal is a governance design decision, not
    something this test asserts; it pins the reversible behaviour that exists
    today (see set_party_status: 'an appended event ... never an edit')."""
    _register(env)
    register_party(env["ws"], "bot-7", "agent", actor="user", log_root=env["lr"])
    set_party_status(env["ws"], "bot-7", "suspended", actor="human-supervisor",
                     log_root=env["lr"])
    assert _operate(env)["final"] == "refused"

    set_party_status(env["ws"], "bot-7", "active", reason="cleared",
                     actor="human-supervisor", log_root=env["lr"])
    resumed = _operate(env)
    assert resumed["final"] != "refused", "re-activation did not restore operation"
    assert resumed["steps"], "resumed agent produced no steps"
