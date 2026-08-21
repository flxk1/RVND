# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-legal's corpus validator — RVND owns neither.

RVND's parallel corpus-validation pass (the reachability / authority-tier /
currency / provenance scan, the official-host allow-list, and the 5-tier
authority hierarchy) is RETIRED. It now lives in ``loomground-legal`` and is
consumed through the ``adapters/legal`` seam (the workspaces boundary rule
confines every upstream import there).

This module re-exports ``validate_corpus`` (over a ``WorldMap``) and the
authority-tier labels behind their historical names, and keeps **one thin RVND
wrapper local**: ``validate_registry``, which validates a *persisted* corpus by
materialising an ``EntityRegistry`` into a ``WorldMap`` — folder runtime, not
domain logic.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..adapters.legal import (
    validate_corpus,
    Finding,
    PRIMARY_LAW,
    INSTITUTIONAL,
    SUPPORTING,
    SECONDARY,
    GENERAL,
)

__all__ = ["validate_corpus", "validate_registry", "Finding",
           "PRIMARY_LAW", "INSTITUTIONAL", "SUPPORTING", "SECONDARY", "GENERAL"]


def validate_registry(registry, *, probe: Optional[Callable[[str], bool]] = None) -> dict:
    """Validate a persisted corpus (``legal_corpus.EntityRegistry``). RVND
    folder-runtime wrapper: materialise the registry into a ``WorldMap`` and hand
    it to the consumed validator."""
    return validate_corpus(registry.to_world_map(), probe=probe)
