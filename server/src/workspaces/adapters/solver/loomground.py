# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of the independent Loomground Solver runtime.
Internal by design: a seam re-export, not a console or MCP surface.

This is one of the sanctioned places that import ``loomground_solver``
directly; everything else in ``workspaces`` reaches these names through
here (or through the top-level compatibility facade at
``workspaces.loomground_lang``), never through the upstream package itself.
RVND owns application policy and integration; parsing, validation,
projection, and evaluation are supplied by ``loomground-solver``.
"""
from __future__ import annotations

from loomground_solver.loomground import *  # noqa: F401,F403
from loomground_solver.loomground import (
    LANGUAGE_VERSION,
    VERDICTS,
    _guard_holds,
    _has_cycle,
    grade_meets,
)
