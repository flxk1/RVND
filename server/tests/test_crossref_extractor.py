# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the cross-document reference extractor (crossref_extractor.py)."""

from __future__ import annotations

from rvnd.crossref_extractor import (
    CrossReferenceExtractor,
    extract_cross_references,
    infer_host_instrument,
)
from rvnd.nd_routing import Classification


def _cls(facets=None):
    return Classification(primary_type="normative", facets=facets or [],
                          confidence=0.9, metadata={})


# --- host inference --------------------------------------------------------

def test_infer_host_ai_act_by_number():
    assert infer_host_instrument("Regulation (EU) 2024/1689 of the European Parliament") == "ai-act"


def test_infer_host_gdpr_by_phrase():
    assert infer_host_instrument("This General Data Protection Regulation lays down") == "gdpr"


def test_infer_host_unknown():
    assert infer_host_instrument("Just an ordinary memo about lunch.") == ""


# --- cross-reference detection ---------------------------------------------

def test_ai_act_referencing_gdpr_full_citation():
    content = ("This Regulation applies without prejudice to Regulation (EU) 2016/679.")
    refs = extract_cross_references(content, host_key="ai-act")
    keys = {r.target_key for r in refs}
    assert "gdpr" in keys
    gdpr = next(r for r in refs if r.target_key == "gdpr")
    assert gdpr.relation == "without-prejudice"
    assert gdpr.target_celex == "32016R0679"


def test_short_name_detection():
    content = "Operators must also comply with NIS2 and the DSA."
    refs = extract_cross_references(content, host_key="ai-act")
    keys = {r.target_key for r in refs}
    assert "nis2" in keys
    assert "dsa" in keys


def test_self_reference_is_dropped():
    content = "Regulation (EU) 2016/679 lays down rules. This Regulation applies."
    refs = extract_cross_references(content, host_key="gdpr")
    assert all(r.target_key != "gdpr" for r in refs)


def test_celex_detection():
    content = "See CELEX 32019L0790 for the copyright rules."
    refs = extract_cross_references(content, host_key="ai-act")
    keys = {r.target_key for r in refs}
    assert "dsm-directive" in keys


def test_structural_relation_wins_over_relational():
    content = "This Regulation amends Directive 2009/24/EC."
    refs = extract_cross_references(content, host_key="ai-act")
    sw = next(r for r in refs if r.target_key == "software-directive")
    assert sw.relation in ("amends", "repeals", "supersedes")
    assert sw.dimension == "structural"


def test_count_accumulates_for_repeated_target():
    content = ("Regulation (EU) 2016/679 here. And again Regulation (EU) 2016/679. "
               "GDPR once more.")
    refs = extract_cross_references(content, host_key="ai-act")
    gdpr = next(r for r in refs if r.target_key == "gdpr")
    assert gdpr.count >= 2


# --- ND dispatcher ---------------------------------------------------------

def test_crossref_nd_emits_pair_with_cross_document_edge():
    nd = CrossReferenceExtractor()
    content = "The AI system shall comply, without prejudice to Regulation (EU) 2016/679."
    pairs = nd.extract(content, _cls(["ai-act"]), source_document="aiact.txt")
    assert len(pairs) >= 1
    p = next(p for p in pairs if p["solution"]["target_instrument"] == "gdpr")
    edge = p["edges"][0]
    assert edge["subject"] == "ai-act"
    assert edge["object"] == "gdpr"
    assert edge["predicate"] == "without-prejudice"
    assert "dimension" in edge


def test_crossref_nd_no_pairs_when_no_external_refs():
    nd = CrossReferenceExtractor()
    content = "This Regulation 2016/679 only talks about itself. This Regulation applies."
    pairs = nd.extract(content, _cls(["gdpr"]), source_document="gdpr.txt")
    assert all(p["solution"]["target_instrument"] != "gdpr" for p in pairs)
