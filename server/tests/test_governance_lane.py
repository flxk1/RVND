# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governance-lane boundaries and durable approval versions."""

import pytest

from workspaces.action_gate import ActionRequest
from workspaces.governance_lane import (
    GovernanceLane, evaluate_lane, get_lane, list_lanes, register_lane,
)


def _lane(**changes):
    values = {
        "lane_id": "lane-research", "agent": "bot", "max_grade": "L3",
        "action_classes": ("summarise",), "footprints": ("personal-data",),
        "folder": "/workspace", "use_cases": ("research",),
        "connectors": ("local-model",), "policy_fingerprint": "sha256:policy",
        "approved_by": "alice", "rationale": "bounded research assistant",
    }
    values.update(changes)
    return GovernanceLane(**values)


def _request(**changes):
    values = {
        "agent": "bot", "action_class": "summarise", "autonomy_grade": "L3",
        "footprint": ("personal-data",), "folder": "/workspace",
    }
    values.update(changes)
    return ActionRequest(**values)


def test_matching_action_stays_inside_lane():
    result = evaluate_lane(
        _lane(), _request(), use_case_id="research", connector_id="local-model",
        policy_fingerprint="sha256:policy")
    assert result.allowed


@pytest.mark.parametrize("request_changes,context,fragment", [
    ({"action_class": "publish"}, {}, "action_class"),
    ({"autonomy_grade": "L4"}, {}, "exceeds"),
    ({"footprint": ("personal-data", "external-publish")}, {}, "footprints"),
    ({"folder": "/other"}, {}, "folder"),
    ({}, {"use_case_id": "sales"}, "use_case"),
    ({}, {"connector_id": "cloud-model"}, "connector"),
    ({}, {"policy_fingerprint": "sha256:changed"}, "fingerprint"),
])
def test_every_lane_dimension_fails_closed(request_changes, context, fragment):
    defaults = {"use_case_id": "research", "connector_id": "local-model",
                "policy_fingerprint": "sha256:policy"}
    defaults.update(context)
    result = evaluate_lane(_lane(), _request(**request_changes), **defaults)
    assert not result.allowed
    assert any(fragment in violation for violation in result.violations)


@pytest.mark.parametrize("grade", ["L0", "L1", "L2", "L3", "L4"])
def test_every_grade_without_lane_is_refused(grade):
    result = evaluate_lane(None, _request(autonomy_grade=grade))
    assert not result.allowed
    assert result.violations == ("no approved governance lane",)


@pytest.mark.parametrize("assigned,requested", [
    ("L0", "L1"), ("L1", "L2"), ("L2", "L3"), ("L3", "L4"),
])
def test_agent_cannot_request_a_grade_above_its_lane(assigned, requested):
    lane = _lane(max_grade=assigned)
    result = evaluate_lane(
        lane, _request(autonomy_grade=requested), use_case_id="research",
        connector_id="local-model", policy_fingerprint="sha256:policy")
    assert not result.allowed
    assert any("exceeds" in violation for violation in result.violations)


def test_l0_action_remains_inside_an_l0_lane():
    lane = _lane(max_grade="L0")
    result = evaluate_lane(
        lane, _request(autonomy_grade="L0"), use_case_id="research",
        connector_id="local-model", policy_fingerprint="sha256:policy")
    assert result.allowed


def test_lane_versions_are_signed_and_latest_wins(tmp_path):
    folder = tmp_path / "workspace"
    first = _lane(folder=str(folder), version=1)
    second = _lane(folder=str(folder), version=2, action_classes=("summarise", "classify"))
    register_lane(folder, first, log_root=tmp_path / "log")
    register_lane(folder, second, log_root=tmp_path / "log")
    assert get_lane(folder, "bot", log_root=tmp_path / "log").version == 2
    assert len(list_lanes(folder, log_root=tmp_path / "log")) == 1
    with pytest.raises(ValueError, match="increase the version"):
        register_lane(folder, first, log_root=tmp_path / "log")


def test_workflow_facade_registers_and_lists_lanes(tmp_path, monkeypatch):
    from workspaces import mcp_server

    monkeypatch.setenv("WORKSPACES_LOG_ROOT", str(tmp_path / "log"))
    folder = str(tmp_path / "workspace")
    result = mcp_server.workspace_workflow("governance_lane_register", {
        "folder_context": folder, "lane_id": "lane-bot", "agent": "bot",
        "max_grade": "L4", "action_classes": ["classify"],
        "approved_by": "alice", "rationale": "bounded classifier",
    })
    listed = mcp_server.workspace_workflow(
        "governance_lane_list", {"folder_context": folder})
    help_ops = {item["op"] for item in mcp_server.workspace_workflow("help")["ops"]}
    assert result["ok"]
    assert listed["lanes"][0]["lane_id"] == "lane-bot"
    assert {"governance_lane_register", "governance_lane_list"} <= help_ops


def test_primary_chokepoint_denies_l0_agent_requesting_l1(tmp_path):
    from workspaces.governance import decide_action
    from workspaces.parties import register_party

    folder = tmp_path / "workspace"
    log_root = tmp_path / "log"
    register_party(str(folder), "bot", "agent", grade="L0", log_root=str(log_root))
    register_lane(folder, GovernanceLane(
        lane_id="lane-l0", agent="bot", max_grade="L0",
        action_classes=("summarise",), folder=str(folder),
        approved_by="alice", rationale="interactive summarisation only",
    ), log_root=log_root)

    escaped = decide_action(
        folder, action_class="summarise", grade="L1", actor="bot",
        log_root=log_root)
    contained = decide_action(
        folder, action_class="summarise", grade="L0", actor="bot",
        log_root=log_root)

    assert escaped["verdict"] == "deny"
    assert escaped["governance_lane"]["allowed"] is False
    assert any("L1 exceeds L0" in item
               for item in escaped["governance_lane"]["violations"])
    assert contained["governance_lane"]["allowed"] is True
