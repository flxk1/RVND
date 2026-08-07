# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Thin shim — the universal source-class map now lives in the legal plane
(``loomground_legal.source_classes``) and is consumed here through the legal
seam (``adapters.legal``).

This module keeps the historical ``workspaces.source_classes`` import path for
every consumer (``from .source_classes import SourceClass`` / ``Effect`` / …); it
defines none of the taxonomy, effect ceilings, relation vocabulary, or the
SC-2/SC-3 invariants itself. See the plane module for the doctrine.
"""
from __future__ import annotations

from .adapters.legal import source_classes as _sc

Effect = _sc.Effect
SourceClass = _sc.SourceClass
Relation = _sc.Relation
VOCABULARY = _sc.VOCABULARY
SourceFinding = _sc.SourceFinding
is_relation = _sc.is_relation
max_effect = _sc.max_effect
self_executes = _sc.self_executes
requires_incorporation = _sc.requires_incorporation
check_source = _sc.check_source
catalogue = _sc.catalogue

__all__ = [
    "Effect", "SourceClass", "Relation", "VOCABULARY", "SourceFinding",
    "is_relation", "max_effect", "self_executes", "requires_incorporation",
    "check_source", "catalogue",
]
