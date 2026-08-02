# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND policy adapter over Solver's governance-neutral reasoning contract."""

from workspaces.adapters.solver.contract import *  # noqa: F401,F403
from workspaces.adapters.solver.contract import (
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


def check_folder_case(case: dict, folder_path, *, stake: bool = False,
                      personal: bool = False) -> ContractReport:
    """Apply Solver's contract using the folder's RVND oversight policy."""
    from .adapters.solver import check_with_rvnd_governance
    return check_with_rvnd_governance(
        case,
        folder_path,
        stake=stake,
        personal=personal,
    )


__all__ = [
    "LEVELS", "level_rank", "required_oversight", "INFORMATION_FORMS",
    "oversight_form", "PROFILES", "DEFAULT_PROFILE", "check_evidence",
    "check_warrants", "check_resolution", "check_judgment_floor", "check_actions",
    "check_norm_completeness", "check_profile", "check_case", "ReasoningViolation",
    "gate", "check_folder_case", "check_export",
]
