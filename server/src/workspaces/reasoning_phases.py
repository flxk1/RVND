# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compatibility facade for Solver's canonical reasoning phase curriculum."""

from workspaces.adapters.solver.phases import *  # noqa: F401,F403
from workspaces.adapters.solver.phases import PHASE_ORDER, all_briefs, brief, curriculum

__all__ = ["PHASE_ORDER", "brief", "curriculum", "all_briefs"]
