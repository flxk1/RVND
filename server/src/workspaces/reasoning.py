# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compatibility facade for Solver's canonical path reasoning implementation.

Internal by design: an import-compatibility shim, not a console or MCP surface.
"""

from workspaces.adapters.solver.reasoning import *  # noqa: F401,F403
from workspaces.adapters.solver.reasoning import Edge, Inference, compose_paths, extract_edges

__all__ = ["Edge", "Inference", "extract_edges", "compose_paths"]
