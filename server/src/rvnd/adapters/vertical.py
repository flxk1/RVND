# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND adapter seam over loomground-vertical — the vertical-registration
surface; internal by design.

The workspaces boundary rule confines every direct import of an upstream
Loomground package to the ``adapters/`` seam (see
``tests/test_adapter_boundary.py``). This module is that seam for the **vertical
plane**: the three things a domain vertical (music rights, medical devices,
employment law) needs in order to plug into the stack, none of which is engine
work —

  * the **subject vocabulary** — what the facets of a subject are and what their
    values mean (``FacetSpec`` / ``DomainVocabulary`` / ``SubjectCard`` and the
    vocabulary registry);
  * the **jurisdiction pack** — a legal system's courts, judgment markers and
    instrument vocabulary, registered rather than hardcoded;
  * the **requirements house** — obligation and artifact atoms arranged into
    rooms.

These were carried DATA plus small registries all along: pure stdlib, importing
nothing from the engines, while the engines (genre router, judgment reading,
duty identification, map, ask) walk the registries. They now live in
``loomground-vertical`` under Apache-2.0, and RVND consumes them here. The
historical import paths — ``workspaces.subject_card``,
``workspaces.jurisdiction_packs``, ``workspaces.requirements_house`` — remain as
thin shims over this seam, so internal callers and the external verticals that
register through them are unchanged.

**The one asymmetry: ``build_house_from_text`` does NOT come from the plane.**
Turning instrument TEXT into obligation and artifact atoms runs RVND's
extraction pipeline (``nd_routing`` classification, ``deontic_facets``,
``instrument_obligation_extractor``, ``crossref_extractor``, ``applicability``).
That is engine work, and a copy of it out in the vertical plane would be exactly
the parallel structure this split exists to avoid. The plane's ``build_house``
assembles a house from atoms it is HANDED; RVND keeps the single implementation
of the extraction, in ``workspaces.requirements_house``.

The registries are stateful module-level stores, so the submodules themselves
are re-exported alongside the functions: a caller that needs to see (or, in a
test, isolate) a registry reaches the ONE module that owns it, never a copy.
"""
from __future__ import annotations

from loomground_vertical import (
    jurisdiction_packs,
    requirements_house,
    subject_card,
)

# -- subject vocabulary ------------------------------------------------------
FacetSpec = subject_card.FacetSpec
DomainVocabulary = subject_card.DomainVocabulary
SubjectCard = subject_card.SubjectCard
UNKNOWN = subject_card.UNKNOWN
AI_ACT_VOCAB = subject_card.AI_ACT_VOCAB
NEUTRAL_VOCAB = subject_card.NEUTRAL_VOCAB
register_vocabulary = subject_card.register_vocabulary
get_vocabulary = subject_card.get_vocabulary
make_card = subject_card.make_card

# -- jurisdiction / instrument packs ----------------------------------------
register_court_pack = jurisdiction_packs.register_court_pack
court_entries = jurisdiction_packs.court_entries
register_judgment_markers = jurisdiction_packs.register_judgment_markers
judgment_marker_patterns = jurisdiction_packs.judgment_marker_patterns
register_instrument_vocab = jurisdiction_packs.register_instrument_vocab
role_steps = jurisdiction_packs.role_steps
room_cues_extra = jurisdiction_packs.room_cues_extra
ask_synonyms = jurisdiction_packs.ask_synonyms

# -- requirements house (assembly only — see the asymmetry note above) -------
Room = requirements_house.Room
RequirementsHouse = requirements_house.RequirementsHouse
build_house = requirements_house.build_house

__all__ = [
    # the registry-owning modules themselves
    "subject_card", "jurisdiction_packs", "requirements_house",
    # subject vocabulary
    "FacetSpec", "DomainVocabulary", "SubjectCard", "UNKNOWN",
    "AI_ACT_VOCAB", "NEUTRAL_VOCAB",
    "register_vocabulary", "get_vocabulary", "make_card",
    # jurisdiction packs
    "register_court_pack", "court_entries",
    "register_judgment_markers", "judgment_marker_patterns",
    "register_instrument_vocab", "role_steps", "room_cues_extra", "ask_synonyms",
    # requirements house
    "Room", "RequirementsHouse", "build_house",
]
