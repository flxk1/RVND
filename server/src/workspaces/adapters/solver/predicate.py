# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of Solver's structured predicate implementation.
Internal by design: a seam re-export, not a console or MCP surface.

This is one of the sanctioned places that import ``loomground_solver``
directly; everything else in ``workspaces`` reaches these names through
here (or through the top-level compatibility facade at
``workspaces.predicate``), never through the upstream package itself.
"""

from loomground_solver.predicate import *  # noqa: F401,F403
from loomground_solver.predicate import (
    PREDICATE_CONFIDENCE_FLOOR,
    Predicate,
    PredicateError,
    attach_predicates,
    parse_condition,
)

__all__ = [
    "Predicate", "PredicateError", "PREDICATE_CONFIDENCE_FLOOR",
    "parse_condition", "attach_predicates",
]
