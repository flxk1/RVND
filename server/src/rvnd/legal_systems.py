# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Thin shim — the switchable jurisdiction-family packs (DE/EU/UK/US) and the
applicable-law resolver now live in the legal plane
(``loomground_legal.legal_systems``), consumed here through the legal seam
(``adapters.legal``). The source-class primitives this module historically
re-exposed come through the same seam.

Keeps the historical ``rvnd.legal_systems`` import path (consumers use it
as a module: ``legal_systems.get(...)`` / ``.applicable_law(...)`` / ``.DEFAULT``);
it defines no pack, registry, or resolver itself.
"""
from __future__ import annotations

from .adapters.legal import legal_systems as _ls, source_classes as _sc

# applicable-law surface (the plane's legal_systems module)
LegalSystem = _ls.LegalSystem
SourceEntry = _ls.SourceEntry
SourceRelation = _ls.SourceRelation
ApplicableLaw = _ls.ApplicableLaw
get = _ls.get
available = _ls.available
register = _ls.register
DEFAULT = _ls.DEFAULT
applicable_systems = _ls.applicable_systems
applicable_law = _ls.applicable_law

# source-class primitives this module historically re-exposed (via its old
# ``from .source_classes import …``); consumers read them off the module alias.
Effect = _sc.Effect
Relation = _sc.Relation
SourceClass = _sc.SourceClass
max_effect = _sc.max_effect
self_executes = _sc.self_executes
requires_incorporation = _sc.requires_incorporation

__all__ = [
    "LegalSystem", "SourceEntry", "SourceRelation", "ApplicableLaw",
    "get", "available", "register", "DEFAULT",
    "applicable_systems", "applicable_law",
    "Effect", "Relation", "SourceClass", "max_effect", "self_executes",
    "requires_incorporation",
]
