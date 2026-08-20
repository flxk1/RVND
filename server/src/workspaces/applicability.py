# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Applicability-facet enrichment — the bridge between obligations and a card.

An obligation pair carries its trigger as prose: a ``bearer`` ("providers of
high-risk AI systems") and a ``condition`` ("where the system is used for
employment"). The matcher cannot reliably compare prose to a typed
:class:`~workspaces.subject_card.SubjectCard`. This module lifts the bearer +
condition into the SAME controlled facet vocabulary the card uses, so matching
is facet-set against facet-set — deterministic and auditable, no model deciding
"does this apply".

This is the honest crux of the whole memo pipeline (concept §3). It is a
keyword→facet projection: high-precision, domain-specific, and deliberately
conservative — a trigger facet it cannot confidently read is left **unset**,
which the matcher treats as "this obligation does not constrain that facet"
(i.e. the obligation applies more broadly), never as a silent exclusion.

Phase-1 is keyword rules (here). The same seam takes a Layer-2 local-LLM
trigger reader later without changing the matcher: it just needs to emit the
same ``{role, risk_tier/artifact_class, activity}`` facet dict.
"""

from __future__ import annotations

import re
from typing import Any



# ---------------------------------------------------------------------------
# AI Act trigger lexicon: phrase -> (facet, value). Longest/most-specific
# phrases first within each facet so "high-risk ai system" wins over "ai system".
# ---------------------------------------------------------------------------

_AI_ACT_ROLE = [
    ("provider", "provider"), ("providers", "provider"),
    ("deployer", "deployer"), ("deployers", "deployer"),
    ("importer", "importer"), ("importers", "importer"),
    ("distributor", "distributor"), ("distributors", "distributor"),
    ("authorised representative", "authorised-representative"),
]

_AI_ACT_TIER = [
    ("prohibited", "prohibited"),
    ("high-risk ai", "high-risk"), ("high risk ai", "high-risk"),
    ("high-risk", "high-risk"),
    ("general-purpose ai", "gpai"), ("gpai", "gpai"),
]

_AI_ACT_AREA = [
    ("biometric", "biometrics"),
    ("employment", "employment"), ("recruitment", "employment"),
    ("workers", "employment"), ("workplace", "employment"),
    ("critical infrastructure", "critical-infrastructure"),
    ("education", "education"), ("vocational training", "education"),
    ("law enforcement", "law-enforcement"),
    ("migration", "migration-border"), ("border", "migration-border"),
    ("administration of justice", "justice-democracy"),
    ("essential", "essential-services"), ("creditworthiness", "essential-services"),
]


def _scan(text: str, table: list[tuple[str, str]]) -> list[str]:
    out: list[str] = []
    low = text.lower()
    for phrase, value in table:
        if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", low):
            if value not in out:
                out.append(value)
    return out


def applicability_facets_ai_act(bearer: str, condition: str) -> dict[str, Any]:
    """Lift an AI Act obligation's bearer + condition into trigger facets.

    Returns only facets the trigger actually constrains. A missing facet means
    "this obligation does not restrict that dimension" — so it applies across
    all values of it (the matcher does not require the card to match an absent
    trigger facet).
    """
    text = f"{bearer} . {condition}"
    facets: dict[str, Any] = {}

    roles = _scan(text, _AI_ACT_ROLE)
    if roles:
        facets["role"] = roles[0]          # an obligation binds one role class

    tiers = _scan(text, _AI_ACT_TIER)
    if tiers:
        # prefer the most specific non-prohibited tier; gpai is distinct.
        facets["risk_tier"] = tiers[0]

    areas = _scan(text, _AI_ACT_AREA)
    if areas:
        facets["annex_iii_area"] = areas

    return facets


# Registry: domain -> trigger-reader function (bearer, condition) -> facet dict.
_TRIGGER_READERS = {
    "ai-act": applicability_facets_ai_act,
}


def register_trigger_reader(domain: str, fn) -> None:
    _TRIGGER_READERS[domain] = fn


def read_trigger(bearer: str, condition: str, *, domain: str | None = None) -> dict[str, Any]:
    """The NEUTRAL trigger read. ``domain`` given → that registered reader; ``None`` → every
    registered reader, first-writer-wins per facet (registration order). The engine callers
    (``duty_identification``) go through THIS — never a named instrument's reader directly —
    so which instruments can be read is a fact about the registered packs, not the engine."""
    if domain is not None:
        reader = _TRIGGER_READERS.get(domain)
        if reader is None:
            raise ValueError(f"no trigger reader registered for domain {domain!r}; "
                             f"one of {sorted(_TRIGGER_READERS)}")
        return reader(bearer, condition)
    facets: dict[str, Any] = {}
    for reader in _TRIGGER_READERS.values():
        for k, v in reader(bearer, condition).items():
            facets.setdefault(k, v)
    return facets


def enrich_pair(pair: dict[str, Any], domain: str) -> dict[str, Any]:
    """Attach ``applicability`` facets to an obligation pair in place + return it.

    Reads ``solution.bearer`` + ``solution.condition`` (the deontic ND shape)
    or falls back to the problem summary. No-op (empty facets) when the domain
    has no trigger reader.
    """
    reader = _TRIGGER_READERS.get(domain)
    if reader is None:
        pair.setdefault("applicability", {})
        return pair
    sol = pair.get("solution") or {}
    bearer = sol.get("bearer") or (pair.get("problem") or {}).get("facets", {}).get("bearer", "")
    condition = sol.get("condition") or ""
    if not bearer and not condition:
        bearer = (pair.get("problem") or {}).get("summary", "")
    pair["applicability"] = reader(bearer, condition)
    return pair


def enrich_pairs(pairs: list[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
    """Enrich a list of obligation pairs with applicability facets."""
    return [enrich_pair(p, domain) for p in pairs]
