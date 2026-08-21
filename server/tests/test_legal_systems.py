# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The legal-system meta-layer: a substrate switch over jurisdiction families.

Proves the substrate carries pluggable packs (DE / EU / UK / US) and that the
switch actually changes behaviour: the conflict-resolution principles, the
authority hierarchy, and the retrieval-expansion vocabulary all follow the
selected legal system — while the engine stays the same.
"""

from __future__ import annotations


import pytest

from rvnd import legal_systems as ls
from rvnd.hybrid_retrieval import Document, HybridIndex


def test_registry_has_the_expected_packs():
    assert set(ls.available()) >= {"DE", "EU", "UK", "US"}
    assert ls.get().code == "DE"                      # default
    with pytest.raises(KeyError):
        ls.get("ZZ")                                  # typo never silently falls back


def test_families_differ_in_conflict_principles():
    de = ls.get("DE")
    uk = ls.get("UK")
    us = ls.get("US")
    assert de.family == "civil" and uk.family == "common"
    # Civil law resolves by the lex-* principles …
    assert "lex-specialis" in de.conflict_principles
    assert "lex-posterior" in de.conflict_principles
    assert "lex-superior" in de.conflict_principles
    # … common law by stare decisis (and has no lex-superior).
    assert "stare-decisis" in uk.conflict_principles and "stare-decisis" in us.conflict_principles
    assert "lex-superior" not in uk.conflict_principles


def test_authority_hierarchies_are_family_specific():
    assert ls.get("DE").authority_hierarchy[0] == "Grundgesetz"
    assert ls.get("US").authority_hierarchy[0] == "US Constitution"
    # a statute outranks a regulation in every family
    de = ls.get("DE")
    assert de.authority_rank("Bundesgesetz") < de.authority_rank("Rechtsverordnung")


def test_switch_changes_retrieval_vocabulary():
    """A German hardship query reaches the German norm only under the DE pack;
    an English hardship query reaches an English norm only under a common-law
    pack — same engine, different switch."""
    de_corpus = [
        Document("DE_GOV", "Von der Einziehung kann abgesehen werden, soweit sie unbillig wäre."),
        Document("NOISE", "Die Sitzung beginnt um neun Uhr im großen Saal."),
    ]
    en_corpus = [
        Document("EN_GOV", "The authority may waive recovery where it would cause undue hardship."),
        Document("NOISE", "The meeting starts at nine in the main hall."),
    ]
    de_q = "Darf das Amt trotz Härtefall auf die Einziehung verzichten?"
    en_q = "May the agency forgo recovery in a case of hardship?"

    de_hit = HybridIndex(de_corpus).retrieve(de_q, k=1, legal_system="DE")
    en_hit = HybridIndex(en_corpus).retrieve(en_q, k=1, legal_system="UK")
    assert de_hit and de_hit[0].doc.id == "DE_GOV" and de_hit[0].coverage >= 2
    assert en_hit and en_hit[0].doc.id == "EN_GOV" and en_hit[0].coverage >= 2

    # Wrong pack for the language → the concept vocabulary doesn't fire.
    en_under_de = HybridIndex(en_corpus).retrieve(en_q, k=1, legal_system="DE")
    assert not en_under_de or en_under_de[0].coverage == 0


def test_contract_reports_the_active_family_principles():
    from rvnd.norm_contract import check_pair, Level
    pair = {
        "id": "x", "problem": {"id": "x-p", "type": "rule", "facets": {
            "modal": "muss", "has_exception": False,
            "applicability": {}, "jurisdiction": ["UK"]}},
        "solution": {"id": "x", "authority_tier": 1, "confidence": 0.9,
            "source": "s.3 Data Protection Act", "predicate": "may-conflict-with",
            "resolution": "genuine-conflict-escalate",
            "temporal": {"status": "in-force", "date_source": "registry"}},
        "edges": [],
    }
    rep = check_pair(pair, legal_system="UK")
    nt6 = [f for f in rep.findings if f.code == "NT-6" and f.level is Level.ESCALATE]
    assert nt6 and "stare-decisis" in nt6[0].message     # message reflects the UK pack
