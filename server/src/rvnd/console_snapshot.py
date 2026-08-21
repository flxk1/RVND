# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Console rollup — one aggregate per workspace for the mixer at scale.

The mixer's bus strips need per-workspace state without one round trip each: at
twenty folders that is twenty polls a frame. ``console_snapshot`` folds every
workspace the caller may see into one response — an aggregate node per folder
(worst verdict, pending count, agent tallies, tree parent) plus a bounded
attention list of the folders that want a person.

Reads only. Workspace enumeration goes through ``list_known_workspaces``, which
is already scoped to the request principal's party membership, so a folderless
cross-workspace read cannot widen what a principal sees. Aggregates are meters,
never a score: each number is reported on its own.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

# worst-first: the strictest state a folder carries decides its lamp.
from .adapters.policy_languages import verdict_order as _verdict_order

# most-restrictive first: governance's verdict order, reversed. 'ask' is an RVND
# display-only prompt state (not a governance verdict), kept explicit at the end.
_SEVERITY = tuple(reversed(_verdict_order())) + ("ask",)
_ATTENTION = frozenset({"prohibited", "reserved", "human"})


def _worst(verdicts: list[str]) -> str:
    """The strictest verdict present, or ``clear`` when none of interest is."""
    present = set(verdicts)
    for v in _SEVERITY:
        if v in present:
            return v
    return "clear"


def _parent_path(path: str, all_paths: list[str]) -> str:
    """The registered workspace whose path is the longest proper prefix of
    ``path`` — the bus this one nests under. Empty when it is top level."""
    me = str(path).rstrip("/")
    best = ""
    for other in all_paths:
        o = str(other).rstrip("/")
        if o != me and me.startswith(o + "/") and len(o) > len(best):
            best = o
    return best


def console_snapshot(*, now: float, log_root: Optional[Path] = None,
                     attention_limit: int = 20) -> dict[str, Any]:
    """Aggregate every visible workspace into one rollup for the mixer.

    ``now`` resolves approval deadlines. ``attention_limit`` caps the attention
    list; anything dropped is counted in ``attention_overflow`` so a truncation
    never reads as "all clear"."""
    from .workspace_registry import list_known_workspaces
    from .governance_graph import governance_graph
    from .approvals import list_approvals

    lr = str(log_root) if log_root is not None else None
    workspaces = list_known_workspaces(log_root=log_root)
    all_paths = [w.get("path", "") for w in workspaces]

    buses: list[dict[str, Any]] = []
    for w in workspaces:
        path = w.get("path", "")
        try:
            g = governance_graph(path, log_root=lr)
        except Exception:
            # a folder that will not read is reported unreadable, never skipped
            # (a missing bus must not look like a clear one).
            buses.append({"path": path, "name": Path(path).name or path,
                          "unreadable": True, "exists": Path(path).is_dir(),
                          "parent": _parent_path(path, all_paths)})
            continue
        nodes = g.get("nodes") or []
        agents = [n for n in nodes if n.get("kind") == "agent"]
        held = [n for n in agents if n.get("status") not in (None, "active")]
        egress = [e for e in (g.get("edges") or []) if e.get("kind") == "egress"]
        worst = _worst([e.get("verdict", "") for e in egress])
        try:
            pend = list_approvals(path, now=now, state="pending", log_root=lr)
            pending = len(pend.get("approvals") or pend.get("items") or [])
        except Exception:
            pending = 0
        buses.append({
            "path": path,
            "name": Path(path).name or path,
            "parent": _parent_path(path, all_paths),
            "worst": worst,
            "pending": pending,
            "agents_total": len(agents),
            "agents_held": len(held),
            "unreadable": False,
            "exists": Path(path).is_dir(),
        })

    ranked = [b for b in buses
              if not b.get("unreadable")
              and (b.get("pending") or b.get("worst") in _ATTENTION)]
    ranked.sort(key=lambda b: (b["worst"] in _ATTENTION, b.get("pending", 0)),
                reverse=True)
    attention = [b["path"] for b in ranked[:attention_limit]]
    overflow = max(0, len(ranked) - attention_limit)

    return {
        "ok": True,
        "buses": buses,
        "count": len(buses),
        "attention": attention,
        "attention_overflow": overflow,
    }
