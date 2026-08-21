# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Evidence-coverage mapper — which document furnishes which room, what's empty.

The requirements house (:mod:`.requirements_house`) gives the rooms. This module
takes the org's actual descriptive / evidence documents — their DPIA, their
policy, their technical file — and:

  1. PLACES each document into the room(s) it furnishes (evidence → control).
  2. Reports coverage: FURNISHED rooms (≥1 doc), EMPTY rooms (a requirement with
     no evidence — the gap), and ORPHAN documents (fit no room — possibly
     irrelevant, possibly a requirement not yet modelled).

This is the reverse of the matcher: matcher placed a *system* against
*obligations*; this places *documents* against *requirement rooms*.

Placement is the judgment-adjacent step (document classification). Phase-1 here
is keyword/title rules — high precision on the obvious cases ("a file titled
Data Protection Impact Assessment furnishes the DPIA room"), conservative on the
rest (an unsure document is an ORPHAN surfaced for the human, never silently
filed). The Layer-2 local-LLM classifier slots in behind this same interface.

The COVERAGE ARITHMETIC (which rooms are empty) is fully deterministic once
placement is decided — that part is sound regardless of the classifier's
quality. Empty rooms are surfaced honestly: a gap you can see beats a gap a
chatbot smoothed over.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

from .requirements_house import RequirementsHouse, Room


@dataclass
class EvidenceDoc:
    """An org document offered as evidence."""
    doc_id: str
    title: str = ""
    text: str = ""               # extracted text (or a snippet)

    def haystack(self) -> str:
        return f"{self.title}\n{self.text}".lower()


@dataclass
class Placement:
    doc_id: str
    room_id: str
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoverageReport:
    domain: str
    furnished: list[dict[str, Any]] = field(default_factory=list)   # room + docs
    empty: list[dict[str, Any]] = field(default_factory=list)        # rooms, no doc
    orphans: list[dict[str, Any]] = field(default_factory=list)      # docs, no room
    placements: list[Placement] = field(default_factory=list)

    @property
    def coverage_ratio(self) -> float:
        total = len(self.furnished) + len(self.empty)
        return round(len(self.furnished) / total, 2) if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "coverage_ratio": self.coverage_ratio,
            "furnished": self.furnished,
            "empty": self.empty,
            "orphans": self.orphans,
            "placements": [p.to_dict() for p in self.placements],
        }


# Per-artifact recognition cues: how a document announces it furnishes a room.
# Title hits score higher than body hits. Shared vocabulary with the artifact
# catalogue so the room and its evidence speak the same language.
_ROOM_CUES: dict[str, tuple[str, ...]] = {
    "dpia": ("data protection impact assessment", "dpia", "impact assessment"),
    "fria": ("fundamental rights impact assessment", "fria"),
    "conformity-assessment": ("conformity assessment", "declaration of conformity",
                              "ce marking"),
    "ropa": ("record of processing", "records of processing activities", "ropa",
             "article 30 record"),
    "risk-management-system": ("risk management system", "risk management policy",
                               "risk register", "risk assessment"),
    "technical-documentation": ("technical documentation", "technical file",
                                "annex iv"),
    "logs": ("logging policy", "audit log", "event log", "record-keeping policy"),
    "toms": ("technical and organisational measures", "toms", "security measures"),
    "dpo": ("data protection officer", "dpo appointment", "dpo designation"),
    "privacy-policy": ("privacy policy", "privacy notice", "data protection notice"),
    "dpa": ("data processing agreement", "data processing addendum",
            "processor agreement"),
    "sccs": ("standard contractual clauses", "sccs"),
    "incident-register": ("incident register", "breach register",
                          "incident response plan"),
}


def _placement_score(doc: EvidenceDoc, room: Room) -> tuple[float, str]:
    """Score how strongly ``doc`` furnishes ``room``. Title hit > body hit.

    Cue source, in priority: the room's own ``evidence_cues`` (domain-authored,
    room-local — the general path); else the global ``_ROOM_CUES`` table (the
    compliance domain); else the room title words. This is what lets the SAME
    mapper serve any domain: a music income room carries its own cues, no global
    table edit needed.
    """
    cues = tuple(getattr(room, "evidence_cues", ()) or ())
    if not cues:
        key = room.artifact_key or room.room_id
        cues = _ROOM_CUES.get(key, ())
    if not cues:
        # fall back to matching the room title words
        title_words = [w for w in re.findall(r"[a-z]{4,}", room.title.lower())]
        cues = tuple(title_words[:4])
    title_l = doc.title.lower()
    body_l = doc.text.lower()
    best = 0.0
    why = ""
    for c in cues:
        if c in title_l:
            if 0.9 > best:
                best, why = 0.9, f"title contains '{c}'"
        elif c in body_l:
            if 0.6 > best:
                best, why = 0.6, f"body contains '{c}'"
    return best, why


