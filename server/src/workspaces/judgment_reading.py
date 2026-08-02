# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Judgment → authority-weighted READINGS of the provisions it construes.

A court decision is not an instrument: it does not *enact* obligations, it *interprets*
existing ones. `policy_ingest`'s genre guard quarantines judgments away from the express
compiler (a holding lowered to a `.lg` gate would be a phantom obligation); this module is
the interpreter-side landing zone the guard routes to. It turns a decision into a list of
:class:`ProvisionReading` — for each provision the judgment construes, a *candidate* reading
carrying the court's authority weight, the proposed interpretive relation, the holding span,
and a currency note.

The honest division of labour (matches the navigator/interpreter doctrine):

  * DETERMINISTIC here — what the document *states about itself*: the court (and thus the
    authority tier + effect ceiling), the case id, the date, the cited provisions
    (reusing :mod:`.national_citations`), and the published Leitsatz/holding span.
  * PROPOSED, not decided — the interpretive RELATION (construes / narrows / extends /
    confirms / disapplies) is read from cue words and carries a confidence; absent a cue it
    defaults to the neutral ``construes`` at low confidence.
  * NEVER auto-applied — every reading is ``requires_ratification=True`` /
    ``auto_applied=False``. *Which* sentence is the ratio for *which* provision, and whether
    the holding controls the org's facts, is the genuinely interpretive call: it belongs to
    the walker + human ratify (``reasoning_walker`` / ``lens.Precedent``), gated like a norm
    collision (``norm_contract`` ESCALATE). This module lays the candidate on the table; it
    does not decide it.

A reading is also TIME-BOUND: a holding can be distinguished or overruled, so each carries a
currency note rather than being treated as permanent (the same temporal honesty norms get).

Pure stdlib + reuse. No model in the loop; no chain writes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Optional

from . import national_citations as _nc
from .source_classes import Effect

# ── Court authority — the one thing legal_systems does NOT carry (it ranks source
# CLASSES, not courts). The ENGINE here is jurisdiction-neutral: which courts exist, what
# their mastheads look like, and what force their readings carry are CARRIED DATA in
# `jurisdiction_packs` (shipped: de-eu; add a jurisdiction by registering a pack — no engine
# change). Effect is the MOST a reading from this body can carry; the relation still defaults
# conservative. Unknown courts get NO authority, never a silently top-ranked one.

@dataclass(frozen=True)
class CourtAuthority:
    label: str          # e.g. "BGH", "SCOTUS"
    name: str           # the institution's full name
    kind: str           # court-judgment | constitutional | cjeu | administrative-decision | …
    tier: int           # 1 = apex … 4 = first-instance / local
    effect: Effect      # ceiling on a reading's force


def _court_table() -> list[tuple[re.Pattern, CourtAuthority]]:
    """The registered packs' court entries, compiled — pack order preserved (specific before
    generic within a pack; first-match + earliest-position semantics in detect_court)."""
    from . import jurisdiction_packs as _jp
    return [(re.compile(pat), CourtAuthority(label, name, kind, tier, Effect[effect]))
            for (pat, label, name, kind, tier, effect) in _jp.court_entries()]


def detect_court(text: str) -> Optional[CourtAuthority]:
    """Identify the ISSUING body (and thus tier + effect ceiling), or None if none is
    recognised — in which case we do NOT fabricate an authority weight.

    A decision *cites* other courts (an administrative decision quotes apex cases far more
    often than it names itself), so frequency points at the wrong body. The issuer is the one
    in the masthead/metadata — it appears EARLIEST. So: among all recognised courts, return the
    one whose first occurrence is earliest (table order breaks a positional tie, specific first)."""
    best: Optional[tuple[int, int, CourtAuthority]] = None
    for idx, (rx, court) in enumerate(_court_table()):
        m = rx.search(text)
        if m and (best is None or (m.start(), idx) < (best[0], best[1])):
            best = (m.start(), idx, court)
    return best[2] if best else None


# ── Interpretive RELATION — proposed from cue words, never asserted without one. ──────────
_RELATION_CUES: tuple[tuple[str, re.Pattern], ...] = (
    ("disapplies", re.compile(r"\b(?:aufgegeben|überholt|hält\s+nicht\s+mehr|nicht\s+mehr\s+fest|"
                              r"overrul\w+|departs?\s+from|no\s+longer\s+good\s+law)\b", re.I)),
    ("confirms",   re.compile(r"\b(?:ständige\s+Rechtsprechung|st\.?\s*Rspr\.?|bestätigt|"
                              r"im\s+Anschluss\s+an|settled\s+case[- ]law|confirms?|reaffirm\w+)\b", re.I)),
    ("narrows",    re.compile(r"\b(?:eng\s+aus(?:zu)?legen|einschränkend|restriktiv|"
                              r"narrowly\s+constru\w+|read\s+down|strictly\s+constru\w+)\b", re.I)),
    ("extends",    re.compile(r"\b(?:weit\s+aus(?:zu)?legen|extensiv|erstreckt\s+sich|"
                              r"broadly\s+constru\w+|extends?\s+to)\b", re.I)),
)
_CONSTRUE_CUE = re.compile(r"\b(?:aus(?:zu)?legen|Auslegung|dahin(?:gehend)?|construed?|interpret\w+)\b", re.I)


def propose_relation(text: str) -> tuple[str, float]:
    """The interpretive relation a holding bears to the provisions it cites — PROPOSED.
    A specific cue (narrows/extends/confirms/disapplies) gives moderate confidence; only a
    generic construe cue gives low; nothing gives the neutral default at floor confidence.
    The relation is never *decided* here — it is a candidate for the interpreter to ratify."""
    for rel, rx in _RELATION_CUES:
        if rx.search(text):
            return rel, 0.5
    if _CONSTRUE_CUE.search(text):
        return "construes", 0.3
    return "construes", 0.1


