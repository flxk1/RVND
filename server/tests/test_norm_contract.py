# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the norm-theory contract — proving each invariant actually fires.

A green run here is the evidence that the contract *forces* the norm-theoretic
guarantees: a conforming pair passes; every omission is caught; discretion,
collision, unknown date and sub-floor confidence escalate (not decide); and a
stated lex-relation is never auto-resolved into a winner.
"""

import pytest

from workspaces.norm_contract import (
    check_pair, enforce, gate, ContractViolation, Level,
)


def conforming_pair() -> dict:
    """A fully norm-theory-conforming rule pair."""
    return {
        "id": "p1",
        "problem": {
            "id": "p1-p",
            "type": "rule",
            "facets": {
                "domain": "ai-act",
                "subject": "provider",
                "modal": "muss",
                "modal_phrase": "muss sicherstellen",
                "has_exception": False,
                "applicability": {"role": "provider", "risk_tier": "high"},
                "jurisdiction": ["EU"],
            },
        },
        "solution": {
            "id": "p1",
            "problem_id": "p1-p",
            "body": "Der Anbieter muss ein Risikomanagementsystem einrichten.",
            "authority_tier": 1,
            "confidence": 0.93,
            "source": "CELEX:32024R1689 Art. 9",
            "temporal": {"status": "in-force", "in_force_from": "2026-08-02", "date_source": "registry"},
        },
        "edges": [],
    }


def test_conforming_pair_passes_clean():
    rep = check_pair(conforming_pair(), risk_class="C")
    assert rep.ok
    assert not rep.escalations
    # every invariant recorded a positive audit line
    codes = {f.code for f in rep.findings if f.level is Level.PASS}
    assert {"NT-1", "NT-2", "NT-3", "NT-4", "NT-5", "NT-6", "NT-7", "NT-8", "NT-9"} <= codes


def test_missing_provenance_is_violation():
    p = conforming_pair(); del p["solution"]["source"]
    rep = check_pair(p)
    assert not rep.ok
    assert any(f.code == "NT-1" for f in rep.violations)


def test_guessed_date_is_violation():
    p = conforming_pair(); p["solution"]["temporal"]["date_source"] = "model"
    rep = check_pair(p)
    assert any(f.code == "NT-2" and "guessed" in f.message for f in rep.violations)


def test_unknown_validity_escalates_not_assumes():
    p = conforming_pair()
    p["solution"]["temporal"] = {"status": "unknown", "date_source": "registry"}
    rep = check_pair(p)
    assert rep.ok  # well-formed
    assert any(f.code == "NT-2" and f.level is Level.ESCALATE for f in rep.escalations)


def test_missing_temporal_block_is_violation():
    p = conforming_pair(); del p["solution"]["temporal"]
    rep = check_pair(p)
    assert any(f.code == "NT-2" for f in rep.violations)


def test_missing_applicability_is_violation():
    p = conforming_pair(); del p["problem"]["facets"]["applicability"]
    rep = check_pair(p)
    assert any(f.code == "NT-3" for f in rep.violations)


def test_rule_without_operator_is_violation():
    p = conforming_pair(); p["problem"]["facets"]["modal"] = ""
    rep = check_pair(p)
    assert any(f.code == "NT-4" for f in rep.violations)


def test_discretionary_modality_escalates():
    p = conforming_pair()
    p["problem"]["facets"]["modal"] = "kann"
    p["problem"]["facets"]["modal_phrase"] = "kann abgesehen werden"
    rep = check_pair(p)
    assert rep.ok
    assert any(f.code == "NT-4" and f.level is Level.ESCALATE for f in rep.escalations)


def test_absorbed_exception_is_violation():
    p = conforming_pair()
    p["solution"]["body"] = "Die Behörde fordert zurück, es sei denn die Einziehung wäre unbillig."
    p["problem"]["facets"]["has_exception"] = False
    rep = check_pair(p)
    assert any(f.code == "NT-5" for f in rep.violations)


def test_flagged_exception_passes():
    p = conforming_pair()
    p["solution"]["body"] = "... es sei denn die Einziehung wäre unbillig."
    p["problem"]["facets"]["has_exception"] = True
    rep = check_pair(p)
    assert rep.ok


def test_missing_authority_tier_is_violation():
    p = conforming_pair(); p["solution"]["authority_tier"] = None
    rep = check_pair(p)
    assert any(f.code == "NT-7" for f in rep.violations)


def test_missing_jurisdiction_is_violation():
    p = conforming_pair(); del p["problem"]["facets"]["jurisdiction"]
    rep = check_pair(p)
    assert any(f.code == "NT-8" for f in rep.violations)


def test_unscoped_jurisdiction_is_allowed():
    p = conforming_pair(); p["problem"]["facets"]["jurisdiction"] = "unscoped"
    rep = check_pair(p)
    assert rep.ok


def test_subfloor_confidence_escalates_on_class_c_only():
    p = conforming_pair(); p["solution"]["confidence"] = 0.5
    assert any(f.code == "NT-9" and f.level is Level.ESCALATE
               for f in check_pair(p, risk_class="C").escalations)
    # same pair on class-B does not escalate on confidence
    assert not any(f.code == "NT-9" and f.level is Level.ESCALATE
                   for f in check_pair(p, risk_class="B").escalations)


def test_genuine_conflict_escalates():
    p = conforming_pair()
    p["solution"]["predicate"] = "may-conflict-with"
    p["solution"]["resolution"] = "genuine-conflict-escalate"
    rep = check_pair(p)
    assert any(f.code == "NT-6" and f.level is Level.ESCALATE for f in rep.escalations)


def test_auto_resolved_conflict_is_violation():
    """A conflict that carries a derived winner violates the no-resolution rule."""
    p = conforming_pair()
    p["solution"]["predicate"] = "may-conflict-with"
    p["solution"]["resolution"] = "a-overrides-b"
    rep = check_pair(p)
    assert any(f.code == "NT-6" and f.level is Level.VIOLATION for f in rep.violations)


def test_unbound_meta_relation_is_violation():
    """A stated lex-specialis edge with no provenance is rejected."""
    p = conforming_pair()
    p["edges"] = [{"subject": "p1", "predicate": "lex-specialis-to", "object": "X", "dimension": "structural"}]
    rep = check_pair(p)
    assert any(f.code == "NT-6" for f in rep.violations)


def test_bound_meta_relation_passes():
    p = conforming_pair()
    p["edges"] = [{"subject": "p1", "predicate": "supersedes", "object": "X",
                   "dimension": "structural", "source": "CELEX:... Art. 99"}]
    rep = check_pair(p)
    assert rep.ok


def test_gate_raises_on_violation():
    bad = conforming_pair(); del bad["solution"]["source"]
    with pytest.raises(ContractViolation):
        gate([bad])


def test_gate_passes_and_returns_escalations():
    p = conforming_pair(); p["solution"]["temporal"]["status"] = "unknown"
    rep = gate([p], risk_class="C")  # no violation -> returns
    assert rep.must_escalate


def test_enforce_aggregates_over_pairs():
    good = conforming_pair()
    bad = conforming_pair(); bad["id"] = "p2"; del bad["problem"]["facets"]["jurisdiction"]
    rep = enforce([good, bad])
    assert not rep.ok
    assert any(f.pair_id == "p2" and f.code == "NT-8" for f in rep.violations)
