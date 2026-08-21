# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Issue tokens — the keystone of the problem-solving computer.

Any input — text, image, audio — contains *n* issue tokens. The domain ND
algebra never reads raw pixels or waveforms: a thin per-modality extraction
front-end lifts the raw input into a typed *surface* carrying spans, and the
detector spots issue tokens over that surface. So an issue token ALWAYS
cites a modality-typed span (char range / bounding box / timecode), never
"the model felt it".

Each token's reasoning method is a DETERMINISTIC function of its issue type
(``ISSUE_METHOD``), not a per-instance choice — a liability-cap issue is
always subsumption/Gutachten; a balancing issue is always residual-heavy.
That table is what makes downstream synthesis auditable.

Detection is registry-driven per domain, mirroring
:func:`rvnd.applicability.register_trigger_reader`: one ND detector per
domain, phased — keyword/facet rules now (this module ships a reference
``contract-de`` text detector), a local-LLM reader later at the same seam
without changing the schema or the downstream graph.

Internal by design: the typed vocabulary other modules exchange; no operator surface of its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .reasoning_contract import PROFILES

#: issue_type -> reasoning profile (method). The method is a property of the
#: TYPE, never chosen per instance. Unknown types degrade to 'generic'.
ISSUE_METHOD: dict[str, str] = {
    "liability_cap":         "legal-de",     # Obersatz–Subsumtion–Ergebnis
    "warranty":              "legal-de",
    "ip_assignment":         "legal-de",
    "data_processing":       "legal-de",
    "termination":           "legal-de",
    "governing_law":         "legal-irac",
    "confidentiality":       "legal-irac",
    "good_faith_balancing":  "generic",      # residual-heavy, no clean subsumption
    "proportionality":       "generic",
}


def assign_method(issue_type: str) -> str:
    """Deterministic method lookup. Validated against the reasoning profiles;
    an unknown type (or one mapped to a retired profile) degrades to
    'generic' — a labelling fact, never an error."""
    prof = ISSUE_METHOD.get(issue_type, "generic")
    return prof if prof in PROFILES else "generic"


@dataclass
class Span:
    """A modality-typed location. Exactly one coordinate family is set:
    text → start/end (char offsets); image → bbox (x0,y0,x1,y1, normalised);
    audio → t_start/t_end (seconds)."""
    modality: str                      # text | image | audio
    start: Optional[int] = None
    end: Optional[int] = None
    bbox: Optional[tuple] = None
    t_start: Optional[float] = None
    t_end: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"modality": self.modality}
        if self.start is not None:
            d["start"], d["end"] = self.start, self.end
        if self.bbox is not None:
            d["bbox"] = list(self.bbox)
        if self.t_start is not None:
            d["t_start"], d["t_end"] = self.t_start, self.t_end
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Span":
        return cls(modality=d.get("modality", "text"),
                   start=d.get("start"), end=d.get("end"),
                   bbox=tuple(d["bbox"]) if d.get("bbox") else None,
                   t_start=d.get("t_start"), t_end=d.get("t_end"))


