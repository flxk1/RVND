# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Requisite variety, measured. Operationalises Ashby's Law of
Requisite Variety as a concrete, testable property — and the tests ARE the
concept comparison: a fixed flow has constant regulator variety and fails
when problem variety exceeds it; a derived solver scales its variety with the
problem and meets requisite variety where the fixed flow cannot.

Ashby (An Introduction to Cybernetics, 1956): only variety can destroy
variety — a regulator must command at least as much variety as the
disturbances it absorbs. Here: regulator variety must cover problem variety
(the set of distinct issue-type fingerprints in the input).

Invariants / claims under test (written BEFORE the logic):
  A1  problem variety = count of DISTINCT issue-type fingerprints in the input
  A2  a fixed flow's regulator variety is CONSTANT — its authored step set,
      independent of the input
  A3  requisite_variety(regulator, problem) is True iff the regulator's
      covered issue types ⊇ the problem's issue types
  A4  CONCEPT COMPARISON: a fixed flow meets requisite variety for problems
      within its step set but FAILS for a problem whose variety exceeds it
  A5  CONCEPT COMPARISON: a derived solver (one node per detected issue type,
      plus recalled solvers) meets requisite variety for the SAME problem the
      fixed flow fails — variety scales with the problem
  A6  the uncovered set is reported (which disturbances have no regulator) —
      governance needs to see the gap, not just a boolean
  A7  deterministic
"""
from __future__ import annotations


from workspaces.issue_token import IssueToken, Span, detect_issues
from workspaces.variety import (
    derived_regulator, fixed_flow_regulator, problem_variety,
    requisite_variety,
)


def _tok(itype, rooms=()):
    return IssueToken(issue_id=f"{itype}@0", issue_type=itype, modality="text",
                      span=Span("text", start=0, end=1),
                      norm_anchors=list(rooms), source="", text=itype)


def test_problem_variety_counts_distinct_issue_types():           # A1
    toks = [_tok("liability_cap"), _tok("liability_cap"),
            _tok("data_processing"), _tok("ip_assignment")]
    pv = problem_variety(toks)
    assert pv["variety"] == 3
    assert set(pv["issue_types"]) == {"liability_cap", "data_processing",
                                      "ip_assignment"}


def test_fixed_flow_variety_is_constant():                        # A2
    flow = fixed_flow_regulator(["liability_cap", "data_processing"])
    small = [_tok("liability_cap")]
    big = [_tok("liability_cap"), _tok("ip_assignment"), _tok("termination")]
    # same regulator variety regardless of the input it faces
    assert flow["variety"] == 2
    assert requisite_variety(flow, small)["variety"] == 2
    assert requisite_variety(flow, big)["variety"] == 2


def test_requisite_variety_is_coverage():                         # A3
    flow = fixed_flow_regulator(["liability_cap", "data_processing"])
    covered = [_tok("liability_cap"), _tok("data_processing")]
    assert requisite_variety(flow, covered)["ok"] is True


def test_fixed_flow_fails_when_variety_exceeds_it():              # A4
    flow = fixed_flow_regulator(["liability_cap", "data_processing"])
    problem = [_tok("liability_cap"), _tok("ip_assignment"),
               _tok("good_faith_balancing")]
    rep = requisite_variety(flow, problem)
    assert rep["ok"] is False
    assert set(rep["uncovered"]) == {"ip_assignment", "good_faith_balancing"}


def test_derived_solver_meets_what_fixed_flow_fails():            # A5 (the comparison)
    problem = [_tok("liability_cap"), _tok("ip_assignment"),
               _tok("good_faith_balancing")]
    flow = fixed_flow_regulator(["liability_cap", "data_processing"])
    assert requisite_variety(flow, problem)["ok"] is False        # fixed: fails

    # derived: one solver node per detected issue type (variety scales)
    derived = derived_regulator(problem)
    rep = requisite_variety(derived, problem)
    assert rep["ok"] is True                                      # derived: meets
    assert rep["uncovered"] == []


def test_recall_adds_regulator_variety():                        # A5 cont.
    problem = [_tok("liability_cap")]
    # a recall that supplies a solver for the issue type also counts as variety
    def recall(t):
        return [{"solver": "skill:liability-nd", "evidence": 3}]
    derived = derived_regulator(problem, recall_fn=recall)
    assert "liability_cap" in derived["covered"]
    assert requisite_variety(derived, problem)["ok"] is True


def test_uncovered_set_is_reported_not_just_boolean():           # A6
    flow = fixed_flow_regulator(["liability_cap"])
    rep = requisite_variety(flow, [_tok("liability_cap"), _tok("termination")])
    assert rep["ok"] is False
    assert rep["uncovered"] == ["termination"]
    assert rep["covered"] == ["liability_cap"]


def test_deterministic():                                         # A7
    flow = fixed_flow_regulator(["liability_cap", "data_processing"])
    p = [_tok("ip_assignment"), _tok("liability_cap")]
    assert requisite_variety(flow, p) == requisite_variety(flow, p)


def test_end_to_end_on_detected_tokens():
    snippet = ("5. Liability capped.\n8. Personal data on instructions "
               "(Art. 28 GDPR).\n11. Work product assigned.\n"
               "20. Governing law Germany.\n")
    toks = detect_issues(snippet, domain="contract-de")
    pv = problem_variety(toks)
    assert pv["variety"] >= 3
    # a 2-step fixed flow cannot regulate a 4-variety contract
    flow = fixed_flow_regulator(["liability_cap", "data_processing"])
    assert requisite_variety(flow, toks)["ok"] is False
    # the derived solver does
    assert requisite_variety(derived_regulator(toks), toks)["ok"] is True
