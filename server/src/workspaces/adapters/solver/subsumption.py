# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of Solver's canonical subsumption engine.
Internal by design: a seam re-export, not a console or MCP surface.

This is one of the sanctioned places that import ``loomground_solver``
directly; everything else in ``workspaces`` reaches these names through
here, never through the upstream package itself.

Two layers are re-exported:

  * the flat, closed-world classifier (:mod:`loomground_solver.subsumption`) —
    ``holds`` / ``subsume`` / ``applicable_rules`` / ``to_norms`` / ``neg`` and
    the ``Rule`` / ``Subsumption`` types — the INTENTIONAL / open-textured
    dimension; and
  * the three-valued, cross-dimensional router
    (:mod:`loomground_solver.cross_subsumption`) — ``Condition`` / ``FactSpace``
    → ``subsume_across`` → :class:`Verdict` (SATISFIED / NOT_SATISFIED / OPEN),
    with ``fold_verdicts`` / ``subsume_antecedent`` for aggregation. This is the
    engine RVND consumes for structural is-a matching (the matcher) and the
    domain-join subsumption in ``use_case_nd`` — it must never re-grow a
    reachability/closed-world evaluator of its own.
"""

from loomground_solver.subsumption import (  # noqa: F401
    Judge,
    Rule,
    Subsumption,
    applicable_rules,
    holds,
    neg,
    subsume,
    to_norms,
)
from loomground_solver.cross_subsumption import (  # noqa: F401
    AntecedentVerdict,
    Condition,
    DimVerdict,
    FactSpace,
    Verdict,
    fold_verdicts,
    subsume_across,
    subsume_antecedent,
)

__all__ = [
    # flat closed-world classifier
    "Judge", "Rule", "Subsumption",
    "applicable_rules", "holds", "neg", "subsume", "to_norms",
    # three-valued cross-dimensional router
    "AntecedentVerdict", "Condition", "DimVerdict", "FactSpace", "Verdict",
    "fold_verdicts", "subsume_across", "subsume_antecedent",
]
