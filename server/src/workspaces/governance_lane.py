# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Durable approval envelopes for governed agents.

An L0-L4 grade says how independently an agent may act. A governance lane says
which actions, data footprints, use cases, connectors, folder, and compiled
policy that approval covers. Every dimension is checked on every iteration;
an omitted runtime value is refused when the lane constrains that dimension.

Lane versions are appended to the signed mutation log. Widening a lane creates
a new attributed approval; runtime learning and drift never rewrite it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .action_gate import ActionRequest
from .mutation_log import LogEvent, MutationLog

__all__ = [
    "GovernanceLane", "LaneDecision", "evaluate_lane", "register_lane",
    "get_lane", "list_lanes",
]

from .adapters.policy_languages import grade_index as _grade_index

_GRADES = _grade_index()  # grade lattice consumed from governance's grammar
_EVENT_KIND = "governance-lane-approved"
_LEGACY_EVENT_KIND = "autonomy-lane-approved"


@dataclass(frozen=True)
class GovernanceLane:
    """The exact envelope a named agent is approved to occupy."""

    lane_id: str
    agent: str
    max_grade: str
    action_classes: tuple[str, ...]
    footprints: tuple[str, ...] = ()
    folder: str = ""
    use_cases: tuple[str, ...] = ()
    connectors: tuple[str, ...] = ()
    policy_fingerprint: str = ""
    version: int = 1
    approved_by: str = ""
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.lane_id.strip() or not self.agent.strip():
            raise ValueError("lane_id and agent are required")
        if self.max_grade not in _GRADES:
            raise ValueError("a governance lane must approve max_grade L0 through L4")
        if not self.action_classes:
            raise ValueError("a governance lane needs at least one action_class")
        if self.version < 1:
            raise ValueError("lane version must be at least 1")
        if not self.approved_by.strip() or not self.rationale.strip():
            raise ValueError("lane approval requires a named approver and rationale")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for field in ("action_classes", "footprints", "use_cases", "connectors"):
            data[field] = list(data[field])
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GovernanceLane":
        values = dict(data)
        for field in ("action_classes", "footprints", "use_cases", "connectors"):
            values[field] = tuple(values.get(field) or ())
        return cls(**values)


@dataclass(frozen=True)
class LaneDecision:
    lane_id: str
    allowed: bool
    violations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"lane_id": self.lane_id, "allowed": self.allowed,
                "violations": list(self.violations)}


def evaluate_lane(
    lane: Optional[GovernanceLane],
    req: ActionRequest,
    *,
    use_case_id: str = "",
    connector_id: str = "",
    policy_fingerprint: str = "",
) -> LaneDecision:
    """Check every constrained dimension, failing closed without a lane."""
    if lane is None:
        return LaneDecision("", False, ("no approved governance lane",))

    violations: list[str] = []
    if lane.agent != req.agent:
        violations.append(f"agent {req.agent!r} is outside lane agent {lane.agent!r}")
    if _GRADES.get(req.autonomy_grade, 0) > _GRADES[lane.max_grade]:
        violations.append(f"grade {req.autonomy_grade} exceeds {lane.max_grade}")
    if req.action_class not in lane.action_classes:
        violations.append(f"action_class {req.action_class!r} is outside the lane")
    outside_footprints = sorted(set(req.footprint) - set(lane.footprints))
    if outside_footprints:
        violations.append(f"footprints outside lane: {', '.join(outside_footprints)}")
    if lane.folder and req.folder != lane.folder:
        violations.append(f"folder {req.folder!r} does not match the approved folder")
    if lane.use_cases and use_case_id not in lane.use_cases:
        violations.append(f"use_case {use_case_id!r} is outside the lane")
    if lane.connectors and connector_id not in lane.connectors:
        violations.append(f"connector {connector_id!r} is outside the lane")
    if lane.policy_fingerprint and policy_fingerprint != lane.policy_fingerprint:
        violations.append("compiled policy fingerprint does not match the lane")
    return LaneDecision(lane.lane_id, not violations, tuple(violations))


def register_lane(
    folder: str | Path,
    lane: GovernanceLane,
    *,
    log_root: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Append one attributed lane approval to the signed folder chain."""
    if lane.folder and str(Path(lane.folder).resolve()) != str(Path(folder).resolve()):
        raise ValueError("lane folder does not match the registry folder")
    log = MutationLog(Path(folder), log_root=Path(log_root) if log_root else None)
    current = get_lane(folder, lane.agent, log_root=log_root)
    if current and lane.version <= current.version:
        raise ValueError("a replacement lane must increase the version")
    audit_id = log.append(LogEvent(
        event="system", folder_path=str(folder), pair_id=f"lane:{lane.lane_id}",
        channel="system", actor=lane.approved_by,
        extra={"kind": _EVENT_KIND, "lane": lane.to_dict()},
    ))
    return {"ok": True, "audit_id": audit_id, "lane": lane.to_dict()}


def list_lanes(
    folder: str | Path,
    *,
    log_root: Optional[str | Path] = None,
) -> list[GovernanceLane]:
    """Return the latest approved lane per agent."""
    log = MutationLog(Path(folder), log_root=Path(log_root) if log_root else None)
    latest: dict[str, GovernanceLane] = {}
    for event in log.replay():
        extra = event.extra or {}
        if extra.get("kind") not in (_EVENT_KIND, _LEGACY_EVENT_KIND):
            continue
        lane = GovernanceLane.from_dict(extra["lane"])
        prior = latest.get(lane.agent)
        if prior is None or lane.version > prior.version:
            latest[lane.agent] = lane
    return sorted(latest.values(), key=lambda lane: (lane.agent, lane.lane_id))


def get_lane(
    folder: str | Path,
    agent: str,
    *,
    log_root: Optional[str | Path] = None,
) -> Optional[GovernanceLane]:
    """Return the latest lane for one agent."""
    return next((lane for lane in list_lanes(folder, log_root=log_root)
                 if lane.agent == agent), None)
