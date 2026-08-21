# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Run-path reservation routing — the defects the adversarial loop (w1222rvfd)
confirmed in the G1b commit, now fixed:

  A1 — multiple reservations on one act: ALL co-reservers must fire (none dropped).
  A2 — a reservation must bind only its own act, not be mis-applied to others.
  A3 — a reserved use case run with zero issues must reserve, not auto-complete.
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
    M.workspace_policy("party_register", {"folder_context": str(f), "party_id": "bot", "kind": "agent", "actor": "x"})
    return str(f)


def _operate(f, ucid, issues=None):
    return M.workspace_workflow("operate", {"folder_context": f, "use_case_id": ucid,
        "agent_id": "bot", "issues": issues if issues is not None else
        [{"issue_id": "i1", "issue_type": ucid, "completeness": "high"}],
        "now_epoch": 1_750_000_000})


def test_A1_multiple_reservers_on_one_act_all_fire(ws):
    """Two competences reserving the same act → both are routed, neither dropped."""
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\nhuman chief role approver\nhuman safety role safety-officer\n"
        "gate launch risk high grant bot\ncord bot -> launch\ncord launch -> master\n"
        "reserve launch by approver\nreserve launch by safety-officer\n"})
    r = _operate(ws, "launch")
    step = r["steps"][0]
    assert step["disposition"] == "reserved"
    everyone = set(step.get("reserved_to_all", [step.get("reserved_to")]))
    assert {"approver", "safety-officer"} <= everyone, f"a co-reserver was dropped: {step}"


def test_A3_reserved_with_zero_issues_does_not_auto_complete(ws):
    """A reserved act operated with no detected issues reserves (fail toward
    oversight), never final='complete'."""
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\nhuman chief role approver\ngate ship risk high grant bot\n"
        "cord bot -> ship\ncord ship -> master\nreserve ship by approver\n"})
    r = _operate(ws, "ship", issues=[])
    assert r["final"] != "complete", f"reserved act auto-completed with zero issues: {r}"
    assert any(s.get("disposition") == "reserved" for s in r.get("steps", []))


def test_reserves_do_not_leak_across_use_cases(ws):
    """A use case's reserved_acts only carry ITS OWN reserves (keyed by gate kind);
    operating one use case never routes to another's reserver — even with a
    non-matching issue_type that hits the fallback. (Locks a loop false-positive that
    assumed cross-use_case trigger mixing; the per-use_case projection prevents it.)"""
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\nhuman cap role captain\nhuman saf role safety\n"
        "gate ship risk low grant bot\ngate deploy risk low grant bot\n"
        "cord bot -> ship\ncord bot -> deploy\ncord ship -> master\ncord deploy -> master\n"
        "reserve ship by captain\nreserve deploy by safety\n"})
    r = M.workspace_workflow("operate", {"folder_context": ws, "use_case_id": "ship",
        "agent_id": "bot", "issues": [{"issue_id": "i1", "issue_type": "audit",
        "completeness": "high"}], "now_epoch": 1_750_000_000})
    s = r["steps"][0]
    routed = set(s.get("reserved_to_all", [])) | {s.get("reserved_to")}
    assert "safety" not in routed, f"another use case's reserver leaked in: {s}"
    assert s.get("reserved_to") == "captain"


def test_A2_reservation_routes_to_the_authored_competence(ws):
    """The reserved disposition routes to the competence the user authored."""
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\nhuman chief role release-boss\ngate cut risk high grant bot\n"
        "cord bot -> cut\ncord cut -> master\nreserve cut by release-boss\n"})
    step = _operate(ws, "cut")["steps"][0]
    assert step["disposition"] == "reserved"
    assert step["reserved_to"] == "release-boss"
