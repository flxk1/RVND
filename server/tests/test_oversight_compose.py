# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fingerprint composition + separation of duties (L4)."""

from workspaces.oversight_extractor import OversightFacet
from workspaces.oversight_compose import (
    ControlChange, compose_facets, binds_grade,
    check_separation, approves_clean)


def _f(min_level="", ceiling="", personal=False, overseer="", measure="",
       cadence="", raw="s"):
    return OversightFacet(min_level=min_level, grade_ceiling=ceiling,
                          personal=personal, overseer=overseer, measure=measure,
                          cadence=cadence, raw_sentence=raw)


# ── composition ──────────────────────────────────────────────────────────────

def test_floor_joins_strictest_wins():
    c = compose_facets([_f(min_level="NOTIFY", raw="a"),
                        _f(min_level="APPROVE", raw="b"),
                        _f(min_level="REVIEW", raw="c")])
    assert c.min_level == "APPROVE"
    assert c.contributing == 3


def test_ceiling_meets_lowest_wins():
    c = compose_facets([_f(ceiling="L3", raw="a"), _f(ceiling="L1", raw="b")])
    assert c.grade_ceiling == "L1"


def test_personal_ors():
    c = compose_facets([_f(raw="a"), _f(personal=True, raw="b")])
    assert c.personal is True


def test_measures_and_cadences_union_dedup():
    c = compose_facets([
        _f(measure="log overrides", cadence="quarterly", raw="a"),
        _f(measure="log overrides", cadence="annually", raw="b"),
        _f(measure="record inputs", raw="c")])
    assert c.measures == ["log overrides", "record inputs"]
    assert c.cadences == ["quarterly", "annually"]


def test_empty_compose_is_empty_constraint():
    c = compose_facets([])
    assert c.min_level == "" and c.grade_ceiling == "" and c.contributing == 0


def test_compose_is_order_independent():
    a = compose_facets([_f(min_level="NOTIFY", raw="x"),
                        _f(min_level="MANUAL", raw="y")])
    b = compose_facets([_f(min_level="MANUAL", raw="y"),
                        _f(min_level="NOTIFY", raw="x")])
    assert a.min_level == b.min_level == "MANUAL"


def test_compose_idempotent():
    f = _f(min_level="APPROVE", ceiling="L2", personal=True, raw="z")
    once = compose_facets([f])
    twice = compose_facets([f, f])
    assert once.min_level == twice.min_level
    assert once.grade_ceiling == twice.grade_ceiling
    assert once.personal == twice.personal


def test_binds_grade_meets_ceiling():
    c = compose_facets([_f(ceiling="L2", raw="a")])
    assert binds_grade(c, "L4") == "L2"
    assert binds_grade(c, "L1") == "L1"


def test_binds_grade_uncapped_passes_through():
    c = compose_facets([_f(min_level="REVIEW", raw="a")])  # no ceiling
    assert binds_grade(c, "L4") == "L4"


# ── separation of duties (L4) ────────────────────────────────────────────────

def test_self_approval_blocked():
    ch = ControlChange(kind="standing-approval-grant", requested_by="bot",
                       approver="bot")
    assert check_separation(ch) is not None


def test_self_widening_blocked():
    ch = ControlChange(kind="learnable-scope-widen", requested_by="ops",
                       target_agent="bot", approver="bot")
    assert "widens its own" in check_separation(ch)


def test_routing_change_requires_human_controller():
    ch = ControlChange(kind="routing-change", requested_by="bot",
                       approver="ops", approver_is_human_controller=False)
    assert "controls" in check_separation(ch)
    ok = ControlChange(kind="routing-change", requested_by="bot",
                       approver="alice", approver_is_human_controller=True)
    assert approves_clean(ok)


def test_grade_promotion_is_routing_like():
    ch = ControlChange(kind="grade-promotion", requested_by="bot",
                       target_agent="bot", approver="alice",
                       approver_is_human_controller=True)
    assert approves_clean(ch)


def test_standing_widen_clean_when_independent_approver():
    ch = ControlChange(kind="standing-approval-widen", requested_by="bot",
                       target_agent="bot", approver="alice")
    assert approves_clean(ch)


def test_missing_approver_blocked():
    ch = ControlChange(kind="policy-change", requested_by="bot", approver="")
    assert "no named approver" in check_separation(ch)


def test_non_control_change_passes():
    ch = ControlChange(kind="pay-invoice", requested_by="bot", approver="bot")
    assert check_separation(ch) is None


def test_compound_attack_4x10_blocked():
    # Saturated approver == the agent laundering its own promotion.
    ch = ControlChange(kind="grade-promotion", requested_by="bot",
                       target_agent="bot", approver="bot",
                       approver_is_human_controller=True)
    assert check_separation(ch) is not None