# ── Case id, date, holding span ───────────────────────────────────────────────────────────
_CASE_ID = re.compile(r"\bECLI:[A-Z:0-9.]+\b|\b[IVX]+\s*ZR\s*\d+/\d+\b|\bKVR\s*\d+/\d+\b|"
                      r"\bB\d\s*-\s*\d+/\d+\b|\b\d+\s*[A-Z]{1,3}\s*\d+/\d+\b")
_DATE = re.compile(r"\b(\d{1,2}\.\s*(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|"
                   r"Oktober|November|Dezember)\s*\d{4})\b|\b(\d{4}-\d{2}-\d{2})\b")
_LEITSATZ = re.compile(r"Leitsa(?:tz|tze|tzes)\b[:\s]*(.+?)(?:\n\s*\n|Tatbestand|Gründe|"
                       r"Entscheidungsgründe|$)", re.I | re.S)


def extract_case_id(text: str) -> str:
    m = _CASE_ID.search(text)
    return m.group(0).strip() if m else ""


def extract_decided(text: str) -> Optional[str]:
    m = _DATE.search(text)
    return (m.group(1) or m.group(2)).strip() if m else None


def extract_holding(text: str, *, max_chars: int = 600) -> str:
    """The published Leitsatz (headnote) — the court's own statement of what it held — when
    present. This is a deterministic SPAN, not a ratio decision: which clause governs which
    provision is left to the interpreter. Empty when the decision publishes no headnote."""
    m = _LEITSATZ.search(text)
    span = (m.group(1) if m else "").strip()
    span = re.sub(r"\s+", " ", span)
    return span[:max_chars]


# ── Construed provisions — what the judgment cites (reuse + generic pinpoint fallback) ─────
_GEN_PARA = re.compile(r"§\s*(\d+[a-z]?)\s+([A-ZÄÖÜ][A-Za-zÄÖÜ]{1,7})\b")
_GEN_ART = re.compile(r"Art(?:\.|ikel)?\s*(\d+[a-z]?)\s+([A-ZÄÖÜ][A-Za-zÄÖÜ]{1,7})\b")


def construed_provisions(text: str) -> list[tuple[str, str]]:
    """Distinct (pinpoint, statute) the judgment construes. First the curated recogniser
    (gives statute name + URL), then a generic ``§ N ABBR`` / ``Art. N ABBR`` pass for
    statutes the corpus has not pre-loaded (GWB, UWG, …) so an uncurated cite still anchors."""
    out: dict[str, tuple[str, str]] = {}
    for c in _nc.extract_citations(text):
        if c.section:
            key = f"{c.section} {c.abbrev}"
            out[key] = (key, c.abbrev)
    for rx, marker in ((_GEN_PARA, "§"), (_GEN_ART, "Art.")):
        for m in rx.finditer(text):
            num, abbr = m.group(1), m.group(2)
            key = f"{marker} {num} {abbr}"
            out.setdefault(key, (key, abbr))
    return list(out.values())


@dataclass
class ProvisionReading:
    """One candidate reading of one provision the judgment construes. A candidate — never an
    applied obligation; the interpreter + a human ratify (or reject) it."""
    provision: str               # pinpoint, e.g. "§ 33 GWB" / "Art. 102 AEUV"
    statute: str                 # "GWB" / instrument abbrev / ""
    relation: str                # proposed: construes | narrows | extends | confirms | disapplies
    relation_confidence: float   # how sure the cue read is — NOT how sure the reading is right
    court: str                   # "BGH"
    court_name: str              # "Bundesgerichtshof"
    court_kind: str              # court-judgment | constitutional | cjeu | administrative-decision
    authority_tier: int          # 1 apex … 4 local
    effect: str                  # Effect.name — the force ceiling of this reading
    weight: float                # effect normalised to [0,1] (authority, not certainty)
    holding_excerpt: str         # Leitsatz span (proposed; ratio is the interpreter's call)
    case_id: str
    decided: Optional[str]
    currency: str                # honest: a holding can be distinguished / overruled
    requires_ratification: bool  # ALWAYS True
    auto_applied: bool           # ALWAYS False
    source: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def to_readings(text: str, *, max_provisions: int = 50) -> list[ProvisionReading]:
    """The adapter: judgment text → candidate readings of the provisions it construes.

    Returns ``[]`` when no court is recognised (we do not weight an unknown authority) or no
    provision can be anchored (a holding with nothing to bind to is not a reading — its span
    is still available via :func:`extract_holding` for the interpreter). Every reading is a
    ratification candidate, never an applied obligation."""
    court = detect_court(text)
    if court is None:
        return []
    provisions = construed_provisions(text)[:max_provisions]
    if not provisions:
        return []
    relation, conf = propose_relation(text)
    holding = extract_holding(text)
    case_id = extract_case_id(text)
    decided = extract_decided(text)
    weight = round(court.effect.value / Effect.BINDING.value, 2)
    currency = (f"as decided{(' ' + decided) if decided else ''}; subject to being "
                f"distinguished or overruled — verify it is still good law")
    src = f"{court.label} {case_id}".strip()
    readings: list[ProvisionReading] = []
    for pinpoint, statute in provisions:
        readings.append(ProvisionReading(
            provision=pinpoint, statute=statute,
            relation=relation, relation_confidence=conf,
            court=court.label, court_name=court.name, court_kind=court.kind,
            authority_tier=court.tier, effect=court.effect.name, weight=weight,
            holding_excerpt=holding, case_id=case_id, decided=decided, currency=currency,
            requires_ratification=True, auto_applied=False, source=src,
        ))
    return readings
