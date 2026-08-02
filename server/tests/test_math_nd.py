# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the Math ND, dimensioned edges, and routing of the domain suite."""

from workspaces.dimensions import Dimension
from workspaces.math_extractor import extract_math
from workspaces.domain_nds import (
    GDPRRuleND,
    MathND,
    register_default_domain_nds,
)
from workspaces.nd_routing import DefaultClassifier, NDRouter


VALID_DIMS = {d.value for d in Dimension}

THEOREM = (
    "Theorem: for all integers n, if n is even then n^2 is even.\n"
    "Let n be an even integer. Prove that n^2 is even.\n"
    "Proof. Since n is even, n = 2k for some integer k. "
    "Then n^2 = 4k^2 = 2(2k^2). Hence n^2 is even. QED"
)

GDPR_RULE = (
    "The controller shall implement appropriate technical and organisational "
    "measures where the processing is likely to result in a high risk to the "
    "rights and freedoms of natural persons."
)


# ── Math extractor ───────────────────────────────────────────────

def test_extract_math_pulls_structure():
    probs = extract_math(THEOREM)
    assert len(probs) == 1
    p = probs[0]
    assert "n^2 is even" in p.find.lower() or "even" in p.find.lower()
    assert p.given                      # "Let n be an even integer"
    assert p.steps                      # proof steps
    assert p.domain in {"logic", "number-theory", "algebra", "general"}
    assert 0.0 < p.confidence <= 1.0
    assert p.body_format == "proof"


def test_extract_math_empty_on_nonmath():
    assert extract_math("Dear Sir, please find attached the invoice.") == []
    assert extract_math("") == []


# ── Math ND produces dimensioned pairs ───────────────────────────

def test_math_nd_emits_dimensioned_pairs():
    nd = MathND()
    classification = DefaultClassifier().classify(THEOREM)
    pairs = nd.extract(THEOREM, classification, source_document="thm.md")
    assert pairs
    pair = pairs[0]
    assert pair["problem"]["type"] == "math-problem"
    dims = {e["dimension"] for e in pair["edges"]}
    assert dims <= VALID_DIMS
    # structural domain edge + intentional goal edge are the load-bearing ones
    assert any(e["predicate"] == "in-domain" and e["dimension"] == Dimension.STRUCTURAL.value
               for e in pair["edges"])
    assert any(e["dimension"] == Dimension.INTENTIONAL.value for e in pair["edges"])


# ── Rule NDs now carry dimensions too ────────────────────────────

def test_rule_nd_emits_dimensioned_edges():
    nd = GDPRRuleND()
    classification = DefaultClassifier().classify(GDPR_RULE)
    pairs = nd.extract(GDPR_RULE, classification, source_document="gdpr.md")
    assert pairs
    all_edges = [e for p in pairs for e in p["edges"]]
    dims = {e["dimension"] for e in all_edges}
    assert dims <= VALID_DIMS
    # every rule pair belongs to its domain (structural)
    assert any(e["predicate"] == "belongs-to" and e["dimension"] == Dimension.STRUCTURAL.value
               for e in all_edges)


# ── End-to-end routing of the domain suite ───────────────────────

def test_math_routes_to_math_nd():
    classification = DefaultClassifier().classify(THEOREM)
    assert classification.primary_type == "math"
    router = NDRouter()
    register_default_domain_nds(router)
    assert "nd-math" in router.registered()
    result = router.dispatch(THEOREM, classification, source_document="thm.md")
    assert "nd-math" in result.nds_engaged
    assert result.total_pairs >= 1


def test_rules_route_to_rule_nd():
    classification = DefaultClassifier().classify(GDPR_RULE)
    router = NDRouter()
    register_default_domain_nds(router)
    result = router.dispatch(GDPR_RULE, classification, source_document="gdpr.md")
    # GDPR content engages at least the gdpr ND (and the contracts catch-all)
    assert "nd-gdpr" in result.nds_engaged
    assert result.total_pairs >= 1


def test_every_emitted_edge_has_valid_dimension():
    for content in (THEOREM, GDPR_RULE):
        classification = DefaultClassifier().classify(content)
        router = NDRouter()
        register_default_domain_nds(router)
        result = router.dispatch(content, classification)
        for pair in result.all_pairs:
            for edge in pair.get("edges", []):
                assert edge["dimension"] in VALID_DIMS
                assert edge["subject"] and edge["predicate"]
