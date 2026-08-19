# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the concrete domain NDs (B2 final wiring).

Each ND:
- Engages on its facet + normative type.
- Produces typed pairs whose ``solution.body`` shows the extracted rule
  structure when one was found, or the full content when extraction missed.
- Does NOT engage on non-normative content (confidence_floor guards this).
"""

from __future__ import annotations


from rvnd import (
    AIActRuleND,
    Classification,
    ContractRuleND,
    DefaultClassifier,
    GDPRRuleND,
    MusicRightsRuleND,
    NDRouter,
    register_default_domain_nds,
)


# ===========================================================================
# Facet-based routing
# ===========================================================================


def test_gdpr_nd_engages_on_gdpr_facet():
    r = NDRouter()
    r.register(GDPRRuleND())
    classification = Classification(
        primary_type="normative", facets=["gdpr"], confidence=0.7,
    )
    result = r.dispatch(
        "The controller shall implement appropriate technical and "
        "organisational measures to ensure compliance.",
        classification,
    )
    assert "nd-gdpr" in result.nds_engaged
    assert result.total_pairs >= 1


def test_ai_act_nd_engages_on_ai_act_facet():
    r = NDRouter()
    r.register(AIActRuleND())
    classification = Classification(
        primary_type="normative", facets=["ai-act"], confidence=0.7,
    )
    result = r.dispatch(
        "Providers of high-risk AI systems shall ensure that their systems "
        "undergo conformity assessment.",
        classification,
    )
    assert "nd-ai-act" in result.nds_engaged


def test_music_rights_nd_engages_on_music_facet():
    r = NDRouter()
    r.register(MusicRightsRuleND())
    classification = Classification(
        primary_type="normative", facets=["music-rights"], confidence=0.7,
    )
    result = r.dispatch(
        "The Licensee shall pay the publisher a mechanical royalty of "
        "ten percent of net sales.",
        classification,
    )
    assert "nd-music-rights" in result.nds_engaged


def test_contracts_nd_catches_normative_without_domain_facet():
    """No domain facet → ContractRuleND still claims it (catch-all)."""
    r = NDRouter()
    r.register(ContractRuleND())
    classification = Classification(
        primary_type="normative", facets=[], confidence=0.6,
    )
    result = r.dispatch(
        "The Licensee shall pay a licence fee within thirty days.",
        classification,
    )
    assert "nd-contracts" in result.nds_engaged


# ===========================================================================
# Confidence floor guards
# ===========================================================================


def test_domain_nd_skipped_below_confidence_floor():
    r = NDRouter()
    r.register(GDPRRuleND())   # confidence_floor=0.45
    classification = Classification(
        primary_type="normative", facets=["gdpr"], confidence=0.30,
    )
    result = r.dispatch("text", classification)
    assert "nd-gdpr" in result.nds_skipped


def test_domain_nd_does_not_engage_on_non_normative_type():
    r = NDRouter()
    r.register(GDPRRuleND())
    classification = Classification(
        primary_type="unknown", facets=[], confidence=0.7,
    )
    result = r.dispatch("plain prose", classification)
    assert "nd-gdpr" not in result.nds_engaged


# ===========================================================================
# Pair structure
# ===========================================================================


def test_gdpr_pair_has_rule_structure():
    """When extraction succeeds, the pair carries structured rule facets."""
    r = NDRouter()
    r.register(GDPRRuleND())
    classification = Classification(
        primary_type="normative", facets=["gdpr"], confidence=0.7,
    )
    result = r.dispatch(
        "The controller shall implement appropriate technical measures.",
        classification,
    )
    pairs = result.pairs_by_nd["nd-gdpr"]
    assert pairs
    pair = pairs[0]
    assert pair["problem"]["facets"]["domain"] == "gdpr"
    # If the rule was extracted, it shows up in solution.rule.
    if pair["problem"]["type"] == "rule":
        assert "subject" in pair["solution"]["rule"]
        assert pair["solution"]["rule"]["modal"] in (
            "obligation", "prohibition", "right", "permission"
        )


def test_umbrella_pair_when_extraction_misses():
    """If rule_extractor can't structure the content, an umbrella pair lands.

    Tests the audit-floor guarantee — even normative content the regex
    regex extractor can't parse must produce a pair so the L0 record exists.
    """
    r = NDRouter()
    r.register(GDPRRuleND())
    classification = Classification(
        primary_type="normative", facets=["gdpr"], confidence=0.7,
    )
    # GDPR Art. 4 definition — known to miss the regex rule extractor.
    result = r.dispatch(
        "For the purposes of this Regulation, the following definitions "
        "apply: 'personal data' means any information relating to an "
        "identified or identifiable natural person.",
        classification,
    )
    pairs = result.pairs_by_nd["nd-gdpr"]
    assert pairs
    # At least one pair is emitted regardless of extraction success.
    assert pairs[0]["problem"]["facets"]["domain"] == "gdpr"


# ===========================================================================
# Full registration helper
# ===========================================================================


def test_register_default_domain_nds_registers_full_suite():
    r = NDRouter()
    register_default_domain_nds(r)
    assert set(r.registered()) == {
        "nd-gdpr", "nd-ai-act", "nd-music-rights", "nd-contracts", "nd-math",
        "nd-oversight",
    }


def test_multi_facet_document_engages_multiple_nds():
    """A document with both gdpr and ai-act facets engages both NDs."""
    r = NDRouter()
    register_default_domain_nds(r)
    classification = Classification(
        primary_type="normative",
        facets=["gdpr", "ai-act"],
        confidence=0.7,
    )
    result = r.dispatch(
        "Providers of high-risk AI systems shall ensure that personal "
        "data is processed in accordance with the GDPR.",
        classification,
    )
    assert "nd-gdpr" in result.nds_engaged
    assert "nd-ai-act" in result.nds_engaged
    # The contracts ND is the FALLBACK: it stays silent when a domain facet
    # already claims the document. (Previously it over-fired on every
    # normative doc, tagging AI Act / GDPR text with a spurious "contracts"
    # scope — fixed 2026-06-05.)
    assert "nd-contracts" not in result.nds_engaged


def test_contracts_fallback_fires_on_facetless_normative():
    """Normative content with NO domain facet falls to the contracts ND only."""
    r = NDRouter()
    register_default_domain_nds(r)
    classification = Classification(
        primary_type="normative", facets=[], confidence=0.7,
    )
    result = r.dispatch(
        "The provider shall document the oversight measures before release.",
        classification,
    )
    assert "nd-contracts" in result.nds_engaged
    assert "nd-gdpr" not in result.nds_engaged
    assert "nd-ai-act" not in result.nds_engaged


# ===========================================================================
# End-to-end: classifier + ND fan-out on real legal text
# ===========================================================================


def test_end_to_end_gdpr_article():
    """Classifier marks the text normative + gdpr facet → nd-gdpr engages."""
    c = DefaultClassifier()
    r = NDRouter()
    register_default_domain_nds(r)
    text = (
        "The controller shall implement appropriate technical and "
        "organisational measures to ensure and to be able to demonstrate "
        "that processing is performed in accordance with this Regulation."
    )
    classification = c.classify(text)
    assert classification.primary_type == "normative"
    assert "gdpr" in classification.facets
    result = r.dispatch(text, classification)
    assert "nd-gdpr" in result.nds_engaged


def test_end_to_end_ai_act_article():
    c = DefaultClassifier()
    r = NDRouter()
    register_default_domain_nds(r)
    text = (
        "Providers of high-risk AI systems referred to in Annex III shall "
        "ensure that their systems undergo the relevant conformity "
        "assessment procedure prior to their placing on the market."
    )
    classification = c.classify(text)
    assert classification.primary_type == "normative"
    assert "ai-act" in classification.facets
    result = r.dispatch(text, classification)
    assert "nd-ai-act" in result.nds_engaged
