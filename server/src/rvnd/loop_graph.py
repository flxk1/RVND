# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compose RVND's runtime controls as an explicit graph of loops.

The projection names the feedback paths already implemented by the execution,
drift, breaker, and human-decision components. ``assess_with_drift`` is the
runtime seam: drift metrics feed the breaker before the action gate runs.

The module performs no delivery and writes no audit events. Callers remain
responsible for recording outcomes and routing human decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .action_gate import ActionRequest, ApprovalUsage, Observables, StandingApproval
from .governance_lane import GovernanceLane, LaneDecision, evaluate_lane, list_lanes
from .breaker import Breaker
from .drift_monitor import DriftReport, drift_tick
from .governance_graph import governance_graph
from .oversight import OversightOutcome, assess
from .oversight_drift import DriftSignal, drift_tripwire, evaluate as evaluate_drift

__all__ = ["LoopAssessment", "assess_with_drift", "graph_of_loops"]


@dataclass
class LoopAssessment:
    """One action outcome with the feedback signal that governed it."""

    outcome: OversightOutcome
    drift: DriftSignal
    lane: LaneDecision

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome.to_dict(), "drift": self.drift.to_dict(),
                "lane": self.lane.to_dict()}


def _breaker_with_drift(breaker: Breaker) -> Breaker:
    """Ensure structural drift is an armed condition on the supplied breaker."""
    if any(t.metric == "drift_structural" for t in breaker.tripwires):
        return breaker
    breaker.tripwires.append(drift_tripwire())
    return breaker


