# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the required-artifact extractor (instrument_obligation_extractor.py)."""

from __future__ import annotations

from rvnd.instrument_obligation_extractor import (
    RequiredArtifactExtractor,
    extract_required_artifacts,
)
from rvnd.nd_routing import Classification


def _cls(facets=None):
    return Classification(primary_type="normative", facets=facets or [],
                          confidence=0.9, metadata={})


# --- extraction ------------------------------------------------------------

def test_ropa_detected_from_record_of_processing():
    content = "The controller shall maintain a record of processing activities."
    arts = extract_required_artifacts(content)
    keys = {a.key for a in arts}
    assert "ropa" in keys
    ropa = next(a for a in arts if a.key == "ropa")
    assert ropa.obligated is True
    assert ropa.category == "register"


def test_dpia_detected():
    content = ("Where processing is likely to result in a high risk, the "
               "controller shall carry out a data protection impact assessment.")
    arts = extract_required_artifacts(content)
    assert any(a.key == "dpia" for a in arts)


def test_conformity_assessment_detected_ai_act():
    content = ("Providers of high-risk AI systems shall ensure their system "
               "undergoes the relevant conformity assessment.")
    arts = extract_required_artifacts(content)
    assert any(a.key == "conformity-assessment" for a in arts)


def test_dpo_appointment_detected():
    content = "The controller shall designate a data protection officer."
    arts = extract_required_artifacts(content)
    dpo = next(a for a in arts if a.key == "dpo")
    assert dpo.category == "appointment"


def test_bare_mention_lower_confidence_than_obligated():
    obligated = extract_required_artifacts(
        "The controller shall maintain a record of processing activities.")
    bare = extract_required_artifacts(
        "This paper discusses the record of processing activities in general.")
    o = next(a for a in obligated if a.key == "ropa")
    b = next(a for a in bare if a.key == "ropa")
    assert o.confidence > b.confidence
    assert o.obligated is True
    assert b.obligated is False


def test_dedup_one_pair_per_artifact():
    content = ("The controller shall carry out a data protection impact "
               "assessment. A data protection impact assessment shall be "
               "reviewed periodically.")
    arts = extract_required_artifacts(content)
    assert sum(1 for a in arts if a.key == "dpia") == 1


def test_no_false_positive_on_unrelated_text():
    content = "The band played a great set at the festival last night."
    assert extract_required_artifacts(content) == []


# --- ND dispatcher ---------------------------------------------------------

def test_artifact_nd_emits_pairs_with_structural_edges():
    nd = RequiredArtifactExtractor()
    content = ("The controller shall maintain a record of processing "
               "activities and designate a data protection officer.")
    pairs = nd.extract(content, _cls(["gdpr"]), source_document="gdpr.txt")
    keys = {p["solution"]["artifact"] for p in pairs}
    assert "ropa" in keys and "dpo" in keys
    p = pairs[0]
    assert p["problem"]["kind"] == "required-artifact"
    assert any(e["predicate"] == "required-by" for e in p["edges"])
    assert all("dimension" in e for e in p["edges"])


def test_artifact_nd_scope_from_facet():
    nd = RequiredArtifactExtractor()
    content = "The controller shall carry out a data protection impact assessment."
    pairs = nd.extract(content, _cls(["gdpr"]), source_document="g.txt")
    assert pairs[0]["problem"]["scope"] == "gdpr"
