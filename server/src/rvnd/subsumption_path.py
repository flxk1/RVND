# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-norm's subsumption path — RVND owns neither.

RVND's parallel multi-hop subsumption assembler is RETIRED. The chain builder
(Norm -> Tatbestand -> Ausnahme -> Auslegung -> Subsumtion -> Ergebnis) with its
five gaps surfaced now lives in ``loomground-norm``
(``loomground_norm.subsumption_path``); this module re-exports that surface
behind the historical import names (``Step``, ``Gap``, ``Subsumption``,
``build``, ``ROLES``, ``REQUIRED_ROLES``) through the ``adapters/norm`` seam.

No path-assembly logic lives here. Callers are unchanged; this shim is deleted
once they migrate to the plane directly.
"""
from __future__ import annotations

from .adapters.norm import (
    Step,
    Gap,
    Subsumption,
    build,
    ROLES,
    REQUIRED_ROLES,
)

__all__ = ["Step", "Gap", "Subsumption", "build", "ROLES", "REQUIRED_ROLES"]
