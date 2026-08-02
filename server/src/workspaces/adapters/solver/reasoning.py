# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of Solver's canonical path reasoning implementation.
Internal by design: a seam re-export, not a console or MCP surface.

This is one of the sanctioned places that import ``loomground_solver``
directly; everything else in ``workspaces`` reaches these names through
here (or through the top-level compatibility facade at
``workspaces.reasoning``), never through the upstream package itself.
"""

from loomground_solver.reasoning import *  # noqa: F401,F403
from loomground_solver.reasoning import Edge, Inference, compose_paths, extract_edges

__all__ = ["Edge", "Inference", "extract_edges", "compose_paths"]
