# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""matrix_coverage — the coverage lens: governance gaps as a grid.

A cord canvas shows what is wired, not what is missing. This projects the same
`governance_graph` as a rows x cols grid so an over-permissive cell is the
finding — the spatial form of the flat coverage queries. Each cell carries the
engine's strictest-wins verdict for its band (the projection a lamp shows) with
the source use cases attached for a deep link.

Pure projection: no writes, no model calls. The flagship preset kind x risk is
derived and read-only; loosening is the governed write in Rules, never here.

  coverage_matrix(folder_context, preset="kind_risk") -> {rows, cols, cells, ...}
"""
from __future__ import annotations

from typing import Any, Optional

from .governance_graph import governance_graph

# Strictest-wins over an egress band, matching governance_graph's edge ranking
# plus the two static dispositions (prohibited severs; unfired = no run yet).
from .adapters.policy_languages import verdict_order as _verdict_order

# 'unfired' is an RVND display state (rank 0); the verdict ranks are consumed from
# governance's grammar so display and enforcement share one restrictiveness order.
_RANK = {"unfired": 0, **{v: i + 1 for i, v in enumerate(_verdict_order())}}
_LETTER = {"auto": "a", "human": "h", "refused": "f", "reserved": "r",
           "prohibited": "x", "unfired": "u", "none": "·"}

# The risk axis, ascending. Matches step_contract's requirement ladder; an
# unrecognised risk lands in "other" so a mis-tagged use case is still visible.
_RISK_COLS = ("low", "medium", "high", "critical")
_HIGH_BAND = ("high", "critical")


def _strictest(verdicts: list[str]) -> str:
    """The governing verdict for a band: the strictest present, or "none"."""
    if not verdicts:
        return "none"
    return max(verdicts, key=lambda v: _RANK.get(v, 0))


def _uc_verdict(node: dict, egress: dict) -> str:
    """One use case's governing disposition, strictest-wins. A reservation is a
    standing floor — it governs whether or not a run has happened — so it beats
    the egress edge's run verdict; prohibition severs outright; otherwise the
    latest run's verdict stands, or unfired when nothing has run."""
    if node.get("prohibited"):
        return "prohibited"
    if node.get("reserved"):
        return "reserved"
    return egress.get(node["id"], {}).get("verdict") or "unfired"


def _kind_of(node: dict) -> str:
    """The kind axis value for a use case: its classified issue_type, or
    "unclassified" (itself worth seeing — an unclassified high-risk act is a
    coverage question, not a blank)."""
    return (node.get("issue_type") or "").strip() or "unclassified"


def _kind_risk(g: dict, *, folder_context: str, log_root, tags) -> dict[str, Any]:
    """Kind x Risk: policy as a shape. Rows are the kinds present, cols the
    risk bands; a cell is the strictest verdict across its use cases. A finding
    is a permissive verdict (auto) in the high-risk band — the same concern the
    auto_high_risk query reports, read as a shape."""
    nodes, edges = g["nodes"], g["edges"]
    egress = {e["from"]: e for e in edges if e["kind"] == "egress"}
    ucs = [n for n in nodes if n["kind"] == "use_case"]
    if tags:
        want = set(tags)
        ucs = [u for u in ucs if want & set(u.get("tags") or [])]

    risks = list(_RISK_COLS)
    if any((u.get("risk") or "") not in _RISK_COLS for u in ucs):
        risks = risks + ["other"]

    def risk_of(u: dict) -> str:
        r = (u.get("risk") or "").strip()
        return r if r in risks else "other"

    kinds = sorted({_kind_of(u) for u in ucs})
    cells: list[list[dict]] = []
    findings = 0
    for k in kinds:
        row: list[dict] = []
        for r in risks:
            band = [u for u in ucs if _kind_of(u) == k and risk_of(u) == r]
            verdict = _strictest([_uc_verdict(u, egress) for u in band])
            finding = bool(band) and verdict == "auto" and r in _HIGH_BAND
            if finding:
                findings += 1
            row.append({
                "row": k, "col": r, "verdict": verdict,
                "letter": _LETTER.get(verdict, "?"),
                "count": len(band),
                "refs": [{"id": u["id"], "label": u["label"]} for u in band],
                "finding": finding,
                "why": ("auto on a high-risk band — a person is not in the loop"
                        if finding else ""),
            })
        cells.append(row)
    return {
        "preset": "kind_risk", "title": "Kind × Risk", "editable": False,
        "row_axis": "kind", "col_axis": "risk",
        "rows": kinds, "cols": risks, "cells": cells,
        "findings": findings, "empty": not ucs,
        "note": ("derived from the run verdicts — read-only; loosening is the "
                 "governed write in Rules, never a click here"),
    }


