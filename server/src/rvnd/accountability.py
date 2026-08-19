# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Accountability guard — the boundary between essential and arbitrary variety.

A solver may legitimately be complex: a hard problem warrants many nodes, and
a structure of any size is accountable as long as every node is WARRANTED (it
names why it exists) and the structure is REPRODUCIBLE (same fingerprints
always yield the same topology). What governance must reject is not complexity
but ARBITRARINESS — a node that just appears with no warrant, or a structure
that differs across runs of the same input. There, responsibility cannot be
attributed: you cannot say who decided this, or why this and not that.

Grounding: Brooks' essential vs accidental complexity (*No Silver Bullet*);
the administrative-law non-arbitrariness standard (a decision must be reasoned
and reproducible, not capricious). This is the reasoning contract's
"no unwarranted step" (Toulmin warrant) lifted from the step to the topology.

The guard reports COMPLEXITY (node count — informational, never a violation)
separately from ACCOUNTABILITY (warrant + determinism), so the two are never
conflated. No model in the loop; deterministic.

Internal by design: a governance signal consumed by gates and projections; it has no operator surface of its own.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

#: legitimate reasons a solver node may exist. A node's warrant must be one of
#: these; an empty warrant is arbitrary variety, an unknown one is suspect.
WARRANT_KINDS = ("detected", "recalled", "dependency", "norm", "root")


def _structure_key(nodes: list) -> tuple:
    """Identity of a topology's node set for reproducibility comparison —
    order-independent, so two runs differ only if the STRUCTURE differs."""
    return tuple(sorted(n.id for n in nodes))


def audit_accountability(
    nodes: list,
    *,
    reproduce_fn: Optional[Callable[[], list]] = None,
) -> dict[str, Any]:
    """Audit a solver structure for accountable variety.

    Each node must carry a warrant from :data:`WARRANT_KINDS`. If
    ``reproduce_fn`` is given, it is called twice and the resulting structures
    compared — a difference is the core arbitrariness failure. Returns
    complexity (node count) apart from accountability, plus the findings."""
    findings: list[dict[str, Any]] = []
    warranted = 0
    for n in nodes:
        w = getattr(n, "warrant", "") or ""
        if not w:
            findings.append({"kind": "unwarranted-variety", "id": n.id})
        elif w not in WARRANT_KINDS:
            findings.append({"kind": "unknown-warrant", "id": n.id,
                             "value": w})
        else:
            warranted += 1

    if reproduce_fn is not None:
        a = _structure_key(reproduce_fn())
        b = _structure_key(reproduce_fn())
        if a != b:
            findings.append({"kind": "nondeterministic-structure",
                             "id": "topology"})

    return {
        "accountable": not findings,
        "complexity": len(nodes),          # informational — never a violation
        "warranted": warranted,
        "findings": findings,
    }


def account_check(warrants: list[str]) -> dict[str, Any]:
    """JSON-clean accountability audit for the MCP facade: given the warrant
    of each node (a string from WARRANT_KINDS, or empty = arbitrary), report
    whether the variety is accountable, the complexity (count), and the
    findings — without needing SolverNode objects."""
    findings: list[dict[str, Any]] = []
    warranted = 0
    for i, w in enumerate(warrants):
        w = (w or "").strip()
        if not w:
            findings.append({"kind": "unwarranted-variety", "id": f"node{i}"})
        elif w not in WARRANT_KINDS:
            findings.append({"kind": "unknown-warrant", "id": f"node{i}",
                             "value": w})
        else:
            warranted += 1
    return {"accountable": not findings, "complexity": len(warrants),
            "warranted": warranted, "findings": findings}


def amplification_curve(base_types: list[str],
                        retained: list[list[str]]) -> list[int]:
    """Measure variety built up by retention: the covered-variety count after
    the base detectors, then after each batch of retained (recalled) solvers
    is added. A rising curve is the regulator amplifying its own variety by
    learning — each added unit warranted 'recalled'."""
    covered = set(base_types)
    curve = [len(covered)]
    for batch in retained:
        covered |= set(batch)
        curve.append(len(covered))
    return curve
