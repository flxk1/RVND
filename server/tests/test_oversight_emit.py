# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Oversight ND OUT face — grounds bundle + doubt dossier.

Pins the non-disturbance and anti-ratification invariants:
- a residual bundle requires origination and never renders as approve/reject;
- a doubt dossier is mandated by footprint/scope, attaches to the human copy;
- the dossier degrades gracefully and never invents what it cannot gather.
"""

from rvnd.oversight_emit import (
    build_grounds_bundle, build_dossier,
    needs_dossier)


def _triple(footprint, action="pay-invoice", agent="bot", verdict="CONDITIONAL"):
    return {"subject": agent, "predicate": verdict, "object": action,
            "footprint": list(footprint), "autonomy_grade": "L3"}


# ── needs_dossier ────────────────────────────────────────────────────────────

def test_dossier_triggered_by_profiling_scope():
    assert needs_dossier(("personal-data",), "credit-scoring") is not None
    assert needs_dossier((), "applicant-profiling") is not None


def test_dossier_triggered_by_high_risk_footprint_combo():
    assert needs_dossier(("personal-data", "irreversible")) is not None


def test_dossier_not_triggered_for_benign():
    assert needs_dossier(("financial",), "pay-invoice") is None
    assert needs_dossier((), "send-mailing") is None


# ── grounds bundle: ratification vs origination ──────────────────────────────

def test_decidable_bundle_is_ratification():
    b = build_grounds_bundle(_triple(("financial",)),
                             grounds=[{"id": "pair:x", "authority_tier": 1}])
    assert b.requires_origination is False
    assert b.connector_payload()["render"] == "ratify"
    assert "options" not in b.connector_payload()


def test_residual_bundle_requires_origination_no_binary():
    opts = [{"id": "a", "label": "retain"}, {"id": "b", "label": "erase"}]
    b = build_grounds_bundle(_triple(("personal-data",), action="erase-record"),
                             options=opts, link_target="workspace://surface/42")
    assert b.requires_origination is True
    payload = b.connector_payload()
    assert payload["render"] == "options"
    assert len(payload["options"]) == 2
    assert payload["link"] == "workspace://surface/42"


def test_reversibility_classes():
    assert build_grounds_bundle(_triple(("irreversible",))).reversibility \
        == "irreversible"
    assert build_grounds_bundle(_triple(("financial",))).reversibility \
        == "window"
    assert build_grounds_bundle(_triple(("personal-data",))).reversibility \
        == "reversible"


def test_bundle_attaches_dossier_on_high_risk():
    b = build_grounds_bundle(
        _triple(("personal-data",), action="loan-eligibility"),
        grounds=[{"id": "p1", "authority_tier": 5, "verified": False}],
        dossier_material={"confidences": [0.6, 0.9],
                          "ood_percentile": 95.0})
    assert b.dossier is not None
    assert b.dossier.trigger
    assert b.connector_payload()["doubt"]["min_confidence"] == 0.6


def test_bundle_no_dossier_when_benign():
    b = build_grounds_bundle(_triple(("financial",)))
    assert b.dossier is None
    assert "doubt" not in b.connector_payload()


# ── doubt dossier assembly ───────────────────────────────────────────────────

def test_weakest_citations_surface_low_tier_and_unverified():
    d = build_dossier(grounds=[
        {"id": "a", "authority_tier": 1, "verified": True},
        {"id": "b", "authority_tier": 5, "verified": True},
        {"id": "c", "authority_tier": 2, "verified": False},
        {"id": "d", "authority_tier": 1, "currency": "superseded"},
    ])
    ids = [g["id"] for g in d.weakest_citations]
    assert "b" in ids and "c" in ids and "d" in ids
    assert "a" not in ids


def test_confidence_profile_flags_below_floor():
    d = build_dossier(confidences=[0.92, 0.61, 0.88, 0.5])
    assert d.confidence_profile["min"] == 0.5
    assert d.confidence_profile["below_floor"] == [0.5, 0.61]


def test_precedent_distance_sorted_nearest_first():
    d = build_dossier(precedents=[
        {"id": "p1", "distance": 0.4}, {"id": "p2", "distance": 0.1},
        {"id": "p3", "distance": 0.8}])
    assert [p["id"] for p in d.precedent_distance] == ["p2", "p1", "p3"]


def test_distributional_position_flags_unusual():
    assert build_dossier(ood_percentile=95.0).distributional_position["unusual"]
    assert not build_dossier(ood_percentile=40.0).distributional_position["unusual"]


def test_override_history_rate():
    d = build_dossier(override_history=[
        {"overridden": True, "reason": "wrong payee"},
        {"overridden": False},
        {"overridden": True, "reason": "amount off"},
        {"overridden": False}])
    assert d.override_history["rate"] == 0.5
    assert "wrong payee" in d.override_history["reasons"]


def test_counterfactual_sensitivity_most_sensitive_first():
    d = build_dossier(decisive_facts=[
        {"fact": "amount", "sensitivity": 0.3},
        {"fact": "payee", "sensitivity": 0.9}])
    assert d.counterfactual_sensitivity[0]["fact"] == "payee"


def test_empty_dossier_is_honest_not_invented():
    d = build_dossier(trigger="high-risk")
    assert d.trigger == "high-risk"
    assert d.weakest_citations == []
    assert d.confidence_profile == {}
    s = d.summary()
    assert s["min_confidence"] is None
    assert s["weakest_citation_count"] == 0


def test_summary_is_compact():
    d = build_dossier(
        grounds=[{"id": "a", "authority_tier": 5, "verified": False}],
        confidences=[0.4], ood_percentile=99.0,
        decisive_facts=[{"fact": "income", "sensitivity": 0.7}],
        blind_spots=["sibling-folder data not visible"],
        trigger="profiling")
    s = d.summary()
    assert s["trigger"] == "profiling"
    assert s["ood_percentile"] == 99.0
    assert s["flip_facts"] == ["income"]
    assert s["blind_spots"] == ["sibling-folder data not visible"]
