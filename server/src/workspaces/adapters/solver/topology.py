# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of Solver's canonical solver topology.
Internal by design: a seam re-export, not a console or MCP surface.

This is one of the sanctioned places that import ``loomground_solver``
directly; everything else in ``workspaces`` reaches these names through
here (or through the top-level compatibility facade at
``workspaces.solver_topology``), never through the upstream package itself.
Bundles the ``_projection`` helpers alongside ``topology`` because the
top-level facade has always exposed both from one module.
"""

from loomground_solver._projection import _edge, _node
from loomground_solver.topology import *  # noqa: F401,F403
from loomground_solver.topology import (
    DEP_RELATIONS,
    HUMAN_GRADES,
    NODE_KINDS,
    Dep,
    SolverNode,
    build_topology,
    topo_order,
    validate_topology,
)

__all__ = [
    "DEP_RELATIONS", "NODE_KINDS", "HUMAN_GRADES", "SolverNode", "Dep",
    "validate_topology", "topo_order", "build_topology", "_edge", "_node",
]
