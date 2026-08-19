# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""G1 — a prohibited act is a HARD stop on the run-path, at any autonomy grade.

Found by the six-scenario validation (2026-06-25): the graph showed an act
`prohibited` while operate() auto-proceeded for a hardened agent — display
promised a boundary the engine broke. operate() must now refuse a prohibited use
case unconditionally, matching the graph verdict.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rvnd import mcp_server as M


@pytest.fixture
def ws(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "w"; f.mkdir()
    M.workspace_workspace("add", {"folder_context": str(f)})
    M.workspace_policy("party_register", {"folder_context": str(f), "party_id": "bot",
                                     "kind": "agent", "actor": "x"})
    return str(f)


def _operate(f, ucid):
    return M.workspace_workflow("operate", {"folder_context": f, "use_case_id": ucid,
        "agent_id": "bot", "issues": [{"issue_id": "i1", "issue_type": ucid,
        "completeness": "high"}], "now_epoch": 1_750_000_000})


def test_prohibited_hardened_act_is_refused_not_auto(ws):
    """A hardened (grade-4) use case that an authored patch prohibits must be
    REFUSED by operate(), never auto. The graph already calls it prohibited."""
    M.workspace_workflow("use_case_register", {"folder_context": ws, "use_case_id": "risky_act",
        "name": "r", "fingerprint": {"issue_type": "risky_act"}, "risk": "low",
        "allowed_agents": ["bot"], "prior_approvals": 20, "actor": "x"})
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\ngate risky_act risk low grant bot\ncord bot -> risky_act\n"
        "cord risky_act -> master\nprohibit risky_act\n"})
    # display layer says prohibited
    v = M.workspace_workflow("governance_graph", {"folder_context": ws}).get("verdicts", {})
    assert v.get("uc:risky_act", {}).get("verdict") == "prohibited"
    # enforcement layer must agree
    r = _operate(ws, "risky_act")
    assert r["final"] == "refused", f"prohibited act not hard-refused: {r}"
    assert all(s.get("disposition") != "auto" for s in r.get("steps", []))


def test_non_prohibited_act_still_runs(ws):
    """The hard stop must be specific to prohibition — an ordinary hardened act
    still auto-proceeds (no over-blocking)."""
    M.workspace_workflow("use_case_register", {"folder_context": ws, "use_case_id": "ok_act",
        "name": "ok", "fingerprint": {"issue_type": "ok_act"}, "risk": "low",
        "allowed_agents": ["bot"], "prior_approvals": 20, "actor": "x"})
    r = _operate(ws, "ok_act")
    assert r["final"] != "refused"
    assert any(s.get("disposition") == "auto" for s in r.get("steps", []))
