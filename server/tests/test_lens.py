# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The Lens (USP 2) — learning admission, scopes, precedents, update budget.

Pins the guard-not-teacher model:
- default-deny: an uncovered class is HELD, never auto-admitted;
- the hard floor (protected attributes, escalated residuals) is REJECTED
  no matter what the scope says;
- no provenance / low confidence / novel teacher → HOLD;
- a precedent binds one case unless declared learnable; it never stamps `user`;
- the update budget refuses to spend past the cap (re-gate instead).
"""

import pytest

from workspaces.lens import (
    Admission, LearningObject, LearningScope, Precedent, UpdateBudget,
    classify_admission, select_precedent)


def _obj(cls="style-pref", **kw):
    base = dict(cls=cls, content_hash="h1", source_actor="alice",
                signature="sig", confidence=0.9, magnitude=1.0)
    base.update(kw)
    return LearningObject(**base)


# ── scope dispositions ───────────────────────────────────────────────────────

def test_allow_class_admits():
    scope = LearningScope(allow=frozenset({"style-pref"}))
    v = classify_admission(_obj("style-pref"), scope)
    assert v.admission is Admission.ADMIT
    assert v.aggregate_only is False


def test_uncovered_class_is_held_default_deny():
    scope = LearningScope(allow=frozenset({"style-pref"}))
    v = classify_admission(_obj("pricing-pattern"), scope)
    assert v.admission is Admission.HOLD
    assert "default-deny" in v.reason


def test_aggregate_only_admits_with_flag():
    scope = LearningScope(aggregate_only=frozenset({"volume-pattern"}))
    v = classify_admission(_obj("volume-pattern"), scope)
    assert v.admission is Admission.ADMIT
    assert v.aggregate_only is True


def test_forbidden_class_rejected():
    scope = LearningScope(forbid=frozenset({"payee-identity"}))
    v = classify_admission(_obj("payee-identity"), scope)
    assert v.admission is Admission.REJECT


def test_hard_floor_rejected_even_if_allowed():
    # User tries to allow a protected-attribute class; the floor overrides.
    scope = LearningScope(allow=frozenset({"protected-attribute"}))
    v = classify_admission(_obj("protected-attribute"), scope)
    assert v.admission is Admission.REJECT
    assert "protected-attribute" in scope.forbid
    assert "protected-attribute" not in scope.allow


def test_escalated_residual_is_hard_forbidden():
    scope = LearningScope(allow=frozenset({"escalated-residual"}))
    v = classify_admission(_obj("escalated-residual"), scope)
    assert v.admission is Admission.REJECT


# ── provenance / confidence / teacher gates ──────────────────────────────────

def test_missing_provenance_holds():
    scope = LearningScope(allow=frozenset({"style-pref"}))
    v = classify_admission(_obj("style-pref", source_actor="", signature=""),
                           scope)
    assert v.admission is Admission.HOLD
    assert "no-provenance" in v.triggers


def test_low_confidence_holds():
    scope = LearningScope(allow=frozenset({"style-pref"}))
    v = classify_admission(_obj("style-pref", confidence=0.5), scope)
    assert v.admission is Admission.HOLD
    assert any("confidence" in t for t in v.triggers)


def test_novel_teacher_holds():
    scope = LearningScope(allow=frozenset({"style-pref"}))
    v = classify_admission(_obj("style-pref", source_actor="mallory"),
                           scope, known_teachers=["alice", "bob"])
    assert v.admission is Admission.HOLD
    assert any("novel teacher" in t for t in v.triggers)


def test_known_teacher_admits():
    scope = LearningScope(allow=frozenset({"style-pref"}))
    v = classify_admission(_obj("style-pref", source_actor="alice"),
                           scope, known_teachers=["alice", "bob"])
    assert v.admission is Admission.ADMIT


def test_forbid_beats_missing_provenance():
    # Refusal order: forbidden class is rejected before provenance is checked.
    scope = LearningScope(forbid=frozenset({"x"}))
    v = classify_admission(_obj("x", source_actor="", signature=""), scope)
    assert v.admission is Admission.REJECT


# ── precedents ───────────────────────────────────────────────────────────────

def _prec(**kw):
    base = dict(id="p1", query_features={"kind": "termination"},
                chosen_option="retain", rationale="bestseller clause",
                actor="alice")
    base.update(kw)
    return Precedent(**base)


def test_precedent_binds_one_case_unless_learnable():
    p = _prec(learnable=False)
    assert p.applies_to({}, similarity=1.0) is False


def test_learnable_precedent_applies_above_threshold():
    p = _prec(learnable=True, similarity_threshold=0.9)
    assert p.applies_to({}, similarity=0.95) is True
    assert p.applies_to({}, similarity=0.85) is False


def test_revoked_precedent_never_applies():
    p = _prec(learnable=True, revoked=True)
    assert p.applies_to({}, similarity=1.0) is False


def test_expired_precedent_never_applies():
    p = _prec(learnable=True, expires_at=1000.0)
    assert p.applies_to({}, similarity=1.0, now=1001.0) is False
    assert p.applies_to({}, similarity=1.0, now=999.0) is True


def test_precedent_stamp_is_never_user():
    p = _prec(learnable=True)
    assert p.actor_stamp() == "agent-under-lens(precedent:p1)"
    assert "user" not in p.actor_stamp()


def test_select_precedent_picks_highest_applicable():
    p1 = _prec(id="p1", learnable=True, similarity_threshold=0.8)
    p2 = _prec(id="p2", learnable=True, similarity_threshold=0.8)
    p3 = _prec(id="p3", learnable=False)
    chosen = select_precedent({}, [(p1, 0.85), (p2, 0.95), (p3, 0.99)])
    assert chosen[0].id == "p2"


def test_select_precedent_none_when_all_below_threshold():
    p1 = _prec(id="p1", learnable=True, similarity_threshold=0.95)
    assert select_precedent({}, [(p1, 0.9)]) is None


# ── update budget ────────────────────────────────────────────────────────────

def test_budget_consumes_until_cap():
    b = UpdateBudget(cap=3.0)
    assert b.consume(1.0) is True
    assert b.consume(1.5) is True
    assert b.would_exceed(1.0) is True
    assert b.consume(1.0) is False           # refuses to spend past cap
    assert b.spent == 2.5                     # unchanged by the refused consume


def test_budget_replay_from_admitted():
    b = UpdateBudget.from_admitted(10.0, [
        {"magnitude": 2.0}, {"magnitude": 3.0}, {"magnitude": None}])
    assert b.spent == 5.0


def test_budget_cap_validated():
    with pytest.raises(ValueError):
        UpdateBudget(cap=0)


def test_scope_roundtrips_to_dict():
    scope = LearningScope(allow=frozenset({"a"}),
                          aggregate_only=frozenset({"b"}),
                          forbid=frozenset({"c"}))
    d = scope.to_dict()
    assert d["allow"] == ["a"]
    assert "c" in d["forbid"]
    assert "protected-attribute" in d["forbid"]    # hard floor always present