def _control_bindings(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Route compiled governance controls to the loops that enforce them."""
    bindings: list[dict[str, Any]] = []
    for node in graph.get("nodes", []):
        if node.get("kind") != "use_case":
            continue
        source = node.get("id", "")
        if node.get("prohibited"):
            bindings.append({
                "source": source, "loop": "breaker", "control": "prohibition",
                "effect": "veto", "basis": node.get("reservations", []),
            })
        if node.get("reserved"):
            bindings.append({
                "source": source, "loop": "oversight", "control": "reserved-act",
                "effect": "human-decision", "basis": node.get("reservations", []),
            })
        if node.get("grade_ceiling") is not None:
            bindings.append({
                "source": source, "loop": "oversight", "control": "grade-ceiling",
                "effect": "cap", "value": node.get("grade_ceiling"),
            })
        bindings.append({
            "source": source, "loop": "execution", "control": "authority",
            "effect": "admit-listed-agents",
        })
    if graph:
        bindings.append({
            "source": "signed-governance-graph", "loop": "drift",
            "control": "configuration-baseline", "effect": "monitor",
        })
    return bindings


def assess_with_drift(
    req: ActionRequest,
    report: DriftReport,
    *,
    breaker: Breaker,
    metrics: Optional[dict[str, Any]] = None,
    standing_approvals: Iterable[StandingApproval] = (),
    prohibited_actions: Iterable[str] = (),
    posture: str = "balanced",
    observables: Optional[Observables] = None,
    approval_usage: Optional[dict[str, ApprovalUsage]] = None,
    grade_ceiling: str = "",
    now: Optional[float] = None,
    scope: str = "",
    grounds: Iterable[dict[str, Any]] = (),
    options: Iterable[dict[str, Any]] = (),
    link_target: str = "",
    dossier_material: Optional[dict[str, Any]] = None,
    lane: Optional[GovernanceLane] = None,
    use_case_id: str = "",
    connector_id: str = "",
    policy_fingerprint: str = "",
) -> LoopAssessment:
    """Evaluate drift, feed it to the breaker, then assess the action.

    Structural drift trips the pre-armed breaker and caps the action at L0.
    Behavioral drift does not quarantine the agent: it caps the current action
    at interactive L0, which routes benign work to a person and refuses risky
    work below its minimum grade. Missing or thin evidence stays a reported
    re-baseline need and does not trip the breaker.
    """
    signal = evaluate_drift(report)
    live_metrics = dict(metrics or {})
    live_metrics.update(signal.metrics)
    effective_prohibitions = set(prohibited_actions)
    lane_decision = evaluate_lane(
        lane, req, use_case_id=use_case_id, connector_id=connector_id,
        policy_fingerprint=policy_fingerprint)
    if not lane_decision.allowed:
        effective_prohibitions.add(req.action_class)
    if signal.structural:
        effective_prohibitions.add(req.action_class)

    effective_ceiling = grade_ceiling
    if signal.recommend_floor:
        effective_ceiling = "L0"

    outcome = assess(
        req,
        breaker=_breaker_with_drift(breaker),
        standing_approvals=standing_approvals,
        prohibited_actions=effective_prohibitions,
        posture=posture,
        observables=observables,
        approval_usage=approval_usage,
        grade_ceiling=effective_ceiling,
        metrics=live_metrics,
        now=now,
        scope=scope,
        grounds=grounds,
        options=options,
        link_target=link_target,
        dossier_material=dossier_material,
    )
    return LoopAssessment(outcome=outcome, drift=signal, lane=lane_decision)


def graph_of_loops(
    folder_context: str | Path = "",
    *,
    log_root: Optional[str | Path] = None,
    catalogue_fingerprint: str = "",
    drift: Optional[DriftSignal] = None,
) -> dict[str, Any]:
    """Project the live control topology for a canvas or operator surface.

    When ``folder_context`` is present, execution counts come from the signed
    governance graph and drift comes from the latest recorded baseline. Passing
    ``drift`` keeps the function usable for a just-computed in-memory signal.
    """
    execution: dict[str, Any] = {}
    controls: list[dict[str, Any]] = []
    lanes: list[dict[str, Any]] = []
    if folder_context:
        root = str(log_root) if log_root is not None else None
        governed = governance_graph(str(folder_context), log_root=root)
        execution = governed.get("summary", {})
        controls = _control_bindings(governed)
        lanes = [lane.to_dict() for lane in list_lanes(
            folder_context, log_root=log_root)]
        if drift is None:
            report = drift_tick(
                folder_context,
                log_root=Path(log_root) if log_root is not None else None,
                catalogue_fingerprint=catalogue_fingerprint,
            )
            drift = evaluate_drift(report)
    drift_state = "unmeasured"
    if drift is not None:
        drift_state = (
            "structural" if drift.structural else
            "behavioural" if drift.recommend_floor else
            "needs-rebaseline" if drift.needs_rebaseline else
            "clear"
        )
    nodes = [
        {"id": "execution", "kind": "loop", "label": "Execution loop",
         "state": {**execution, "governance_lanes": len(lanes)}},
        {"id": "oversight", "kind": "loop", "label": "Oversight loop"},
        {"id": "drift", "kind": "loop", "label": "Drift loop", "state": drift_state},
        {"id": "breaker", "kind": "loop", "label": "Recovery loop"},
        {"id": "policy", "kind": "loop", "label": "Policy improvement loop",
         "state": {"distributed_controls": len(controls)}},
        {"id": "human", "kind": "boundary", "label": "Human decision surface"},
        {"id": "world", "kind": "boundary", "label": "Egress boundary"},
    ]
    edges = [
        {"from": "execution", "to": "oversight", "kind": "proposes"},
        {"from": "oversight", "to": "world", "kind": "permits", "veto": True},
        {"from": "execution", "to": "drift", "kind": "telemetry"},
        {"from": "drift", "to": "breaker", "kind": "structural-trip", "veto": True},
        {"from": "drift", "to": "oversight", "kind": "behavioural-review"},
        {"from": "breaker", "to": "oversight", "kind": "grade-cap", "veto": True},
        {"from": "oversight", "to": "human", "kind": "escalates"},
        {"from": "human", "to": "policy", "kind": "records-decision"},
        {"from": "policy", "to": "oversight", "kind": "tightens-rules"},
        {"from": "human", "to": "breaker", "kind": "clears-with-rationale"},
    ]
    return {
        "schema": "rvnd/graph-of-loops/v1",
        "folder_context": str(folder_context) if folder_context else "",
        "nodes": nodes,
        "edges": edges,
        "control_bindings": controls,
        "governance_lanes": lanes,
        "invariants": [
            "strictest edge wins",
            "structural drift quarantines before egress",
            "behavioural drift routes to review without automatic quarantine",
            "only a named human with rationale can clear quarantine",
            "monitoring evidence never rewrites its own policy baseline",
            "every registered agent action requires a matching approved governance lane",
        ],
    }
