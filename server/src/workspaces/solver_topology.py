# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compatibility facade for Solver's canonical solver topology; internal by design."""

from workspaces.adapters.solver.topology import _edge, _node
from workspaces.adapters.solver.topology import *  # noqa: F401,F403
from workspaces.adapters.solver.topology import (
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
