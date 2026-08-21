# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of Solver's governance-neutral reasoning contract.
Internal by design: a seam re-export, not a console or MCP surface.

This is one of the sanctioned places that import ``loomground_solver``
directly; everything else in ``workspaces`` reaches these names through
here (or through the top-level compatibility facade at
``rvnd.reasoning_contract``), never through the upstream package itself.
RVND's own policy layer on top of this contract (``check_folder_case``)
stays in the facade — it is application glue, not a Solver re-export.
"""

from loomground_solver.contract import *  # noqa: F401,F403
from loomground_solver.contract import (
    DEFAULT_PROFILE,
    INFORMATION_FORMS,
    LEVELS,
    PROFILES,
    ContractReport,
    ReasoningViolation,
    check_actions,
    check_case,
    check_evidence,
    check_export,
    check_judgment_floor,
    check_norm_completeness,
    check_profile,
    check_resolution,
    check_warrants,
    gate,
    level_rank,
    oversight_form,
    required_oversight,
)

__all__ = [
    "LEVELS", "level_rank", "required_oversight", "INFORMATION_FORMS",
    "oversight_form", "PROFILES", "DEFAULT_PROFILE", "check_evidence",
    "check_warrants", "check_resolution", "check_judgment_floor", "check_actions",
    "check_norm_completeness", "check_profile", "check_case", "ReasoningViolation",
    "gate", "check_export", "ContractReport",
]