@dataclass
class IssueToken:
    """One detected problem in a document, with its citation and its method."""
    issue_id: str
    issue_type: str
    modality: str
    span: Span
    norm_anchors: list[str] = field(default_factory=list)
    source: str = ""
    confidence: float = 0.0
    text: str = ""                     # the surface excerpt the token cites

    def method(self) -> str:
        return assign_method(self.issue_type)

    def fingerprint(self) -> dict[str, Any]:
        """Retrieval fingerprint — the issue token IS the fingerprint at
        document granularity. Shape matches ``case_index.case_fingerprint``
        so the same retrieval ranks solvers for it."""
        fp: dict[str, Any] = {"issue_type": self.issue_type,
                              "profile": self.method()}
        rooms = sorted({a for a in self.norm_anchors if a})
        fp["rooms"] = rooms
        return fp

    def to_dict(self) -> dict[str, Any]:
        return {"issue_id": self.issue_id, "issue_type": self.issue_type,
                "modality": self.modality, "span": self.span.to_dict(),
                "norm_anchors": list(self.norm_anchors), "source": self.source,
                "confidence": self.confidence, "text": self.text,
                "method": self.method()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IssueToken":
        return cls(issue_id=d["issue_id"], issue_type=d["issue_type"],
                   modality=d["modality"], span=Span.from_dict(d["span"]),
                   norm_anchors=list(d.get("norm_anchors") or []),
                   source=d.get("source", ""),
                   confidence=d.get("confidence", 0.0),
                   text=d.get("text", ""))


def token_to_subpayload(tok: IssueToken) -> dict[str, Any]:
    """Project an issue token into the sub-case payload shape that
    ``kg_export.case_set_to_cytoscape`` consumes — so a detected set of
    tokens feeds the existing visualiser unchanged. The token is a problem
    NOT yet solved: no grounds, every norm anchor an open room (gap) until a
    walker run receipts it."""
    return {"case": {"problem": {"text": tok.text or tok.issue_type},
                     "grounds": [], "gaps": list(tok.norm_anchors),
                     "resolution": {"type": "open"},
                     "profile": tok.method()},
            "inputs": {"rooms": list(tok.norm_anchors),
                       "profile": tok.method()}}


# ── detection registry (one ND detector per domain) ───────────────────────────

Detector = Callable[[str], list[IssueToken]]
_DETECTORS: dict[str, Detector] = {}


def register_detector(domain: str, fn: Detector) -> None:
    _DETECTORS[domain] = fn


def detect_issues(surface: str, *, domain: str) -> list[IssueToken]:
    """Run the domain's ND detector over a (text) surface. Unknown domain →
    no tokens, never an error — absence is honest, not a failure."""
    fn = _DETECTORS.get(domain)
    return list(fn(surface)) if fn else []


# ── reference detector: contract-de (Phase-1 keyword/facet rules) ─────────────

#: (issue_type, trigger regex, norm anchors the issue typically engages).
_CONTRACT_DE_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("liability_cap",   r"\bliabilit\w+\b.{0,80}\bcap",      ("§ 309 Nr. 7 BGB",)),
    ("liability_cap",   r"\bhaftung\b.{0,80}\b(begrenz|beschränk)", ("§ 309 Nr. 7 BGB",)),
    ("data_processing", r"\b(personal data|personenbezogene? daten)\b", ("Art. 28(3) GDPR",)),
    ("data_processing", r"\bArt\.?\s*28\b",               ("Art. 28(3) GDPR",)),
    ("ip_assignment",   r"\b(work product|intellectual property|assigned to)\b", ("§ 31 UrhG",)),
    ("warranty",        r"\bwarrant\w+\b",                  ("§ 434 BGB",)),
    ("termination",     r"\bterminat\w+\b|\bkündig\w+\b",   ("§ 314 BGB",)),
    ("governing_law",   r"\bgoverning law\b|\banwendbares recht\b", ()),
    ("confidentiality", r"\bconfidential\w*\b|\bvertraulich\w*\b", ("§ 1 GeschGehG",)),
)


def detect_contract_de(surface: str) -> list[IssueToken]:
    """Reference ND detector: spots common contract issues in a text surface
    by clause-trigger rules, citing the matched char span. Conservative —
    each match is one token; overlapping types both fire (a clause can raise
    two issues). Phase-1 keyword layer; a local-LLM reader replaces the body
    later without changing the signature."""
    by_clause: dict[tuple[str, int], IssueToken] = {}
    for itype, pattern, anchors in _CONTRACT_DE_RULES:
        for m in re.finditer(pattern, surface, re.IGNORECASE | re.DOTALL):
            start = max(0, surface.rfind("\n", 0, m.start()) + 1)
            end = surface.find("\n", m.end())
            end = len(surface) if end == -1 else end
            # one clause raising one issue type = ONE token, even if several
            # trigger rules for that type fire inside the clause. Merge
            # anchors so no norm reference is lost to dedup.
            key = (itype, start)
            existing = by_clause.get(key)
            if existing is not None:
                for a in anchors:
                    if a not in existing.norm_anchors:
                        existing.norm_anchors.append(a)
                continue
            by_clause[key] = IssueToken(
                issue_id=f"{itype}@{start}",
                issue_type=itype, modality="text",
                span=Span("text", start=start, end=end),
                norm_anchors=list(anchors), source="",
                confidence=0.5, text=surface[start:end].strip()[:200])
    return sorted(by_clause.values(), key=lambda t: (t.span.start or 0))


register_detector("contract-de", detect_contract_de)
