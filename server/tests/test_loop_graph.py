# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Control-loop topology and the drift-to-action runtime seam."""

from workspaces.action_gate import ActionRequest
from workspaces.governance_lane import GovernanceLane
from workspaces.breaker import Breaker, Lease
from workspaces.drift_monitor import DriftReport
from workspaces.loop_graph import _control_bindings, assess_with_drift, graph_of_loops


def _request(*, footprint=()):
    return ActionRequest(
        agent="bot", action_class="summarise", autonomy_grade="L3",
        footprint=footprint,
    )


def _report(**changes):
    values = {"folder": "/workspace", "as_of": 10.0, "window_n": 50}
    values.update(changes)
    return DriftReport(**values)


def _breaker():
    return Breaker(Lease("bot", "L3", expires_at=100.0), tripwires=[])


def _lane():
    return GovernanceLane(
        "lane-summary", "bot", "L3", ("summarise",),
        approved_by="alice", rationale="bounded summarisation")


def test_structural_drift_vetoes_action_before_egress():
    result = assess_with_drift(
        _request(), _report(structural=[{"metric": "policy:lock_mode"}]),
        breaker=_breaker(), lane=_lane(), now=20.0,
    )
    assert result.outcome.blocked
    assert result.outcome.breaker_state == "QUARANTINED"
    assert result.outcome.effective_grade == "L0"


def test_behavioural_drift_routes_benign_action_to_human_review():
    result = assess_with_drift(
        _request(), _report(behavioural=[{"metric": "by_channel:api"}]),
        breaker=_breaker(), lane=_lane(), now=20.0,
    )
    assert result.outcome.needs_human
    assert result.outcome.breaker_state == "RUNNING"
    assert result.drift.recommend_floor == "REVIEW"


def test_missing_baseline_is_visible_but_does_not_invent_a_trip():
    result = assess_with_drift(
        _request(), _report(no_baseline=True), breaker=_breaker(), lane=_lane(), now=20.0,
    )
    assert result.outcome.proceed
    assert result.drift.needs_rebaseline


def test_graph_exposes_feedback_and_veto_edges():
    graph = graph_of_loops()
    assert graph["schema"] == "rvnd/graph-of-loops/v1"
    assert {node["id"] for node in graph["nodes"]} >= {
        "execution", "oversight", "drift", "breaker", "policy", "human", "world"
    }
    vetoes = {(edge["from"], edge["to"]) for edge in graph["edges"] if edge.get("veto")}
    assert ("drift", "breaker") in vetoes
    assert ("oversight", "world") in vetoes


def test_graph_reads_execution_state_and_latest_drift_baseline(tmp_path):
    graph = graph_of_loops(tmp_path, log_root=tmp_path)
    execution = next(node for node in graph["nodes"] if node["id"] == "execution")
    drift = next(node for node in graph["nodes"] if node["id"] == "drift")
    assert execution["state"]["runs"] == 0
    assert drift["state"] == "needs-rebaseline"
    assert graph["folder_context"] == str(tmp_path)


def test_workflow_facade_exposes_the_loop_graph(tmp_path, monkeypatch):
    from workspaces import mcp_server

    monkeypatch.setenv("WORKSPACES_LOG_ROOT", str(tmp_path / "log"))
    help_ops = {item["op"] for item in mcp_server.workspace_workflow("help")["ops"]}
    result = mcp_server.workspace_workflow(
        "loop_graph", {"folder_context": str(tmp_path / "workspace")})
    assert "loop_graph" in help_ops
    assert result["schema"] == "rvnd/graph-of-loops/v1"


def test_compiled_controls_are_distributed_to_enforcing_loops():
    controls = _control_bindings({"nodes": [{
        "id": "uc:credit", "kind": "use_case", "prohibited": True,
        "reserved": ["final-decision"], "reservations": [{"source": "policy:7"}],
        "grade_ceiling": 2,
    }]})
    routed = {(item["loop"], item["control"], item["effect"]) for item in controls}
    assert ("breaker", "prohibition", "veto") in routed
    assert ("oversight", "reserved-act", "human-decision") in routed
    assert ("oversight", "grade-ceiling", "cap") in routed
    assert ("execution", "authority", "admit-listed-agents") in routed
    assert ("drift", "configuration-baseline", "monitor") in routed
