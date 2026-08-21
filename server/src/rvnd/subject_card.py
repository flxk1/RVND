# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim — the subject card now lives in the vertical plane.

The structured description of the thing being assessed (``SubjectCard``), the
facet grammar it is described in (``FacetSpec`` / ``DomainVocabulary``), the
shipped vocabularies (``NEUTRAL_VOCAB``, ``AI_ACT_VOCAB``) and the vocabulary
registry are a VERTICAL's surface, not engine code: pure data plus a small
registry, importing nothing from the engines. They now live in
``loomground-vertical`` (``loomground_vertical.subject_card``) and are consumed
here through the ``adapters.vertical`` seam.

This module keeps the historical ``workspaces.subject_card`` import path for
every consumer — ``matcher``, ``memo``, ``applicability``, ``card_store``,
``fact_intake``, ``use_case_intake``, ``music_domain``,
``workspace_legal_facade``, and the external verticals that register a
vocabulary through it. It defines none of the model, the vocabularies, or the
registry itself; there is exactly one registry, and it is the plane's.
"""
from __future__ import annotations

from .adapters.vertical import subject_card as _sc

FacetSpec = _sc.FacetSpec
DomainVocabulary = _sc.DomainVocabulary
SubjectCard = _sc.SubjectCard
UNKNOWN = _sc.UNKNOWN
AI_ACT_VOCAB = _sc.AI_ACT_VOCAB
NEUTRAL_VOCAB = _sc.NEUTRAL_VOCAB
register_vocabulary = _sc.register_vocabulary
get_vocabulary = _sc.get_vocabulary
make_card = _sc.make_card

__all__ = [
    "FacetSpec", "DomainVocabulary", "SubjectCard", "UNKNOWN",
    "AI_ACT_VOCAB", "NEUTRAL_VOCAB",
    "register_vocabulary", "get_vocabulary", "make_card",
]
