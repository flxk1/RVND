# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Named read-only queries over the governance patch.

The tests cover human-needed, high-risk, unfired, unwired, agent-reach, facade,
and unknown-query paths.
"""
from __future__ import annotations

import os
import pytest

from rvnd import parties as pt
from rvnd.governance_graph import governance_query
from rvnd.use_case import register_use_case
from rvnd.operations import operate

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"; ws.mkdir()
    lr = str(tmp_path / "logs")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", lr)   # facade path reads this
    pt.register_party(str(ws), "bot7", "agent", name="bot7", actor="alex", log_root=lr)
    # NB: deliberately NO human registered
    register_use_case(str(ws), use_case_id="uc-draft", name="uc-draft",
                      fingerprint={"issue_type": "liability_cap"}, risk="low",
                      allowed_agents=["bot7"], actor="alex", prior_approvals=25, log_root=lr)
    # Reservations are authored or ingested, not inferred from a legal enum.
    # uc-decide is reserved by an AUTHORED policy reservation (was the
    # automated_decision enum); needs_human_no_human still flags it.
    register_use_case(str(ws), use_case_id="uc-decide", name="uc-decide",
                      fingerprint={"issue_type": "automated_decision"}, risk="high",
                      allowed_agents=["bot7"], actor="alex",
                      policy_reservations={"uc-decide": {
                          "reserved_to": "data-protection", "act_type": "review",
                          "source": "company policy — automated decision review"}},
                      log_root=lr)
    register_use_case(str(ws), use_case_id="uc-orphan", name="uc-orphan",
                      fingerprint={}, risk="low", allowed_agents=[], actor="alex", log_root=lr)
    operate(str(ws), use_case_id="uc-draft", agent_id="bot7",
            issues=[{"issue_id": "i1", "issue_type": "liability_cap", "completeness": "high"}],
            now_epoch=1000, log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _q(env, name):
    return governance_query(env["ws"], name, log_root=env["lr"])


def test_needs_human_no_human(env):
    r = _q(env, "needs_human_no_human")
    labels = {row["use_case"] for row in r["rows"]}
    assert "uc-decide" in labels        # reserved (authored policy), no human registered


def test_auto_high_risk_empty(env):
    assert _q(env, "auto_high_risk")["rows"] == []   # law floor holds


def test_unfired(env):
    labels = {row["use_case"] for row in _q(env, "unfired")["rows"]}
    assert "uc-decide" in labels and "uc-orphan" in labels
    assert "uc-draft" not in labels     # uc-draft was run


def test_unwired(env):
    labels = {row["use_case"] for row in _q(env, "unwired_use_cases")["rows"]}
    assert labels == {"uc-orphan"}


def test_agent_reach(env):
    rows = {row["agent"]: row["use_cases"] for row in _q(env, "agent_reach")["rows"]}
    assert rows["bot7"] == 2            # uc-draft + uc-decide


def test_facade_and_surface(env):
    from rvnd import mcp_server as M
    assert len(M._DECLARED_TOOLS) == 24
    r = M.workspace_workflow(op="governance_query", params={"folder_context": env["ws"], "query": "unfired"})
    assert "rows" in r and r["count"] >= 2
    bad = M.workspace_workflow(op="governance_query", params={"folder_context": env["ws"], "query": "nope"})
    assert "error" in bad
