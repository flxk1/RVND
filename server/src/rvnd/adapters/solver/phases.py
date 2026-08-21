# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of Solver's canonical reasoning phase curriculum.
Internal by design: a seam re-export, not a console or MCP surface.

This is one of the sanctioned places that import ``loomground_solver``
directly; everything else in ``workspaces`` reaches these names through
here (or through the top-level compatibility facade at
``rvnd.reasoning_phases``), never through the upstream package itself.
"""

from loomground_solver.phases import *  # noqa: F401,F403
from loomground_solver.phases import PHASE_ORDER, all_briefs, brief, curriculum

__all__ = ["PHASE_ORDER", "brief", "curriculum", "all_briefs"]
