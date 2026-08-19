# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of the independent Loomground Solver runtime.
Internal by design: a seam re-export, not a console or MCP surface.

This is one of the sanctioned places that import ``loomground_solver``
directly; everything else in ``workspaces`` reaches these names through
here (or through the top-level compatibility facade at
``rvnd.loomground_lang``), never through the upstream package itself.
RVND owns application policy and integration; parsing, validation,
projection, and evaluation are supplied by ``loomground-solver``.
"""
from __future__ import annotations

from loomground_solver.loomground import *  # noqa: F401,F403
from loomground_solver.loomground import (
    LANGUAGE_VERSION,  # noqa: F401  -- re-export
    VERDICTS,  # noqa: F401  -- re-export
    _guard_holds,  # noqa: F401  -- re-export: `import *` skips _private names, so this line is the export
    _has_cycle,  # noqa: F401  -- re-export: `import *` skips _private names, so this line is the export
    grade_meets,  # noqa: F401  -- re-export
)

# Declared so `import *` from this seam is an explicit surface rather than
# whatever happens to be bound. These are exactly the public names it
# already exported, so behaviour is unchanged.
__all__ = [
    "Any",
    "ApplyError",
    "CORD_TYPES",
    "GRADES",
    "GRADE_RANK",
    "GUARD_FIELDS",
    "GUARD_OPS",
    "LANGUAGE_VERSION",
    "MASTER",
    "NODE_CLASSES",
    "Optional",
    "ParseError",
    "RISKS",
    "RISK_RANK",
    "SUPPORTED_LANGUAGE_VERSIONS",
    "VERDICTS",
    "VERDICT_RANK",
    "annotations",
    "apply",
    "evaluate",
    "evaluate_log",
    "expand_racks",
    "grade_meets",
    "grade_rank",
    "language_version",
    "parse",
    "project",
    "re",
    "reason",
    "require_language_version",
    "to_netlist",
    "validate",
    "validate_token",
    "vocabulary",
]
