# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-norm's subsumption validator — RVND owns neither.

RVND's parallel two-layer (universal + regional) norm-theory validator is
RETIRED. The validator now lives in ``loomground-norm``
(``loomground_norm.subsumption_validator``); this module re-exports it behind the
historical import names (``Finding``, ``ValidationReport``, ``validate``) through
the ``adapters/norm`` seam.

RVND's active jurisdiction pack (``legal_systems.get``) is injected into the
plane's ``LegalSystemPack`` port by the seam, so ``validate(sub,
legal_system="DE")`` keeps its historical shape and default. No validation logic
lives here. Callers are unchanged; this shim is deleted once they migrate to the
plane directly.
"""
from __future__ import annotations

from .adapters.norm import (
    Finding,
    ValidationReport,
    validate,
)

__all__ = ["Finding", "ValidationReport", "validate"]
