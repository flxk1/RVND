# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Jurisdiction / instrument packs — CARRIED DATA, not engine.

The governance engines (genre router, judgment reading, duty identification, map, ask) are
jurisdiction-NEUTRAL: they walk registries. What a BGH masthead looks like, which roles the
AI Act binds, or that "dpia" means an assessment are facts about *particular* legal systems
and instruments — RVND carries them, it does not author them. This module is where that
carried vocabulary lives, as plain data with registration seams, mirroring the repo's other
carried-data patterns (`subject_card.NEUTRAL_VOCAB`, the Loomground vocabulary, the trigger-
reader registry in `applicability`).

Shipped packs (the jurisdictions RVND has needed so far — a starting library, not a claim of
coverage): ``de-eu`` courts + judgment markers, ``en-uk`` judgment markers, the ``eu-ai``
instrument vocabulary. Add a jurisdiction by REGISTERING a pack — no engine change:

    from workspaces import jurisdiction_packs as JP
    JP.register_court_pack("us", [(r"\\bSupreme Court of the United States\\b|\\bU\\.S\\.\\b …",
                                   "SCOTUS", "Supreme Court of the United States",
                                   "court-judgment", 1, "BINDING")])

Pure data + tiny registries. No imports from the engines (they import *this*).

Internal by design: carried configuration data walked by the engines; no
operator surface of its own.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# ── Courts: pattern → (label, name, kind, tier, effect-NAME) ─────────────────────────────
# Order inside a pack matters (specific before generic); packs merge in registration order.
# Effect is a `source_classes.Effect` NAME — resolved by the engine, kept as a string here so
# this module stays pure data.
_COURT_PACKS: dict[str, list[tuple[str, str, str, str, int, str]]] = {}
#: registration order — merged table preserves it (first-match + earliest-position semantics)
_COURT_ORDER: list[str] = []


def register_court_pack(pack_id: str,
                        entries: list[tuple[str, str, str, str, int, str]]) -> None:
    """Register (or replace) a jurisdiction's court-authority table.
    Entry: ``(pattern, label, name, kind, tier, effect_name)``."""
    if pack_id not in _COURT_PACKS:
        _COURT_ORDER.append(pack_id)
    _COURT_PACKS[pack_id] = list(entries)


def court_entries() -> list[tuple[str, str, str, str, int, str]]:
    """All registered court entries, pack-registration order preserved."""
    return [e for pid in _COURT_ORDER for e in _COURT_PACKS[pid]]


# The DE+EU starter pack — the original interleaved order is load-bearing (specific first;
# positional tie broken by table index), so it ships as ONE pack in that order.
register_court_pack("de-eu", [
    (r"\bBVerfG\b|\bBundesverfassungsgericht\b",
     "BVerfG", "Bundesverfassungsgericht", "constitutional", 1, "BINDING"),
    (r"\b(?:EuGH|Gerichtshof der Europäischen Union)\b|\bCourt of Justice\b|\bECLI:EU:C:",
     "CJEU", "Court of Justice of the EU", "cjeu", 1, "BINDING"),
    (r"\b(?:EuG|General Court)\b|\bECLI:EU:T:",
     "GC", "General Court of the EU", "cjeu", 2, "INTERPRETIVE"),
    (r"\bBGH\b|\bBundesgerichtshof\b",
     "BGH", "Bundesgerichtshof", "court-judgment", 2, "INTERPRETIVE"),
    (r"\bBAG\b|\bBundesarbeitsgericht\b",
     "BAG", "Bundesarbeitsgericht", "court-judgment", 2, "INTERPRETIVE"),
    (r"\bBVerwG\b|\bBundesverwaltungsgericht\b",
     "BVerwG", "Bundesverwaltungsgericht", "court-judgment", 2, "INTERPRETIVE"),
    (r"\bBFH\b|\bBundesfinanzhof\b",
     "BFH", "Bundesfinanzhof", "court-judgment", 2, "INTERPRETIVE"),
    (r"\bBSG\b|\bBundessozialgericht\b",
     "BSG", "Bundessozialgericht", "court-judgment", 2, "INTERPRETIVE"),
    # administrative authority — a decision, NOT court precedent
    (r"\bBundeskartellamt\b|\bBKartA\b",
     "BKartA", "Bundeskartellamt", "administrative-decision", 3, "PERSUASIVE"),
    # full institutional names only — bare AG/LG/KG collide with company forms
    (r"\bOLG\b|\bOberlandesgericht\b|\bKammergericht\b",
     "OLG", "Oberlandesgericht", "court-judgment", 3, "INTERPRETIVE"),
    (r"\bLandgericht\b", "LG", "Landgericht", "court-judgment", 4, "PERSUASIVE"),
    (r"\bAmtsgericht\b", "AG", "Amtsgericht", "court-judgment", 4, "PERSUASIVE"),
])


