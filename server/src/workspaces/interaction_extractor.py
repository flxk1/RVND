# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Implicit cross-instrument edge extraction — the interaction layer.

Most cross-instrument relationships are stated in the text (DORA lex-specialis
to NIS2; transitional supersession clauses) and fall out of per-document
extraction. The *implicit* ones do not live in any single document: NIS2's
24-hour incident-notification window vs. DORA's much shorter one are never
named together, yet they co-apply to the same entity. Those relationships fall
out of comparing two already-extracted obligation pairs.

This module is the deterministic mechanism for that comparison. It does NOT
ship resolutions — per the locked decision *ship the law, never the
resolution*: a genuine conflict is surfaced for a human, never auto-resolved.

Two architectural rules from the concept (enforced here, not just documented):

1. **The precedent / edge firewall.** A *precedent* (a previously validated
   interaction) lives in the interaction ND and only *guides* a proposal — it
   gives consistency. The *graph edge* (a proposal after admission) is a
   separate object; only graph edges are ever walked by ``reason``. They
   cross-reference but live in different layers, so the self-feeding loop
   (``reason`` consuming its own output) never forms. Here: a precedent can
   shape a proposal, but ``to_graph_edge`` only emits for an *admitted*,
   non-escalating proposal — never directly from a precedent.

2. **The confidence trap.** Precedent recurrence gives *consistency*, not new
   legal *authority*. A proposal's ``authority_tier`` inherits from the
   precedent's original sign-off and is never promoted by recurrence count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional

from workspaces.adapters.solver.dimensions import Dimension


# Confidence at/above this ships as a re-verifiable candidate; below → held.
CONFIDENCE_FLOOR = 0.85

# A small, high-precision regulatory-topic lexicon. Two obligations co-apply
# only when they share a topic AND come from different instruments.
_TOPIC_LEXICON: dict[str, tuple[str, ...]] = {
    "incident-reporting": ("incident", "notification", "notify", "report", "breach"),
    "risk-management": ("risk", "risk management", "mitigation"),
    "security": ("security", "encryption", "resilience", "technical measures"),
    "data-protection": ("personal data", "data subject", "processing", "dpia"),
    "governance": ("oversight", "accountability", "governance", "audit"),
}

# Deadline phrases ("within 24 hours", "innerhalb von 72 Stunden", "4 hours").
_DEADLINE_RE = re.compile(
    r"(?P<n>\d{1,3})\s*(?P<unit>hours?|hrs?|days?|stunden?|tage?|heures?|giorni|días?)",
    re.IGNORECASE,
)
_UNIT_HOURS = {
    "hour": 1, "hours": 1, "hr": 1, "hrs": 1, "stunde": 1, "stunden": 1,
    "heure": 1, "heures": 1,
    "day": 24, "days": 24, "tag": 24, "tage": 24, "giorni": 24,
    "día": 24, "días": 24,
}


@dataclass
class InteractionProposal:
    """A proposed relationship between two obligation pairs.

    Lives as a *candidate* (it would be written to the interaction ND only on
    human sign-off). It is NOT a graph edge until admitted via
    :meth:`to_graph_edge`.
    """

    subject: str          # pair A id
    object: str           # pair B id
    predicate: str        # co-applies-with | may-conflict-with | supersedes
    dimension: str        # workspaces.dimensions value
    resolution: str       # cumulative | a-overrides-b | genuine-conflict-escalate | unresolved
    authority_tier: int   # 1 textual/cited; lower if interpretive (inherited from precedent)
    confidence: float
    status: str           # "admitted" | "pending"
    provenance: dict[str, Any] = field(default_factory=dict)
    proposed_from_precedent: Optional[str] = None
    recurrence: int = 0   # consistency signal only — NEVER promotes authority_tier

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_graph_edge(self) -> Optional[dict[str, Any]]:
        """Emit a 5D graph edge that ``reason`` may walk — but ONLY for an
        admitted, non-escalating proposal. A genuine conflict (escalate) and
        any pending proposal return ``None``: they await human judgment and
        must not enter the reasoning graph (the firewall)."""
        if self.status != "admitted":
            return None
        if self.resolution == "genuine-conflict-escalate":
            return None
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "dimension": self.dimension,
            "weight": round(self.confidence, 3),
            "provenance": self.provenance,
        }


# --- pair accessors (tolerant of the domain_nds pair shape) ----------------

def _facets(pair: dict[str, Any]) -> dict[str, Any]:
    return (pair.get("problem") or {}).get("facets") or {}


def _domain(pair: dict[str, Any]) -> str:
    return str(_facets(pair).get("domain") or (pair.get("problem") or {}).get("scope") or "")


def _text(pair: dict[str, Any]) -> str:
    sol = pair.get("solution") or {}
    return f"{(pair.get('problem') or {}).get('summary','')} {sol.get('body','')}".lower()


def _authority(pair: dict[str, Any]) -> int:
    return int((pair.get("solution") or {}).get("authority_tier") or 3)


def _topics(pair: dict[str, Any]) -> set[str]:
    text = _text(pair)
    found = set()
    for topic, cues in _TOPIC_LEXICON.items():
        if any(c in text for c in cues):
            found.add(topic)
    return found


def _deadline_hours(pair: dict[str, Any]) -> Optional[int]:
    m = _DEADLINE_RE.search(_text(pair))
    if not m:
        return None
    n = int(m.group("n"))
    unit = m.group("unit").lower().rstrip(".")
    return n * _UNIT_HOURS.get(unit, 1)


# --- co-applicability -------------------------------------------------------

