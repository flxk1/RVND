# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""lane_capabilities — the agent-facing projection of ONE lane's boundaries.

An agent does not know its boundaries a priori: today it learns them by
proposing and being disposed. This module hands the agent a projection of the
same ``.lg`` policy the gate enforces — effectively "dry-run the gate" over the
candidate ``kind x risk`` action space — and nothing else. It is the per-agent
re-cut of the human-side ``coverage_matrix`` preset ``kind_risk``, returned in
a machine shape at admission time and re-queryable mid-session.

Discipline (the four fences):

* **Advisory, not dispositive.** The payload is flagged ``advisory: true``.
  It changes no enforcement — the gate re-decides every action at run time;
  an agent that misreads the projection still cannot bypass it.
* **One source of truth.** Verdicts are computed by CALLING the enforcement
  evaluator (``evaluate_log``/``_guard_holds``/``grade_meets`` via the
  sanctioned ``adapters/solver`` seam) over a patch compiled from the signed
  chain. Nothing is authored, stored, or re-implemented — no parallel
  registry to drift.
* **Fail-closed.** An unreadable policy/chain yields NO capabilities, never
  "everything allowed"; an unknown verdict clamps to the most restrictive
  symbol, never to the releasing ``auto``.
* **Preview == enforcement.** A property test asserts that every projected
  cell equals the evaluator's own terminal verdict for the same inputs
  (``tests/test_lane_capabilities.py``), so the two can never drift.

Pure projection: no writes, no model calls.

  lane_capabilities(folder_context, actor) -> {capabilities, provenance, ...}
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from .adapters.policy_languages import grade_levels as _grade_levels
from .adapters.solver.loomground import (
    LANGUAGE_VERSION as _LANGUAGE_VERSION,
    RISKS as _RISKS,
    VERDICTS as _VERDICTS,
    _guard_holds,
    evaluate_log as _evaluate_log,
)
from .governance_graph import governance_graph
from .governance_lane import get_lane
from .operations import AUTO_GRADE_MIN

__all__ = ["lane_capabilities", "preview_patch", "SCHEMA_KIND"]

SCHEMA_KIND = "lane_capabilities/v1"

#: the step-(2) default-deny reading for a kind no gate grants (§7.1).
_DEFAULT_DENY = "not granted at any gate (default-deny)"

_NOTES = [
    "advisory projection — the gate re-decides every action at run time",
    "collapse rule: a kind whose cell is identical across all risk tiers is "
    "reported once with by_risk omitted and flat verdict fields",
]

_SOURCE = "projection of the enforced .lg policy; not an authored registry"

_RISK_RANK = {r: i for i, r in enumerate(_RISKS)}


