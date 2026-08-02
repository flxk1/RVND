# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compatibility facade for Solver's canonical 5D reasoning vocabulary.

Internal by design: an import-compatibility shim, not a console or MCP surface.
"""

from workspaces.adapters.solver.dimensions import *  # noqa: F401,F403
from workspaces.adapters.solver.dimensions import (
    COMPOSITION_TABLE,
    DEFAULT_DIMENSION,
    Dimension,
    classify_predicate,
    classify_query_dimension,
    compose,
    compose_weights,
)

__all__ = [
    "Dimension", "DEFAULT_DIMENSION", "COMPOSITION_TABLE", "compose",
    "compose_weights", "classify_query_dimension", "classify_predicate",
]
