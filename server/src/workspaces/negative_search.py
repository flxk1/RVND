# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Documented negative search — prove what was looked for and ruled out.

Top-k says "these documents look relevant." It never says "I searched for the
exceptions, the transitional provisions, the special norms and the
counter-jurisprudence, and here is what I found or ruled out." For a
Verwaltungsakt that silence is the violation: the Amtsermittlungsgrundsatz
requires that the absence of an exception be *established by searching*, not
assumed.

This module runs the mandatory probe set over a corpus and produces an auditable
record: for each category, what was searched, what was found, and what was
explicitly excluded (with a reason). The search is **complete** only when every
mandatory category was probed — finding nothing is a valid, recorded outcome;
not looking is not.

Category detection uses operative-language markers (multilingual). It is
deliberately conservative: a document it cannot confidently categorise is still
listed under any category whose marker it matches, never silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# The categories a bound decision MUST probe before it may stand.
MANDATORY_CATEGORIES = ("norm", "exception", "transitional", "discretion",
                        "counter-jurisprudence")

_MARKERS: dict[str, re.Pattern[str]] = {
    "exception": re.compile(
        r"\b(?:es sei denn|abweichend|unbeschadet|soweit|in besonderen|"
        r"kann abgesehen werden|ausnahme|unless|by way of derogation|notwithstanding|save where)\b", re.I),
    "transitional": re.compile(
        r"\b(?:übergangs|übergangsvorschrift|altf|für altverfahren|transitional|"
        r"shall continue to apply|prior to the entry into force)\b", re.I),
    "discretion": re.compile(
        r"\b(?:kann|soll|dürfen|darf|may|discretion|härtefall|nach (?:billigem )?ermessen)\b", re.I),
    "counter-jurisprudence": re.compile(
        r"\b(?:urteil|beschluss|az\.?|rn\.?|judgment|c-\d|bgh|bverwg|bverfg|eugh|cjeu|ewca|uksc|u\.s\.)\b", re.I),
}


@dataclass
class CategoryProbe:
    category: str
    searched: bool
    hits: list[str] = field(default_factory=list)      # doc ids matching
    excluded: list[dict[str, str]] = field(default_factory=list)  # {doc_id, reason}
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "searched": self.searched,
                "hits": self.hits, "excluded": self.excluded, "note": self.note}


@dataclass
class NegativeSearchRecord:
    query: str
    probes: list[CategoryProbe] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Every mandatory category was actually probed (found-or-not)."""
        searched = {p.category for p in self.probes if p.searched}
        return set(MANDATORY_CATEGORIES) <= searched

    @property
    def unsearched(self) -> list[str]:
        searched = {p.category for p in self.probes if p.searched}
        return [c for c in MANDATORY_CATEGORIES if c not in searched]

    @property
    def found_nothing(self) -> list[str]:
        """Categories searched that returned nothing — the documented negatives."""
        return [p.category for p in self.probes if p.searched and not p.hits]

    def to_dict(self) -> dict[str, Any]:
        return {"query": self.query, "complete": self.complete,
                "unsearched": self.unsearched, "found_nothing": self.found_nothing,
                "probes": [p.to_dict() for p in self.probes]}


def run(query: str, corpus: Iterable[dict], *,
        excluded: Optional[dict[str, dict[str, str]]] = None) -> NegativeSearchRecord:
    """Probe ``corpus`` for every mandatory category.

    corpus — iterable of ``{"id": ..., "text": ...}`` documents.
    excluded — optional ``{category: {doc_id: reason}}`` recording documents the
        agent/human searched and *deliberately ruled out* (a real negative result
        is stronger than a missing one).
    """
    docs = [(str(d.get("id", "")), str(d.get("text", ""))) for d in corpus]
    excluded = excluded or {}
    rec = NegativeSearchRecord(query=query)
    # "norm" is everything operative; the others are marker-detected.
    for cat in MANDATORY_CATEGORIES:
        hits: list[str] = []
        if cat == "norm":
            hits = [i for i, _ in docs]            # the whole corpus is the norm search space
            note = "operative norm search ran over the full corpus"
        else:
            pat = _MARKERS[cat]
            hits = [i for i, t in docs if pat.search(t)]
            note = ("found candidates" if hits else
                    f"searched, none found — negative result recorded for '{cat}'")
        rec.probes.append(CategoryProbe(
            category=cat, searched=True, hits=hits,
            excluded=[{"doc_id": k, "reason": v} for k, v in (excluded.get(cat) or {}).items()],
            note=note))
    return rec
