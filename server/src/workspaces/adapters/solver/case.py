# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of Solver's pure case-record subset.
Internal by design: a seam re-export, not a console or MCP surface.

This is one of the sanctioned places that import ``loomground_solver``
directly. RVND's problem-solving KG (``workspaces.problem_kg``) reaches the
pure ``Ground``/``Fact``/``CaseRecord``/``project_pairs`` primitives through
here, and keeps only the corpus-coupled ``build_case`` orchestration on top —
exactly the split ``loomground_solver.case`` itself prescribes: the pure subset
lives in the package; corpus-coupled assembly (registry/extractor) lives in the
host, behind injected ports.

``_norm_spans_for`` is a private helper in ``loomground_solver.case`` that a few
host consumers (``reasoning_walker``, the policy-resolve tests) still reach as
``problem_kg._norm_spans_for``; it is re-exported here so that access keeps
working through the seam rather than through a local re-grown copy.
"""

from loomground_solver.case import (  # noqa: F401
    CaseRecord,
    Fact,
    Ground,
    _norm_spans_for,
    project_pairs,
)

__all__ = ["Ground", "Fact", "CaseRecord", "project_pairs", "_norm_spans_for"]