# ── Judgment-structure markers (genre fingerprints for "this is a court decision") ────────
_MARKER_PACKS: dict[str, list[str]] = {}


def register_judgment_markers(pack_id: str, patterns: list[str]) -> None:
    _MARKER_PACKS[pack_id] = list(patterns)


def judgment_marker_patterns() -> list[str]:
    return [p for pats in _MARKER_PACKS.values() for p in pats]


register_judgment_markers("de", [
    r"\bLeitsa(?:tz|tze|tzes)\b", r"\bTatbestand\b", r"\bEntscheidungsgr(?:ü|ue)nde\b",
    r"\b(?:Urteil|Beschluss)\b", r"\bRn\.?\s*\d+",
    r"\b[IVX]+\s*ZR\s*\d+/\d+|\bKVR\s*\d+/\d+|\bB\d\s*-\d+/\d+",
])
register_judgment_markers("eu", [
    r"\bECLI:", r"\bJudgment of the Court\b",
    r"\bOpinion of (?:the )?Advocate General\b",
])
register_judgment_markers("en-uk", [
    r"\bthe Court\s+(?:holds?|held|finds?|found|rules?|ruled)\b",
    r"\[\d{4}\]\s+(?:UKSC|EWCA|EWHC|UKHL|AC|QB|WLR|CSOH|IESC)\b",
])


# ── Instrument vocabulary: role→lifecycle-step, room cues, ask-synonyms ────────────────────
#: role → the lifecycle steps that role owns (merged over packs; neutral default: none —
#: a role without a step entry simply shows no step, it is NOT mis-mapped).
_ROLE_STEPS: dict[str, str] = {}
#: extra room cues per pack, appended to the engine's neutral cues: (room, (cues…))
_ROOM_CUES_EXTRA: list[tuple[str, tuple[str, ...]]] = []
#: extra ask-synonyms per pack: phrase → (facet, value)
_ASK_SYNONYMS: dict[str, tuple[str, str]] = {}


def register_instrument_vocab(pack_id: str, *, role_steps: Optional[dict[str, str]] = None,
                              room_cues: Optional[list[tuple[str, tuple[str, ...]]]] = None,
                              ask_synonyms: Optional[dict[str, tuple[str, str]]] = None) -> None:
    _ROLE_STEPS.update(role_steps or {})
    _ROOM_CUES_EXTRA.extend(room_cues or [])
    _ASK_SYNONYMS.update(ask_synonyms or {})


def role_steps() -> dict[str, str]:
    return dict(_ROLE_STEPS)


def room_cues_extra() -> list[tuple[str, tuple[str, ...]]]:
    return list(_ROOM_CUES_EXTRA)


def ask_synonyms() -> dict[str, tuple[str, str]]:
    return dict(_ASK_SYNONYMS)


# EU AI-Act + GDPR instrument vocabulary — carried verbatim from the instruments' own
# role/step language; the engines merge it with per-call overrides (project(step_of=…)).
register_instrument_vocab("eu-ai",
    role_steps={
        "provider": "design · document · conformity",
        "deployer": "deploy · operate",
        "importer": "import into EU",
        "distributor": "distribute",
        "controller": "determine purpose & means",
        "processor": "process on instruction",
    },
    room_cues=[
        ("Conformity", ("ce marking",)),
        ("Risk management", ("dpia",)),
    ],
    ask_synonyms={
        "gpai": ("risk", "gpai"),
        "dpia": ("demand", "assessment"),
    })
