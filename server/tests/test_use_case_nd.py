# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the use-case ND: prose→facets (front door), JSON (side door),
self-gating, and subsumption against duty pairs.

Closes the three-doc test's missing-join finding: a POC description now lands in
the canonical facet shape and subsumes under ingested duties.
"""
from __future__ import annotations

import pytest

from workspaces.use_case_nd import (
    extract_use_case_facets, facets_from_json, looks_like_use_case,
    subsume, UseCaseND, FOOTPRINT_TAGS,
)

POC = """# POC — candidate-screening assistant
We are building a proof of concept: an AI assistant that screens incoming job
applications, scores candidates, and ranks the top twenty for the recruiter.
Rejected applications are auto-archived. It runs overnight without supervision
and sends rejection emails automatically. Deployment target: production pilot
with two recruiters overseeing roughly 400 applications per week. We have not
yet decided how the recruiters will be trained.
"""

STATUTE = """Article 14(1). High-risk AI systems shall be designed and developed
in such a way that they can be effectively overseen by natural persons.
Article 26(2). Deployers shall assign human oversight to natural persons who
have the necessary competence, training and authority."""

STANDARD = """Clause 6.1. The provider shall document the oversight measures
selected for each identified risk. Clause 7.2. If the system operates above the
defined autonomy threshold, the deployer must conduct a documented review."""


# ---- self-gate -----------------------------------------------------------

def test_self_gate_fires_on_use_case():
    assert looks_like_use_case(POC) >= 0.6


def test_self_gate_silent_on_statute():
    assert looks_like_use_case(STATUTE) == 0.0


def test_self_gate_silent_on_standard():
    assert looks_like_use_case(STANDARD) == 0.0


def test_nd_returns_nothing_for_statute():
    nd = UseCaseND()

    class _C:
        primary_type = "unknown"; facets = []; confidence = 0.3; metadata = {}
    assert nd.extract(STATUTE, _C()) == []


# ---- front door: prose → facets -----------------------------------------

def test_extract_core_facets():
    f = extract_use_case_facets(POC)
    assert f.lifecycle_stage == "poc"
    assert "employment-screening" in f.purpose_tags
    assert f.autonomy_grade == "L4"          # "overnight without supervision"
    assert "job applicants" in f.affected_parties
    assert f.affected_party_scale == 400


def test_extract_footprint_multi_including_cross_line_email():
    f = extract_use_case_facets(POC)
    # personal data (applicants/CV), irreversible (auto-archive), external-publish
    # (sends rejection emails — across a line break in the source).
    assert "personal-data" in f.footprint
    assert "external-publish" in f.footprint
    assert "irreversible" in f.footprint
    for tag in f.footprint:
        assert tag in FOOTPRINT_TAGS


def test_benign_internal_tool_stays_minimal():
    benign = ("We are building a POC: an internal dashboard that summarises our "
              "own sales numbers for the team. Runs on request.")
    f = extract_use_case_facets(benign)
    assert "external-publish" not in f.footprint
    assert f.autonomy_grade == "L1"


def test_each_facet_carries_provenance():
    f = extract_use_case_facets(POC)
    assert "purpose_tags" in f.provenance
    assert f.provenance["purpose_tags"]["origin"] == "extracted"
    assert 0.0 < f.provenance["purpose_tags"]["confidence"] <= 1.0
    assert f.provenance["purpose_tags"]["source_span"]


# ---- side door: JSON → facets -------------------------------------------

def test_json_side_door_happy_path():
    f = facets_from_json({
        "system_name": "x", "role": "deployer",
        "footprint": ["personal-data", "external-publish"],
        "autonomy_grade": "L4", "purpose_tags": ["employment-screening"],
    })
    assert f.role == "deployer"
    assert f.provenance["role"]["origin"] == "provided"
    assert f.provenance["role"]["confidence"] == 1.0


def test_json_side_door_rejects_bad_enum():
    with pytest.raises(ValueError):
        facets_from_json({"system_name": "x", "role": "overlord",
                          "footprint": []})


def test_json_side_door_rejects_bad_footprint():
    with pytest.raises(ValueError):
        facets_from_json({"system_name": "x", "role": "deployer",
                          "footprint": ["world-domination"]})


def test_json_side_door_requires_keys():
    with pytest.raises(ValueError):
        facets_from_json({"role": "deployer", "footprint": []})


# ---- subsumption ---------------------------------------------------------

REGIME = {"footprint_instruments": {
    "personal-data": ["GDPR (processing; Art. 28 if processor)"],
    "external-publish": ["AI Act Art. 50 (disclosure to affected persons)"],
}}

DUTY_PAIRS = [
    {"solution": {"confidence": 0.8, "rule": {
        "subject": "providers", "modal": "obligation",
        "action": "ensure natural persons are informed they interact with AI"}}},
    {"solution": {"confidence": 0.7, "rule": {
        "subject": "the deployer", "modal": "obligation",
        "action": "conduct a documented review"}}},
]


def test_subsume_footprint_to_instruments():
    f = facets_from_json({"system_name": "x", "role": "provider",
                          "footprint": ["personal-data", "external-publish"]})
    links = subsume(f, [], REGIME)
    titles = " ".join(l.title for l in links if l.kind == "duty")
    assert "GDPR" in titles
    assert "Art. 50" in titles


def test_subsume_role_matches_duties():
    f = facets_from_json({"system_name": "x", "role": "provider",
                          "footprint": []})
    links = subsume(f, DUTY_PAIRS, REGIME)
    assert any("provider" in l.title for l in links if l.kind == "duty")


def test_subsume_high_risk_is_residual_not_asserted():
    f = facets_from_json({"system_name": "x", "role": "deployer",
                          "footprint": ["personal-data"],
                          "purpose_tags": ["employment-screening"]})
    res = [l for l in subsume(f, [], REGIME) if l.kind == "residual"]
    assert res and res[0].confidence == 0.0   # human decides; never asserted


def test_subsume_flags_oversight_gap():
    f = facets_from_json({"system_name": "x", "role": "deployer",
                          "footprint": ["personal-data"],
                          "purpose_tags": ["employment-screening"],
                          "autonomy_grade": "L4", "human_review": "none",
                          "overseer_competence": "unspecified"})
    gaps = [l for l in subsume(f, [], REGIME) if l.kind == "oversight-gap"]
    assert gaps


def test_subsume_dedups_upstream_quadruplication():
    # Same duty stored 4× (the known domain-ND over-fire) must collapse to 1.
    dup = DUTY_PAIRS[:1] * 4
    f = facets_from_json({"system_name": "x", "role": "provider", "footprint": []})
    links = [l for l in subsume(f, dup, REGIME) if l.kind == "duty"]
    assert len(links) == 1
