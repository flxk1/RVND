# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Executable regressions for the June 2026 six-scenario review.

The old scenario harness was useful as an exploration script, but it
was not a maintained gate. These tests turn the review's main claims into normal
pytest checks over the current `workspaces` facade:

* authored prohibitions are enforced by the run path, not only rendered;
* authored reservations route to the named competence;
* ordinary hardened, low-risk acts can still auto-run;
* an ingested policy's named statute reaches the operated reservation basis;
* the grounder refuses ungrounded claims ("no citation, no claim").
"""
from __future__ import annotations

from pathlib import Path

import pytest

from workspaces import mcp_server as M


NOW = 1_750_000_000


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "w"
    f.mkdir()
    M.workspace_workspace("add", {"folder_context": str(f)})
    return str(f)


def _party(folder: str, party_id: str, kind: str, **kw):
    return M.workspace_policy(
        "party_register",
        {
            "folder_context": folder,
            "party_id": party_id,
            "kind": kind,
            "actor": "review-test",
            **kw,
        },
    )


def _register_auto_use_case(folder: str, use_case_id: str, agent_id: str):
    _party(folder, agent_id, "agent")
    return M.workspace_workflow(
        "use_case_register",
        {
            "folder_context": folder,
            "use_case_id": use_case_id,
            "name": use_case_id,
            "fingerprint": {"issue_type": use_case_id},
            "risk": "low",
            "allowed_agents": [agent_id],
            "prior_approvals": 20,
            "actor": "review-test",
        },
    )


def _apply_boundary(folder: str, *, agent: str, act: str, reserve_to: str = "",
                    prohibit: bool = False):
    _party(folder, agent, "agent")
    lines = [
        f"actor {agent}",
        f"gate {act} risk low grant {agent}",
        f"cord {agent} -> {act}",
        f"cord {act} -> master",
    ]
    if reserve_to:
        _party(folder, reserve_to, "human", role=reserve_to)
        lines.insert(1, f"human {reserve_to} role {reserve_to}")
        lines.append(f"reserve {act} by {reserve_to}")
    if prohibit:
        lines.append(f"prohibit {act}")
    applied = M.workspace_workflow(
        "patch_apply",
        {
            "folder_context": folder,
            "actor": "review-test",
            "netlist": "\n".join(lines) + "\n",
        },
    )
    assert applied.get("ok") is True, applied
    return applied


def _operate(folder: str, use_case_id: str, agent_id: str, *, issue_type: str | None = None):
    return M.workspace_workflow(
        "operate",
        {
            "folder_context": folder,
            "use_case_id": use_case_id,
            "agent_id": agent_id,
            "issues": [
                {
                    "issue_id": "issue-1",
                    "issue_type": issue_type or use_case_id,
                    "completeness": "high",
                }
            ],
            "now_epoch": NOW,
        },
    )


def _disposition(run: dict) -> str:
    if run.get("final") == "refused":
        return "refused"
    return (run.get("steps") or [{}])[0].get("disposition", "")


def _graph_verdict(folder: str, use_case_id: str) -> str:
    graph = M.workspace_workflow("governance_graph", {"folder_context": folder})
    return (graph.get("verdicts", {}).get(f"uc:{use_case_id}") or {}).get("verdict", "")


@pytest.mark.parametrize(
    ("auto_act", "reserved_act", "reserved_to", "prohibited_act", "agent"),
    [
        ("advance_story", "major_plot_twist", "director", "gore_past_rating", "narrator"),
        ("help_homework", "new_contact", "parent", "share_photo_externally", "curator"),
        ("explore_arch", "commit_tapeout", "architect", "exceed_power", "design_explorer"),
    ],
)
def test_review_domain_boundaries_are_enforced(
    ws, auto_act, reserved_act, reserved_to, prohibited_act, agent
):
    """The scenario boundary must bind the engine, not just the graph."""
    _register_auto_use_case(ws, auto_act, agent)
    auto = _operate(ws, auto_act, agent)
    assert _disposition(auto) == "auto", auto

    _apply_boundary(ws, agent=agent, act=reserved_act, reserve_to=reserved_to)
    reserved = _operate(ws, reserved_act, agent)
    assert _disposition(reserved) == "reserved", reserved
    assert reserved["steps"][0]["reserved_to"] == reserved_to
    assert _graph_verdict(ws, reserved_act) == "reserved"

    _apply_boundary(ws, agent=agent, act=prohibited_act, prohibit=True)
    refused = _operate(ws, prohibited_act, agent)
    assert _disposition(refused) == "refused", refused
    assert _graph_verdict(ws, prohibited_act) == "prohibited"


def test_policy_ingest_named_statute_reaches_operated_basis(ws):
    """A statute named by the user's policy is attributed through to operate()."""
    policy = (
        "Telemetry sharing must be approved by the data trustee under "
        "Data Act Article 4."
    )
    twin = M.workspace_workflow(
        "policy_ingest", {"folder_context": ws, "policy_text": policy}
    )
    assert twin.get("ok") is True, twin
    reservations = (twin.get("patch") or {}).get("reservations") or []
    assert reservations and reservations[0].get("source"), reservations

    patch = twin["patch"]
    actor_id = next(n["id"] for n in patch["nodes"] if n.get("class") == "actor")
    gate_id = reservations[0]["kind"]
    applied = M.workspace_workflow(
        "patch_apply",
        {"folder_context": ws, "actor": "review-test", "patch": patch},
    )
    assert applied.get("ok") is True, applied

    run = _operate(ws, gate_id, actor_id)
    assert _disposition(run) == "reserved", run
    assert "Data Act" in run["steps"][0].get("basis", "")


def test_music_grounder_refuses_ungrounded_claim(ws):
    """The music/copyright scenario's attribution boundary is executable."""
    res = M.workspace_grounder(
        "ground",
        {
            "folder_context": ws,
            "claim": "This sample is cleared for release.",
            "works": [],
        },
    )
    assert res["claim"]["status"] == "refused", res
    assert res["citations"] == []