# A document furnishes a room only at or above this score; below, it does not
# count as evidence (conservative — avoids false "covered").
PLACEMENT_FLOOR = 0.6


def map_coverage(house: RequirementsHouse, docs: list[EvidenceDoc],
                 *, floor: float = PLACEMENT_FLOOR) -> CoverageReport:
    """Place each document into the room(s) it furnishes; report coverage."""
    report = CoverageReport(domain=house.domain)
    placed_docs: set[str] = set()
    docs_by_room: dict[str, list[tuple[EvidenceDoc, float, str]]] = {}

    for doc in docs:
        best_room = None
        best_score = 0.0
        best_reason = ""
        for room in house.rooms:
            score, why = _placement_score(doc, room)
            if score > best_score:
                best_room, best_score, best_reason = room, score, why
        if best_room is not None and best_score >= floor:
            docs_by_room.setdefault(best_room.room_id, []).append(
                (doc, best_score, best_reason))
            placed_docs.add(doc.doc_id)
            report.placements.append(Placement(
                doc_id=doc.doc_id, room_id=best_room.room_id,
                confidence=round(best_score, 3), reason=best_reason))

    # furnished vs empty
    for room in house.rooms:
        hits = docs_by_room.get(room.room_id)
        if hits:
            report.furnished.append({
                "room_id": room.room_id,
                "title": room.title,
                "category": room.category,
                "documents": [{"doc_id": d.doc_id, "title": d.title,
                               "confidence": s, "reason": r}
                              for (d, s, r) in hits],
                "obligation_count": len(room.obligations),
            })
        else:
            report.empty.append({
                "room_id": room.room_id,
                "title": room.title,
                "category": room.category,
                "obligation_count": len(room.obligations),
                "sources": room.sources,
            })

    # orphans
    for doc in docs:
        if doc.doc_id not in placed_docs:
            report.orphans.append({"doc_id": doc.doc_id, "title": doc.title})

    return report


def coverage_brief(report: CoverageReport):
    """The bounded brief for a coverage report — what a supervisor must read.

    :func:`render_coverage` renders everything; this renders only what is
    unsettled. An empty room is a gap the supervisor must look at, an orphan
    document is a placement in dispute, and a furnished room is omitted and
    counted rather than shown. The result therefore grows with the org's gaps,
    not with the size of its document set.

    Selection is `loomground-brief`'s, reached through
    :mod:`rvnd.adapters.brief`; the mapping onto statused premises is
    documented there.
    """
    from .adapters.brief import brief_from_coverage

    return brief_from_coverage(
        furnished=report.furnished, empty=report.empty, orphans=report.orphans,
    )


def render_coverage(house: RequirementsHouse, report: CoverageReport) -> str:
    """Human-readable coverage report — the payoff view of the house."""
    out: list[str] = []
    out.append("=" * 74)
    out.append(f"REQUIREMENTS COVERAGE — {house.title or house.domain}".upper())
    out.append("=" * 74)
    out.append("DRAFT — automated, re-verifiable. Not legal advice. Empty rooms "
               "are gaps to confirm; orphans and placements need human review.")
    out.append(f"\nCoverage: {int(report.coverage_ratio * 100)}% of rooms "
               f"furnished ({len(report.furnished)}/"
               f"{len(report.furnished) + len(report.empty)}).")
    out.append("")

    out.append(f"[FURNISHED ROOMS] ({len(report.furnished)})")
    for r in report.furnished:
        out.append(f"  ✓ {r['title']}  [{r['category']}]")
        for d in r["documents"]:
            out.append(f"      ← {d['title']}  ({int(d['confidence']*100)}% · {d['reason']})")
    if not report.furnished:
        out.append("  (none)")
    out.append("")

    out.append(f"[EMPTY ROOMS — GAPS] ({len(report.empty)})")
    for r in report.empty:
        out.append(f"  ✗ {r['title']}  [{r['category']}]  "
                   f"— {r['obligation_count']} obligation(s), no evidence on file")
    if not report.empty:
        out.append("  (none — every requirement has evidence)")
    out.append("")

    out.append(f"[ORPHAN DOCUMENTS — fit no room] ({len(report.orphans)})")
    for d in report.orphans:
        out.append(f"  ? {d['title']}  (doc {d['doc_id']}) — "
                   f"irrelevant, or a requirement not yet modelled")
    if not report.orphans:
        out.append("  (none)")
    out.append("")
    out.append("Empty rooms are surfaced as gaps, not hidden. Every room traces "
               "to a source provision; every placement is re-verifiable.")
    return "\n".join(out)
