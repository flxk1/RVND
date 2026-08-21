# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Retrieval experiment: hybrid (BM25 + expansion + concept-coverage + as_of)
vs a baseline RAG. The §4 (query-document mismatch) + R1.4 (point-in-time) proof.

The corpus is the trap the essay describes:

  GOV   the governing norm, worded UNLIKE the query ("von der Einziehung kann
        abgesehen werden, soweit ... unbillig"), in force at the as_of date.
  OLD   a superseded earlier version (right words, wrong time).
  DECOY an unrelated procedural norm that shares the query's surface words
        ("das Amt ... Einziehung") — what keyword overlap latches onto.

The layperson query uses none of GOV's content words; it expresses the legal
concepts (Härtefall, verzichten/absehen). A correct system retrieves GOV.
`pytest -s` prints the ranked lists and einschlägigkeit@1.
"""

from __future__ import annotations

from datetime import date

import rvnd.currency as cur
from rvnd.hybrid_retrieval import (
    Document, HybridIndex, baseline_retrieve, einschlaegigkeit_at_k,
)

REG = cur.CurrencyRegistry.from_rows([
    {"celex": "NORM-NEU", "in_force_from": "2018-01-01"},
    {"celex": "NORM-ALT", "in_force_from": "1990-01-01",
     "superseded_by": "NORM-NEU", "superseded_from": "2018-01-01"},
    {"celex": "DECOY-REG", "in_force_from": "2000-01-01"},
])

CORPUS = [
    Document("GOV",
             "Von der Einziehung kann abgesehen werden, soweit sie nach Lage des "
             "Einzelfalls unbillig wäre.",
             celex="NORM-NEU", authority_tier=1),
    Document("OLD",
             "Von der Einziehung konnte abgesehen werden, soweit sie unbillig war. "
             "(frühere Fassung)",
             celex="NORM-ALT", authority_tier=1),
    Document("DECOY",
             "Das Amt betreibt die Einziehung der Forderung und überwacht die "
             "fristgerechte Zahlung.",
             celex="DECOY-REG", authority_tier=1),
]

QUERY = "Darf das Amt trotz Härtefall auf die Einziehung verzichten?"
RELEVANT = {"GOV"}


def _print(title, hits):
    print(f"\n  {title}")
    for r, h in enumerate(hits, 1):
        print(f"    {r}. {h.doc.id:6} cov={h.coverage} score={h.score:6.3f} status={h.status}")


def test_hybrid_beats_baseline_and_respects_time():
    index = HybridIndex(CORPUS)
    baseline = baseline_retrieve(QUERY, CORPUS, k=3)
    hybrid = index.retrieve(QUERY, k=3, as_of=date(2024, 6, 1), registry=REG, temporal="filter")

    print("\nQuery-document mismatch experiment (as_of 2024-06-01)")
    _print("baseline (keyword overlap, no time):", baseline)
    _print("hybrid (BM25+expansion+coverage+as_of):", hybrid)
    b1 = einschlaegigkeit_at_k(baseline, RELEVANT, 1)
    h1 = einschlaegigkeit_at_k(hybrid, RELEVANT, 1)
    print(f"\n  einschlägigkeit@1  baseline={b1:.2f}  hybrid={h1:.2f}")

    assert hybrid[0].doc.id == "GOV"          # governing norm ranked first …
    assert h1 == 1.0
    assert baseline[0].doc.id != "GOV"        # … which keyword overlap does NOT do
    assert b1 == 0.0
    assert all(h.doc.id != "OLD" for h in hybrid)   # superseded version excluded by time
    # einschlägigkeit (concept coverage) is what carries GOV over a higher-BM25 decoy
    assert hybrid[0].coverage > hybrid[1].coverage


def test_as_of_filter_changes_what_is_retrieved():
    """Same query, different date: in 2010 the OLD version governs (NEU not yet in
    force); in 2024 the NEU version governs (OLD superseded)."""
    index = HybridIndex(CORPUS)
    before = index.retrieve(QUERY, k=3, as_of=date(2010, 6, 1), registry=REG, temporal="filter")
    after = index.retrieve(QUERY, k=3, as_of=date(2024, 6, 1), registry=REG, temporal="filter")
    assert before[0].doc.id == "OLD"
    assert all(h.doc.id != "GOV" for h in before)     # NEU not yet in force in 2010
    assert after[0].doc.id == "GOV"
    assert all(h.doc.id != "OLD" for h in after)      # OLD superseded by 2024


def test_expansion_is_what_bridges_the_mismatch():
    """Ablation: without query expansion + concept coverage, the differently-worded
    governing norm is not reachable from the query and the decoy wins — proving the
    bridge is the mechanism, not luck."""
    index = HybridIndex(CORPUS)
    no_expand = index.retrieve(QUERY, k=3, as_of=date(2024, 6, 1), registry=REG,
                               temporal="filter", expand=False)
    assert no_expand[0].doc.id == "DECOY"
    assert all(h.doc.id != "GOV" for h in no_expand[:1])
