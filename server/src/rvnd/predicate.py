# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compatibility facade for Solver's structured predicate implementation.

Internal by design: an import-compatibility shim, not a console or MCP surface.
"""

from rvnd.adapters.solver.predicate import *  # noqa: F401,F403
from rvnd.adapters.solver.predicate import (
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
