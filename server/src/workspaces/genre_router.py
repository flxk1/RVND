# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Document-GENRE router — "policy ingest should know them all".

Statutes, regulations, standards, contracts and policies are documents with recognisable
*genres*, each with its own structure: an EU regulation has recitals then numbered Articles;
an ISO/Annex-SL standard has clauses 1–3 (scope/refs/terms) then requirement clauses 4–10 plus
Annexes; a German statute is a run of ``§`` paragraphs; a contract opens with WHEREAS recitals.

This module detects the genre and applies the matching profile — which **preamble to drop**
(non-normative recitals / foreword) and which **segmenter** to use — reusing RVND's substrate
(`legal_norm_splitter` for Article/§ provisions; `policy_normalise` for line cleanup). It is a
registry with a generic fallback; it does not pretend to know every statute on earth, it knows
the shapes RVND ingests and is extensible (add a profile).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from . import legal_norm_splitter as _split
from . import policy_normalise as _norm

_ADOPTED = re.compile(r"ha(?:ve|s)\s+adopted\s+this\s+(?:regulation|directive)\s*:?", re.I)
_ARTICLE1 = re.compile(r"(?m)^\s*Article\s+1\b")

# A court JUDGMENT is not an instrument: it INTERPRETS norms, it does not enact them — so it
# must never reach the policy compiler (a holding lowered to a `.lg` gate would be a phantom
# enforcement node manufactured from a court's reasoning). Detect it by a FINGERPRINT of
# decision-structure markers that co-occur: a statute that merely *cites* a case carries one;
# a judgment carries several. WHAT the markers look like is jurisdiction-specific CARRIED DATA
# (`jurisdiction_packs`, shipped: de / eu / en-uk) — the co-occurrence ENGINE here is neutral.
_JUDGMENT_MIN = 3


def _judgment_markers() -> list[re.Pattern]:
    from . import jurisdiction_packs as _jp
    return [re.compile(p, re.I) for p in _jp.judgment_marker_patterns()]


def is_judgment(text: str) -> bool:
    """True when ≥3 distinct court-decision markers co-occur — a judgment, not an instrument.
    Conservative on purpose: a statute that cites a single case (one marker) is not caught."""
    return sum(1 for rx in _judgment_markers() if rx.search(text)) >= _JUDGMENT_MIN


# ── Genre registry — the engine walks it in order; a genre is DATA + a predicate. ─────────
# Shipped profiles are the shapes RVND has ingested so far (a starting library, not a claim
# of coverage). Add a genre with register_genre() — no engine change.
def _is_eu_regulation(text: str, low: str) -> bool:
    return bool(_ADOPTED.search(text) or (
        re.search(r"\bregulation \(eu\)|\bdirective \d", low)
        and re.search(r"(?m)^\s*article\s+\d", text, re.I)
        and re.search(r"(?m)^\s*\(\d+\)", text)))


_GENRES: list[dict[str, Any]] = [
    {"genre": "eu-regulation", "jurisdiction": "EU", "is_law": True,
     "predicate": _is_eu_regulation},
    {"genre": "paragraph-statute", "jurisdiction": "DE", "is_law": True,
     "predicate": lambda text, low: len(re.findall(r"§\s*\d", text)) >= 3},
    {"genre": "us-statute", "jurisdiction": "US", "is_law": True,
     "predicate": lambda text, low: bool(
         re.search(r"\bU\.S\.C\.|\bUnited States Code\b", text)
         or re.search(r"(?m)^\s*Sec\.\s*\d", text))},
    {"genre": "iso-standard", "jurisdiction": None, "is_law": False,
     "predicate": lambda text, low: bool(
         re.search(r"ISO/IEC|ISO\s+\d{3,}|Annex SL", text)
         or ("terms and definitions" in low
             and re.search(r"(?m)^\s*4\s+context", text, re.I)))},
    {"genre": "contract", "jurisdiction": None, "is_law": False,
     "predicate": lambda text, low: "whereas" in low
         and ("the parties" in low or "this agreement" in low)},
]


def register_genre(genre: str, predicate: Callable[[str, str], bool], *,
                   jurisdiction: Optional[str] = None, is_law: bool = False,
                   position: Optional[int] = None) -> None:
    """Register a document genre: ``predicate(text, lowercased_text) -> bool``. Order matters
    (first match wins); ``position`` inserts before an existing profile, default appends."""
    entry = {"genre": genre, "jurisdiction": jurisdiction, "is_law": is_law,
             "predicate": predicate}
    _GENRES.insert(position, entry) if position is not None else _GENRES.append(entry)


def _genre_entry(genre: str) -> Optional[dict[str, Any]]:
    return next((g for g in _GENRES if g["genre"] == genre), None)


def detect_genre(text: str) -> str:
    """Classify a document's genre by walking the registry. A judgment is a judgment even when
    it cites a regulation/§§ — checked FIRST, before any statute shape can claim it."""
    low = text.lower()
    if is_judgment(text):
        return "case-law"
    for g in _GENRES:
        if g["predicate"](text, low):
            return g["genre"]
    return "generic"


def _trim_preamble(text: str, genre: str) -> str:
    """Drop the non-normative preamble for the genre. EU reg/directive: everything up to the
    LAST 'HAVE ADOPTED THIS …' (or, failing that, before 'Article 1') — i.e. the recitals.
    ISO/Annex-SL: handled by policy_normalise's clause-1–3 trim. Others: unchanged."""
    if genre == "eu-regulation":
        hits = list(_ADOPTED.finditer(text))
        if hits:
            return text[hits[-1].end():]
        m = _ARTICLE1.search(text)
        if m:
            return text[m.start():]
    return text


def route(text: str) -> dict[str, Any]:
    """Detect genre and return {genre, jurisdiction, body (preamble-trimmed), n_units,
    units?} — the body is what extraction should consume; units are provision segments for
    law genres (each with a pinpoint), for future per-provision extraction."""
    genre = detect_genre(text)
    body = _trim_preamble(text, genre)
    entry = _genre_entry(genre)
    units = _split.segment_provisions(body) if (entry and entry["is_law"]) else []
    return {"genre": genre, "jurisdiction": entry["jurisdiction"] if entry else None,
            "body": body, "n_units": len(units)}


def ingest_prepare(raw: str) -> tuple[str, str]:
    """Front of the file-ingest path: detect genre, drop the genre's preamble, then line-clean
    (boilerplate/de-hyphenate/reflow). Returns (genre, text_ready_for_extraction)."""
    genre = detect_genre(raw)
    body = _trim_preamble(raw, genre)
    cleaned, _rep = _norm.clean(body, trim=(genre == "iso-standard"))
    return genre, (cleaned if cleaned.strip() else body)
