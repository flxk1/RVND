# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND compatibility facade for Solver's configurable norm contract."""

from rvnd.adapters.solver.norm_contract import *  # noqa: F401,F403
from rvnd.adapters.solver.norm_contract import (
    CONFIDENCE_FLOOR,
    DISCRETION_MODALS,
    ContractReport,
    ContractViolation,
    Finding,
    Level,
    check_applicability,
    check_authority,
    check_collision,
    check_confidence,
    check_deontic,
    check_exception,
    check_incident_vocabulary,
    check_jurisdiction,
    check_pair,
    check_predicate_floor,
    check_provenance,
    check_temporal,
    check_typed_dates,
    enforce,
    gate,
)

__all__ = [
    "CONFIDENCE_FLOOR", "DISCRETION_MODALS", "Level", "Finding", "ContractReport",
    "check_provenance", "check_temporal", "check_applicability", "check_deontic",
    "check_exception", "check_authority", "check_jurisdiction", "check_confidence",
    "check_collision", "check_typed_dates", "check_predicate_floor",
    "check_incident_vocabulary", "check_pair", "enforce", "ContractViolation", "gate",
]