def _task_role(g: dict, *, folder_context: str, log_root, tags) -> dict[str, Any]:
    """Task x Role: can a registered role discharge each reserved act? Rows are
    the tasks that reserve an act, columns the competences those reservations
    name. A cell is covered when at least one active human holds that
    competence, and a fail-closed finding when the act is reserved to a
    competence no one can discharge — a reservation that can never be met."""
    from .parties import list_parties
    ucs = [n for n in g["nodes"] if n["kind"] == "use_case"
           and n.get("reservations")]
    if tags:
        want = set(tags)
        ucs = [u for u in ucs if want & set(u.get("tags") or [])]

    def _comps_of(u: dict) -> set:
        return {(r.get("reserved_to") or "").strip()
                for r in u.get("reservations", [])
                if (r.get("reserved_to") or "").strip()}

    cols = sorted({c for u in ucs for c in _comps_of(u)})

    def _approvers(comp: str) -> int:
        res = list_parties(folder_context, kind="human", competence=comp,
                           log_root=log_root)
        rows = res.get("parties", []) if isinstance(res, dict) else (res or [])
        return sum(1 for p in rows if (p.get("status") or "active") == "active")
    approver_count = {c: _approvers(c) for c in cols}

    rows_out, cells, findings = [], [], 0
    for u in ucs:
        rows_out.append(u["label"])
        u_comps = _comps_of(u)
        row = []
        for c in cols:
            if c not in u_comps:
                row.append({"row": u["label"], "col": c, "verdict": "none",
                            "letter": "·", "count": 0, "refs": [],
                            "finding": False, "why": ""})
                continue
            n = approver_count.get(c, 0)
            gap = n == 0
            if gap:
                findings += 1
            row.append({
                "row": u["label"], "col": c,
                "verdict": "gap" if gap else "covered",
                "letter": "!" if gap else "r", "count": n,
                "refs": [{"id": u["id"], "label": u["label"]}],
                "finding": gap,
                "why": (f"reserved to {c!r} but no active party holds that"
                        " competence — the reservation can never be met"
                        if gap else ""),
            })
        cells.append(row)
    return {
        "preset": "task_role", "title": "Task × Role", "editable": False,
        "row_axis": "task", "col_axis": "role",
        "rows": rows_out, "cols": cols, "cells": cells,
        "findings": findings, "empty": not ucs,
        "note": ("reserved acts vs the competent roster — read-only; the fix is"
                 " registering a competent approver in Roles, not a click here"),
    }


def _task_agent(g: dict, *, folder_context: str, log_root, tags) -> dict[str, Any]:
    """Task x Agent: the authority grants as a grid. Rows are the use cases,
    columns the agents; a granted cell carries the task's governing verdict and
    is editable in the tighten-only direction (revoke via authority_revoke) —
    granting stays a deliberate act on the patch. An empty row is an unwired
    task; an over-full column is an over-reaching agent."""
    nodes, edges = g["nodes"], g["edges"]
    egress = {e["from"]: e for e in edges if e["kind"] == "egress"}
    ucs = [n for n in nodes if n["kind"] == "use_case"]
    if tags:
        want = set(tags)
        ucs = [u for u in ucs if want & set(u.get("tags") or [])]
    agents = [n for n in nodes if n["kind"] == "agent"]
    auth = {(e["from"], e["to"]) for e in edges if e["kind"] == "authority"}

    cols = [a["label"] for a in agents]
    rows_out, cells = [], []
    for u in ucs:
        rows_out.append(u["label"])
        row = []
        for a in agents:
            if (a["id"], u["id"]) not in auth:
                row.append({"row": u["label"], "col": a["label"],
                            "verdict": "none", "letter": "·", "count": 0,
                            "refs": [], "finding": False, "why": ""})
                continue
            verdict = _uc_verdict(u, egress)
            row.append({
                "row": u["label"], "col": a["label"], "verdict": verdict,
                "letter": _LETTER.get(verdict, "?"), "count": 1,
                "refs": [{"id": u["id"], "label": u["label"]}],
                "finding": False, "why": "",
                "editable": True,
                "use_case_id": u["id"].replace("uc:", "", 1),
                "agent_id": a["id"].replace("party:", "", 1),
            })
        cells.append(row)
    return {
        "preset": "task_agent", "title": "Task × Agent", "editable": True,
        "row_axis": "task", "col_axis": "agent",
        "rows": rows_out, "cols": cols, "cells": cells,
        "findings": 0, "empty": not (ucs and agents),
        "note": ("the authority grants, authored — a granted cell may be"
                 " revoked (tighten-only, recorded); granting stays a"
                 " deliberate act on the patch. An empty row is a task no"
                 " agent can run"),
    }


# preset key -> (builder, one-line gap question). New presets register here; the
# facade lists them from this table so a new lens is never silently unreachable.
_PRESETS = {
    "kind_risk": (_kind_risk, "where is autonomy weak in the high-risk band?"),
    "task_role": (_task_role, "is any reserved act left with no role to discharge it?"),
    "task_agent": (_task_agent, "who may run what, and where is authority too wide?"),
}


def presets() -> list[dict[str, str]]:
    """The available lenses and the gap question each answers."""
    return [{"preset": k, "question": q} for k, (_, q) in _PRESETS.items()]


def coverage_matrix(folder_context: str, preset: str = "kind_risk", *,
                    gaps_only: bool = False, tags: Optional[list[str]] = None,
                    log_root: Optional[str] = None) -> dict[str, Any]:
    """Project one coverage lens over the patch. Read-only. ``gaps_only`` drops
    rows with no finding so the grid never becomes a wall of clean cells."""
    entry = _PRESETS.get(preset)
    if entry is None:
        return {"error": f"unknown preset {preset!r}",
                "valid": sorted(_PRESETS)}
    builder, _ = entry
    g = governance_graph(folder_context, log_root=log_root)
    out = builder(g, folder_context=folder_context, log_root=log_root, tags=tags)
    if gaps_only and out.get("cells"):
        kept = [(name, row) for name, row in zip(out["rows"], out["cells"])
                if any(c["finding"] for c in row)]
        out["rows"] = [name for name, _ in kept]
        out["cells"] = [row for _, row in kept]
        out["gaps_only"] = True
    return out