def co_applying_pairs(
    pairs: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any], set[str]]]:
    """High-precision: yield (A, B, shared_topics) for pairs from DIFFERENT
    instruments that share at least one regulatory topic. Same-instrument and
    topic-disjoint pairs are out of scope (that keeps the overlap meaningful)."""
    out = []
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            a, b = pairs[i], pairs[j]
            if _domain(a) == _domain(b) or not _domain(a) or not _domain(b):
                continue
            shared = _topics(a) & _topics(b)
            if shared:
                out.append((a, b, shared))
    return out


# --- precedent lookup (guides; never walked by reason) ----------------------

def _precedent_key(a: dict[str, Any], b: dict[str, Any], topics: set[str]) -> tuple:
    return (tuple(sorted((_domain(a), _domain(b)))), tuple(sorted(topics)))


def find_precedent(
    a: dict[str, Any], b: dict[str, Any], topics: set[str],
    interaction_nd: Iterable[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Nearest prior in the interaction ND: same instrument pair + same topics.
    Returns the precedent record (which carries the validated edge type,
    dimension, resolution, and its ORIGINAL authority tier)."""
    domains_key, topics_key = _precedent_key(a, b, topics)
    best, best_rec = None, -1
    for prec in interaction_nd:
        if (tuple(sorted(prec.get("domains", []))) == domains_key
                and tuple(sorted(prec.get("topics", []))) == topics_key):
            rec = int(prec.get("recurrence", 0))
            if rec > best_rec:
                best, best_rec = prec, rec
    return best


# --- proposal ---------------------------------------------------------------

def propose_edge(
    a: dict[str, Any], b: dict[str, Any], topics: set[str],
    *, interaction_nd: Optional[Iterable[dict[str, Any]]] = None,
    confidence_floor: float = CONFIDENCE_FLOOR,
) -> InteractionProposal:
    """Propose the relationship between two co-applying obligation pairs.

    Order of preference:
      1. **Precedent-guided** (consistency): if the interaction ND holds a
         validated prior for this (instrument-pair, topics), reuse its edge.
         Authority is *inherited* from the precedent, never promoted by
         recurrence (the confidence trap).
      2. **Temporal conflict**: both obligations carry a deadline on the same
         topic and the deadlines differ → genuine conflict, surfaced for a
         human (never auto-resolved).
      3. **Cold co-application**: same topic, different instruments → both
         apply cumulatively, proposed as a low-confidence candidate.
    """
    prov = {
        "pair_a": a.get("id"), "pair_b": b.get("id"),
        "domains": sorted((_domain(a), _domain(b))),
        "topics": sorted(topics),
        "cited_sources_a": (a.get("solution") or {}).get("cited_sources", []),
        "cited_sources_b": (b.get("solution") or {}).get("cited_sources", []),
    }

    # 1. Precedent-guided.
    if interaction_nd is not None:
        prec = find_precedent(a, b, topics, interaction_nd)
        if prec is not None:
            inherited_tier = int(prec.get("authority_tier", 3))   # NOT promoted
            recurrence = int(prec.get("recurrence", 0)) + 1
            resolution = prec.get("resolution", "cumulative")
            # Consistency raises confidence toward, but never above, a cap that
            # keeps a genuine conflict escalating.
            conf = min(0.9, 0.7 + 0.02 * recurrence)
            status = "pending" if resolution == "genuine-conflict-escalate" or conf < confidence_floor else "admitted"
            return InteractionProposal(
                subject=a["id"], object=b["id"],
                predicate=prec.get("predicate", "co-applies-with"),
                dimension=prec.get("dimension", Dimension.RELATIONAL.value),
                resolution=resolution, authority_tier=inherited_tier,
                confidence=round(conf, 3), status=status, provenance=prov,
                proposed_from_precedent=prec.get("id"), recurrence=recurrence)

    # 2. Temporal conflict (different deadlines on a shared topic).
    da, db = _deadline_hours(a), _deadline_hours(b)
    if da is not None and db is not None and da != db:
        prov["deadline_hours"] = {"a": da, "b": db}
        return InteractionProposal(
            subject=a["id"], object=b["id"], predicate="may-conflict-with",
            dimension=Dimension.TEMPORAL.value,
            resolution="genuine-conflict-escalate",
            authority_tier=min(_authority(a), _authority(b)),
            confidence=0.5, status="pending", provenance=prov)

    # 3. Cold co-application — cumulative, low-confidence candidate.
    conf = 0.7
    return InteractionProposal(
        subject=a["id"], object=b["id"], predicate="co-applies-with",
        dimension=Dimension.RELATIONAL.value, resolution="cumulative",
        authority_tier=min(_authority(a), _authority(b)),
        confidence=conf, status="pending" if conf < confidence_floor else "admitted",
        provenance=prov)


def extract_interactions(
    pairs: list[dict[str, Any]],
    *, interaction_nd: Optional[Iterable[dict[str, Any]]] = None,
    confidence_floor: float = CONFIDENCE_FLOOR,
) -> list[InteractionProposal]:
    """Full pass: find co-applying cross-instrument pairs and propose an edge
    for each. Returns candidates; callers persist admitted ones to the graph
    and route pending/escalating ones to the oversight queue."""
    proposals = []
    for a, b, topics in co_applying_pairs(pairs):
        proposals.append(propose_edge(
            a, b, topics, interaction_nd=interaction_nd,
            confidence_floor=confidence_floor))
    return proposals


def graph_edges(proposals: Iterable[InteractionProposal]) -> list[dict[str, Any]]:
    """The subset of proposals that may enter the reasoning graph (admitted,
    non-escalating). The firewall in one call."""
    return [e for p in proposals if (e := p.to_graph_edge()) is not None]
