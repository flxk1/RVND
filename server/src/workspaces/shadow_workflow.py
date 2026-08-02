# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Shadow-workflow classification over recorded cross-workspace crossings.

`cross_workspace_read` gates each boundary crossing and records it on the target
workspace's signed chain; this is the pluggable layer that reads those records back
and asks a governance question the per-crossing gate cannot: *is the emergent
pattern of lateral data flow into this workspace a declared process, or a shadow
one?* A shadow workflow is an undeclared chain of workspace-to-workspace reads — each
individual crossing may have passed the gate, yet no declared workflow describes
the end-to-end flow, so nobody signed off on the shape of it.

Detective and read-only. It classifies what already happened from the chain; it
never blocks (the gate already did that per crossing) and never copies content.

Classification per distinct edge (source -> target, by role), from the LATEST
crossing's verdict:
  - ``blocked``        latest verdict NO-GO — the read was refused (recorded
                       as an attempt; worth surfacing as pressure on a boundary).
  - ``needs_signoff``  latest verdict CONDITIONAL — recorded but flagged; its
                       content must not be used downstream without human sign-off.
  - ``shadow``         GO crossing into a workspace that declares NO workflow at all —
                       pure emergent flow, nothing sanctions it.
  - ``review``         GO crossing into a workspace that DOES declare workflow(s) — a
                       governed process exists, but the current model cannot
                       auto-confirm this crossing belongs to it, so it is flagged
                       for human confirmation rather than asserted sanctioned.
We deliberately do not emit a ``sanctioned`` class: asserting sanction from the
data we have would be false comfort. Confirmation is a human act.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .workspace_lock import replay
from .workflows import list_workflows_for_folder

BLOCKED = "blocked"
NEEDS_SIGNOFF = "needs_signoff"
SHADOW = "shadow"
REVIEW = "review"


def _classify(last_verdict: str, has_declared: bool) -> str:
    if last_verdict == "NO-GO":
        return BLOCKED
    if last_verdict == "CONDITIONAL":
        return NEEDS_SIGNOFF
    return REVIEW if has_declared else SHADOW


def classify_shadow_workflows(folder: str | Path,
                              *,
                              high_fan_in: int = 3,
                              log_root: str | Path | None = None) -> dict[str, Any]:
    """Scan ``folder``'s signed chain for cross-workspace crossings and classify the
    emergent flow. Returns ``{folder, declared_workflows, edges, shadow,
    needs_signoff, blocked, review, fan_in, high_fan_in, summary}``.

    ``edges`` is one entry per distinct (source, role) with its crossing
    ``count``, ``last_verdict``, ``last_ts``, ``pair_count`` and ``class``.
    """
    resolved = str(Path(folder).expanduser().resolve())

    declared = []
    try:
        declared = [getattr(w, "name", "") for w in
                    list_workflows_for_folder(resolved, log_root=log_root)]
    except Exception:
        declared = []
    has_declared = bool(declared)

    # collect cross-workspace-read events from the chain
    agg: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "last_verdict": "", "last_ts": 0,
                 "pair_count": 0, "verdicts": []})
    try:
        events = list(replay(resolved, log_root=log_root))
    except Exception:
        events = []
    for e in events:
        extra = getattr(e, "extra", None) or {}
        if extra.get("kind") != "cross-workspace-read":
            continue
        key = (extra.get("source", ""), extra.get("role", ""))
        a = agg[key]
        a["count"] += 1
        v = extra.get("verdict", "")
        a["verdicts"].append(v)
        ts = getattr(e, "ts", 0) or 0
        if ts >= a["last_ts"]:
            a["last_ts"] = ts
            a["last_verdict"] = v
        a["pair_count"] = max(a["pair_count"], len(extra.get("source_pair_ids", []) or []))

    edges: list[dict[str, Any]] = []
    for (source, role), a in agg.items():
        cls = _classify(a["last_verdict"], has_declared)
        edges.append({
            "source": source, "role": role, "count": a["count"],
            "last_verdict": a["last_verdict"], "last_ts": a["last_ts"],
            "pair_count": a["pair_count"], "class": cls,
        })
    edges.sort(key=lambda d: (d["class"] != SHADOW, -d["count"], d["source"]))

    by = lambda c: [e for e in edges if e["class"] == c]  # noqa: E731
    # fan-in = distinct GO/CONDITIONAL sources feeding this workspace
    feeding = {e["source"] for e in edges if e["class"] in (SHADOW, REVIEW, NEEDS_SIGNOFF)}
    fan_in = len(feeding)

    shadow, blocked, signoff, review = by(SHADOW), by(BLOCKED), by(NEEDS_SIGNOFF), by(REVIEW)
    parts = []
    if shadow:  parts.append(f"{len(shadow)} shadow")
    if signoff: parts.append(f"{len(signoff)} need sign-off")
    if review:  parts.append(f"{len(review)} to review")
    if blocked: parts.append(f"{len(blocked)} blocked")
    if fan_in >= high_fan_in:
        parts.append(f"high fan-in ({fan_in} sources)")
    summary = ("no cross-workspace crossings recorded" if not edges
               else "; ".join(parts) or "all crossings accounted for")

    return {
        "folder": resolved,
        "declared_workflows": declared,
        "edges": edges,
        "shadow": shadow,
        "needs_signoff": signoff,
        "blocked": blocked,
        "review": review,
        "fan_in": fan_in,
        "high_fan_in": fan_in >= high_fan_in,
        "summary": summary,
    }
