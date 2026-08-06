# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Hybrid legal retrieval: lexical BM25 + query expansion + ratione-temporis filter.

Dense-only / keyword-only retrieval finds *similar* text; legal research needs
*einschlägige* text. This module fuses the three signals the essay calls for:

  1. BM25 lexical match — exact Normbegriffe, paragraph numbers, Aktenzeichen
     survive (dense embeddings routinely smear these).
  2. Query expansion over a legal equivalence map — bridges the
     query-document mismatch the essay centres on: a layperson asks whether the
     authority may "erlassen die Rückforderung trotz Härtefall"; the norm says
     "von der Einziehung kann abgesehen werden, soweit sie unbillig wäre". The
     content words barely overlap; expansion connects erlassen↔abgesehen,
     Härtefall↔unbillig, Rückforderung↔Einziehung.
  3. Ratione-temporis filter — using :mod:`workspaces.currency`, drop or penalise any
     instrument not in force at the ``as_of`` date, so the result is the law that
     ACTUALLY GOVERNED the conduct, not today's version (closes register R1.4).

Optional authority-tier boost reuses the existing signal so a statute outranks a
blog on a tie. Pure stdlib — no embedding service required, fully reproducible.

Internal by design: a retrieval stage inside the legal pipelines, not a standalone query surface.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from . import currency as cur
from .adapters.versum import BM25 as _VersumBM25  # the consumed lexical-ranking mechanism


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens; keeps German umlauts (\\w is Unicode-aware)."""
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 2]


# ── Legal equivalence map — sourced from the active legal-system pack ────────
# The vocabulary is NOT hard-coded here. It comes from workspaces.legal_systems, so
# switching the folder's legal_system (DE / EU / UK / US …) switches the
# expansion behaviour. The engine below stays domain- and jurisdiction-agnostic.
from . import legal_systems as _ls

_MAPS_CACHE: dict[str, tuple[dict, dict]] = {}


def _maps(legal_system: str) -> tuple[dict, dict]:
    """(term→cluster, term→cluster-id) for the selected legal system, cached."""
    code = (legal_system or _ls.DEFAULT).upper()
    if code not in _MAPS_CACHE:
        expand: dict[str, frozenset] = {}
        cid: dict[str, int] = {}
        for i, cluster in enumerate(_ls.get(code).equivalence_clusters):
            for term in cluster:
                expand[term] = cluster
                cid[term] = i
        _MAPS_CACHE[code] = (expand, cid)
    return _MAPS_CACHE[code]


def query_concept_clusters(tokens: Iterable[str], *, legal_system: str = "DE") -> set[int]:
    """The distinct legal concept-clusters a query activates, in the given system."""
    _, cid = _maps(legal_system)
    return {cid[t] for t in tokens if t in cid}


def doc_concept_coverage(doc_tokens: Iterable[str], query_clusters: set[int],
                         *, legal_system: str = "DE") -> int:
    """How many of the query's distinct concept-clusters this doc addresses — the
    einschlägigkeit signal that separates the governing norm from a lexically
    similar decoy. Concepts come from the active legal-system pack."""
    _, cid = _maps(legal_system)
    covered = {cid[t] for t in doc_tokens if t in cid}
    return len(covered & query_clusters)


def expand_query(tokens: Iterable[str], *, legal_system: str = "DE",
                 weight_original: float = 1.0, weight_expanded: float = 0.5) -> Counter:
    """Weighted token multiset: originals at full weight, equivalence-cluster
    members (from the active legal system) at a discount."""
    expand, _ = _maps(legal_system)
    bag: Counter = Counter()
    for t in tokens:
        bag[t] += weight_original
        for syn in expand.get(t, ()):
            if syn != t:
                bag[syn] += weight_expanded
    return bag


# ── Documents + BM25 ────────────────────────────────────────────────────────

@dataclass
class Document:
    id: str
    text: str
    celex: Optional[str] = None        # for the ratione-temporis filter
    authority_tier: int = 3            # 1 = statute/cited … higher = weaker
    jurisdiction: tuple[str, ...] = ()
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.tokens:
            self.tokens = tokenize(self.text)


@dataclass
class Hit:
    doc: Document
    score: float                      # lexical (BM25, expansion, authority) confidence
    status: str = "n/a"               # ratione-temporis status at as_of
    coverage: int = 0                 # distinct query concept-clusters addressed


class HybridIndex:
    """BM25 index with optional query expansion, temporal filtering and an
    authority boost. ``k1``/``b`` are the standard Okapi defaults."""

    def __init__(self, docs: Iterable[Document], *, k1: float = 1.5, b: float = 0.75):
        self.docs = list(docs)
        # Consume versum's BM25 (the lexical idf/tf-normalisation mechanism) rather
        # than re-growing it here. The legal wrapper in retrieve() — query
        # expansion (weighted), ratione-temporis filtering, authority boost and
        # concept coverage — stays local; versum's BM25.score(..., weights=)
        # carries the expanded-term weighting exactly as before.
        self._bm25 = _VersumBM25(k1, b).fit([d.tokens for d in self.docs])

    def retrieve(self, query: str, *, k: int = 5, expand: bool = True,
                 as_of: Optional[date] = None,
                 registry: Optional[cur.CurrencyRegistry] = None,
                 temporal: str = "filter", authority_boost: bool = True,
                 legal_system: str = "DE") -> list[Hit]:
        """Rank documents for ``query`` under a selected ``legal_system`` (the
        pack supplies the equivalence vocabulary).

        temporal: 'filter' drops anything not in force at ``as_of``; 'penalise'
        keeps it at a heavy discount; None ignores time. Requires ``registry``
        and per-doc ``celex`` to take effect.
        """
        qtokens = tokenize(query)
        qbag = (expand_query(qtokens, legal_system=legal_system) if expand
                else Counter({t: 1.0 for t in qtokens}))
        qclusters = (query_concept_clusters(qtokens, legal_system=legal_system)
                     if expand else set())

        hits: list[Hit] = []
        for i, d in enumerate(self.docs):
            status = "n/a"
            factor = 1.0
            if as_of and registry is not None and d.celex:
                status = cur.validity_status(registry.get(d.celex), as_of)
                if status != "in-force":
                    if temporal == "filter":
                        continue
                    if temporal == "penalise":
                        factor *= 0.1
            # versum BM25 over the doc index; the weighted query bag carries the
            # expansion weights (originals 1.0, synonyms discounted) exactly.
            score = self._bm25.score(list(qbag), i, weights=qbag) * factor
            if authority_boost and score > 0:
                score *= 1.0 + (4 - min(max(d.authority_tier, 1), 4)) * 0.05
            coverage = (doc_concept_coverage(d.tokens, qclusters, legal_system=legal_system)
                        if qclusters else 0)
            if score > 0 or coverage > 0:
                hits.append(Hit(doc=d, score=score, status=status, coverage=coverage))
        # Einschlägigkeit before similarity: rank first by how many distinct legal
        # concepts of the query a document addresses, then by lexical confidence.
        # A decoy that merely repeats a shared word cannot outrank the norm that
        # actually covers more of what the question is about.
        hits.sort(key=lambda h: (h.coverage, h.score), reverse=True)
        return hits[:k]


def baseline_retrieve(query: str, docs: Iterable[Document], *, k: int = 5) -> list[Hit]:
    """Naive RAG analogue: plain lexical token-overlap, NO expansion, NO temporal
    awareness — the thing the essay says is insufficient. Used as the comparator."""
    q = set(tokenize(query))
    hits = []
    for d in docs:
        overlap = len(q & set(d.tokens))
        if overlap:
            hits.append(Hit(doc=d, score=float(overlap), status="n/a"))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]


def einschlaegigkeit_at_k(hits: list[Hit], relevant_ids: set[str], k: int = 1) -> float:
    """Fraction of the top-k that are the legally governing (relevant) documents."""
    top = hits[:k]
    if not top:
        return 0.0
    return sum(1 for h in top if h.doc.id in relevant_ids) / len(top)