def _gates(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The folder's gates by bare id: each use-case node of the server patch."""
    return {n["id"].split(":", 1)[1]: n
            for n in graph.get("nodes", []) if n.get("kind") == "use_case"}


def _authority_pairs(graph: dict[str, Any]) -> set[tuple[str, str]]:
    """Bare (actor, gate) pairs holding an authority cord on the chain."""
    return {(e["from"].split(":", 1)[1], e["to"].split(":", 1)[1])
            for e in graph.get("edges", []) if e.get("kind") == "authority"}


def preview_patch(
    graph: dict[str, Any], actor: str, kind: str,
) -> Optional[dict[str, Any]]:
    """Compile the solver patch enforcement gates ``(actor, kind)`` with.

    One gate per patch: the kind's use case as the source (and terminal) gate,
    the acting actor, and the master boundary. The gate carries the run-path's
    single auto threshold as its required grade (``AUTO_GRADE_MIN``); the
    actor carries the use case's EARNED contract grade — the same granted/
    required pair ``operate()`` feeds ``grade_meets``. Prohibitions and
    reservations come verbatim from the chain (a reservation's authored
    ``when`` guard rides along). Returns ``None`` when the kind is unwired —
    there is no gate to enter, which is the §7.1 step-(2) default-deny.
    Derived, never stored: recompiled from the graph on every call."""
    uc = _gates(graph).get(kind)
    if uc is None:
        return None
    nodes: list[dict[str, Any]] = [
        # granted side: the earned contract grade (operate()'s authority);
        # a missing grade stays ungraded and never meets a real requirement.
        {"id": actor, "class": "actor", "grade": uc.get("grade")},
        {"id": kind, "class": "gate",
         "risk_floor": uc.get("risk") or "low",
         "grade_required": AUTO_GRADE_MIN},
        {"id": "master", "class": "master"},
    ]
    cords: list[dict[str, Any]] = []
    if (actor, kind) in _authority_pairs(graph):
        cords.append({"from": actor, "to": kind, "type": "authority"})
    cords.append({"from": kind, "to": "master", "type": "egress"})
    return {
        "nodes": nodes,
        "cords": cords,
        "grants": [],  # a bare authority cord confers full authority
        "prohibitions": ([{"kind": kind, "when": None}]
                         if uc.get("prohibited") else []),
        "reservations": [{"kind": kind, "when": (r.get("when") or None)}
                         for r in (uc.get("reservations") or [])],
        "obligations": [],
    }


def _eff_risk(candidate: str, floor: str) -> str:
    """The gate's effective risk: max(token risk, gate floor) — §7.1."""
    return _RISKS[max(_RISK_RANK.get(candidate, 0), _RISK_RANK.get(floor, 0))]


def _refused_cell() -> dict[str, Any]:
    return {"verdict": "refused", "grade_required": None,
            "escalation": None, "guard": _DEFAULT_DENY}


def _cell(
    patch: dict[str, Any],
    uc: dict[str, Any],
    kind: str,
    risk: str,
    actor: str,
    tags: list[str],
) -> dict[str, Any]:
    """One projected cell: run the enforcement evaluator over a synthesized
    activation token and attribute the disposition (grade / escalation /
    governing guard). The verdict is the evaluator's terminal effective
    verdict — never computed here."""
    token = {"id": f"preview:{kind}:{risk}", "kind": kind, "risk": risk,
             "party": actor, "provenance": [], "tags": list(tags)}
    log = _evaluate_log(
        patch, {"activations": [{"source": kind, "actor": actor,
                                 "token": token}]})
    verdict = log[-1]["verdict"] if log else _VERDICTS[-1]
    if verdict not in _VERDICTS:            # fail-safe clamp, never to auto
        verdict = _VERDICTS[-1]

    grade_required: Optional[str] = None
    escalation: Optional[str] = None
    guard: Optional[str] = None
    if verdict == "prohibited":
        escalation = "severed"
        guard = f"prohibit {kind}"
    elif verdict == "refused":
        guard = _DEFAULT_DENY
    elif verdict == "reserved":
        eff = _eff_risk(risk, uc.get("risk") or "low")
        governing = next(
            (r for r in (uc.get("reservations") or [])
             if _guard_holds(r.get("when") or None, token, eff)), None)
        if governing is not None:
            escalation = governing.get("reserved_to") or "designated-approver"
            guard = f"reserve {kind} by {escalation}"
            if governing.get("when"):
                guard += f" when {governing['when']}"
        else:                               # attribution gap, disposition stands
            escalation = "designated-approver"
            guard = f"reserve {kind}"
    else:                                   # auto | human — the graded step-(4)
        grade_required = _grade_levels()[AUTO_GRADE_MIN]
        if verdict == "human":
            escalation = "human-in-the-loop"
    return {"verdict": verdict, "grade_required": grade_required,
            "escalation": escalation, "guard": guard}


def _fail_closed(
    folder: str, actor: str, reason: str, *, readable: bool,
) -> dict[str, Any]:
    """No capabilities, never 'everything allowed' — the unreadable-policy
    shape (mirrors console_snapshot's unreadable-never-skipped discipline)."""
    return {"ok": False, "kind": SCHEMA_KIND, "folder_context": folder,
            "actor": actor, "advisory": True, "readable": readable,
            "reason": reason, "capabilities": []}


def lane_capabilities(
    folder_context: str,
    actor: str,
    *,
    kinds: Optional[list[str]] = None,
    risks: Optional[list[str]] = None,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Project ONE agent's governance-lane boundaries. Read-only.

    For every candidate ``kind`` (the lane's declared action_classes /
    use_cases ∪ the folder's wired use-case kinds — derived, never
    hand-listed) and every risk tier of the governance vocabulary: the verdict
    the gate would dispose (``auto|human|reserved|refused|prohibited``), the
    grade required for auto, the escalation point, and the governing guard.
    Stamped with the ``policy_fingerprint`` the lane is bound to, so a stale
    copy is self-identifying. Advisory, never dispositive; fail-closed when
    the chain will not read."""
    folder = str(Path(folder_context).expanduser().resolve())
    try:
        lane = get_lane(folder, actor, log_root=log_root)
        graph = (governance_graph(folder, log_root=log_root)
                 if lane is not None else None)
    except Exception as exc:  # noqa: BLE001 — an unreadable chain must
        # project NO capabilities (fail-closed), whatever broke the read.
        return _fail_closed(folder, actor,
                            f"policy unreadable: {type(exc).__name__}: {exc}",
                            readable=False)
    if lane is None:
        return _fail_closed(folder, actor, "no active governance lane",
                            readable=True)

    risk_axis = [r for r in (risks if risks is not None else _RISKS)
                 if r in _RISK_RANK]
    if not risk_axis:
        return _fail_closed(folder, actor,
                            "no candidate risk tier is in the risk vocabulary",
                            readable=True)

    gates = _gates(graph or {})
    lane_kinds = set(lane.action_classes) | set(lane.use_cases)
    if kinds is not None:
        candidates = list(dict.fromkeys(kinds))
    else:
        candidates = sorted(lane_kinds | set(gates))

    capabilities: list[dict[str, Any]] = []
    for kind in candidates:
        uc = gates.get(kind)
        if uc is None:
            # A kind the lane names but the folder has not wired (or an
            # unknown candidate): no gate to enter — default-deny, no blank.
            cells = {r: _refused_cell() for r in risk_axis}
        else:
            patch = preview_patch(graph or {}, actor, kind)
            tags = list(uc.get("tags") or [])
            cells = {r: _cell(patch or {}, uc, kind, r, actor, tags)
                     for r in risk_axis}
        entry: dict[str, Any] = {"kind": kind,
                                 "in_lane": kind in lane_kinds,
                                 "wired": uc is not None}
        first = cells[risk_axis[0]]
        if all(cells[r] == first for r in risk_axis):
            entry.update(first)             # collapsed: flat cell, no by_risk
        else:
            entry["by_risk"] = cells
        capabilities.append(entry)

    return {
        "ok": True,
        "kind": SCHEMA_KIND,
        "folder_context": folder,
        "actor": actor,
        "advisory": True,
        "readable": True,
        "provenance": {
            "policy_fingerprint": lane.policy_fingerprint,
            "lane_id": lane.lane_id,
            "lane_version": lane.version,
            "language_version": _LANGUAGE_VERSION,
            "max_grade": lane.max_grade,
            "derived_at": time.time(),
            "source": _SOURCE,
        },
        "risk_axis": risk_axis,
        "capabilities": capabilities,
        "notes": list(_NOTES),
    }
