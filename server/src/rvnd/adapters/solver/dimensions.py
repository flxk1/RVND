# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of Solver's canonical 5D reasoning vocabulary.
Internal by design: a seam re-export, not a console or MCP surface.

This is one of the sanctioned places that import ``loomground_solver``
directly; everything else in ``workspaces`` reaches these names through
here (or through the top-level compatibility facade at
``rvnd.dimensions``), never through the upstream package itself.
"""

from loomground_solver.dimensions import *  # noqa: F401,F403
from loomground_solver.dimensions import (
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
