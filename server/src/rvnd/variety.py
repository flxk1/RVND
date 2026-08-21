# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Requisite variety, measured — Ashby's Law as a concrete property.

Ashby (*An Introduction to Cybernetics*, 1956): only variety can destroy
variety; a regulator must command at least as much variety as the
disturbances it must absorb. This module makes that measurable for solvers.

A *problem*'s variety is the set of distinct issue-type fingerprints in the
input. A *regulator*'s variety is the set of issue types it can address:

  * a FIXED flow (n8n / Power Automate) has a constant covered set — the
    issue types its authored steps handle, independent of the input;
  * a DERIVED solver covers one issue type per detected token, plus any
    issue type a recalled solver supplies — so its variety scales with the
    problem.

``requisite_variety`` is then coverage: the regulator meets requisite variety
iff its covered set ⊇ the problem's issue types. The uncovered set is reported
so governance sees exactly which disturbances have no regulator — the gap,
not just a boolean. No model in the loop; deterministic.
"""
from __future__ import annotations

from typing import Any, Callable, Optional


def problem_variety(tokens: list) -> dict[str, Any]:
    """The variety a problem presents: distinct issue types among its tokens."""
    types = sorted({t.issue_type for t in tokens if getattr(t, "issue_type", "")})
    return {"variety": len(types), "issue_types": types}


def fixed_flow_regulator(step_issue_types: list[str]) -> dict[str, Any]:
    """A fixed flow as a regulator: a constant covered set. Its variety does
    not depend on the input it faces — the defining limitation Ashby's Law
    exposes."""
    covered = sorted(set(step_issue_types))
    return {"kind": "fixed", "covered": covered, "variety": len(covered)}


def derived_regulator(tokens: list, *,
                      recall_fn: Optional[Callable[[Any], list]] = None
                      ) -> dict[str, Any]:
    """A derived solver as a regulator: it instantiates one solver node per
    detected issue type (variety scales with the problem), and any issue type
    a recalled verified solver supplies is covered too. The covered set is a
    function OF the input — variety matched to disturbance."""
    covered = {t.issue_type for t in tokens if getattr(t, "issue_type", "")}
    if recall_fn is not None:
        for t in tokens:
            hits = recall_fn(t) or []
            if any(h.get("evidence", 0) >= 1 for h in hits):
                covered.add(t.issue_type)
    cov = sorted(covered)
    return {"kind": "derived", "covered": cov, "variety": len(cov)}


def variety_check(covered_types: list[str],
                  problem_types: list[str]) -> dict[str, Any]:
    """JSON-clean Ashby coverage check for the MCP facade: covered issue
    types vs the problem's issue types, no token objects. Reports ok, the
    variety counts, and the uncovered disturbances."""
    covered = sorted({t for t in covered_types if t})
    needed = sorted({t for t in problem_types if t})
    uncovered = [t for t in needed if t not in set(covered)]
    return {
        "ok": not uncovered,
        "regulator_variety": len(covered),
        "problem_variety": len(needed),
        "covered": [t for t in needed if t in set(covered)],
        "uncovered": uncovered,
    }


def requisite_variety(regulator: dict[str, Any], tokens: list) -> dict[str, Any]:
    """Ashby coverage check: regulator covered set vs problem issue types.
    Reports ok, the regulator variety, covered, and the UNCOVERED disturbances
    (issue types with no regulator) — the governance-relevant gap."""
    covered = set(regulator.get("covered", []))
    needed = {t.issue_type for t in tokens if getattr(t, "issue_type", "")}
    uncovered = sorted(needed - covered)
    return {
        "ok": not uncovered,
        "variety": regulator.get("variety", len(covered)),
        "problem_variety": len(needed),
        "covered": sorted(needed & covered),
        "uncovered": uncovered,
    }
