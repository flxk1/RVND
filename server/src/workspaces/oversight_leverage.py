# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Oversight leverage, measured — the thesis made falsifiable.

Thesis: Workspaces scales human oversight in a responsible format. The defensible
form has two halves that must hold together:

  * LEVERAGE — a human closure on a problem shape governs every future
    instance of that shape via recall. The reusable asset base is the count
    of distinct shapes a human has closed. A shape closed more than once is
    leverage NOT captured — the human repeating themselves where reuse should
    have served.
  * INTEGRITY — the binding risk is automation bias (Bainbridge, *Ironies of
    Automation*, 1983): as reuse grows, the human disengages and stops
    catching the rare failure. Reuse WITHOUT human re-sampling means the
    automation is unverified and oversight quality may be decaying silently.

These are measurements, not guarantees. ``leverage_report`` measures the
POTENTIAL leverage in the case memory's structure (realised reuse requires
recall logging, not yet recorded). ``sampling_adequacy`` makes the integrity
condition checkable: responsible scaling requires a non-zero sampling rate.
Deterministic; no model in the loop.
"""
from __future__ import annotations

from typing import Any


def _shape_key(fp: dict[str, Any]) -> tuple:
    """Stable identity of a problem shape for grouping closures."""
    rooms = tuple(sorted(fp.get("rooms", []) or []))
    return (fp.get("issue_type", ""), fp.get("profile", ""), rooms)


def leverage_report(edges: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure the leverage structure of a folder's human-closed case memory.

    ``edges`` are solves-edges (each already human-closed by construction).
    Reports the closures, the distinct shapes governed (the reusable asset
    base), and redundant closures (same shape closed again — uncaptured
    leverage). Declares it measures POTENTIAL, not realised, reuse."""
    shapes: dict[tuple, int] = {}
    for e in edges:
        k = _shape_key(e.get("fingerprint") or {})
        shapes[k] = shapes.get(k, 0) + 1
    closures = len(edges)
    governed = len(shapes)
    return {
        "human_closures": closures,
        "shapes_governed": governed,
        "redundant_closures": closures - governed,
        "basis": "potential leverage from memory structure; realised reuse "
                 "requires recall logging (not yet recorded)",
    }


def sampling_adequacy(reuse_count: int, sampled_count: int,
                      floor: float = 0.0) -> dict[str, Any]:
    """The integrity condition, made checkable. As automated reuse grows, a
    fraction must be sampled back to a human or oversight quality cannot be
    verified (automation bias). Returns the sampling rate, whether it meets
    the floor, and an automation-bias flag when reuse happens with no
    sampling at all."""
    reuse = max(0, int(reuse_count))
    sampled = max(0, int(sampled_count))
    if reuse == 0:
        return {"sampling_rate": 1.0, "responsible": True, "flag": None,
                "note": "no reuse yet — nothing to sample"}
    rate = round(sampled / reuse, 4)
    if sampled == 0:
        return {"sampling_rate": 0.0, "responsible": False,
                "flag": "automation-bias",
                "note": "reuse without any human re-sampling — oversight "
                        "quality is unverifiable (Bainbridge)"}
    return {"sampling_rate": rate, "responsible": rate >= floor,
            "flag": None if rate >= floor else "below-sampling-floor",
            "note": f"sampling rate {rate} vs floor {floor}"}
