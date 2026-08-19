# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim — the jurisdiction / instrument packs now live in the vertical plane.

What a BGH masthead looks like, which roles the AI Act binds, or that "dpia"
means an assessment are facts about *particular* legal systems and instruments —
CARRIED DATA, never engine. The governance engines (genre router, judgment
reading, duty identification, map, ask) stay jurisdiction-NEUTRAL and walk the
registries. Those registries, and the shipped packs (``de-eu`` courts + judgment
markers, ``en-uk`` judgment markers, the ``eu-ai`` instrument vocabulary), now
live in ``loomground-vertical`` (``loomground_vertical.jurisdiction_packs``) and
are consumed here through the ``adapters.vertical`` seam.

Adding a jurisdiction still takes no engine change — register a pack:

    from rvnd import jurisdiction_packs as JP
    JP.register_court_pack("us", [(r"\\bSupreme Court of the United States\\b …",
                                   "SCOTUS", "Supreme Court of the United States",
                                   "court-judgment", 1, "BINDING")])

This module keeps that historical import path (RVND's ``judgment_reading``,
``governance_map`` and ``governance_ask`` read it, and it is the surface an
external vertical registers itself through). It defines no packs and no registry
of its own; the stateful stores are the plane's, and there is one of each.
"""
from __future__ import annotations

from .adapters.vertical import jurisdiction_packs as _jp

register_court_pack = _jp.register_court_pack
court_entries = _jp.court_entries
register_judgment_markers = _jp.register_judgment_markers
judgment_marker_patterns = _jp.judgment_marker_patterns
register_instrument_vocab = _jp.register_instrument_vocab
role_steps = _jp.role_steps
room_cues_extra = _jp.room_cues_extra
ask_synonyms = _jp.ask_synonyms

__all__ = [
    "register_court_pack", "court_entries",
    "register_judgment_markers", "judgment_marker_patterns",
    "register_instrument_vocab", "role_steps", "room_cues_extra", "ask_synonyms",
]
