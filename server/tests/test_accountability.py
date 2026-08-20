# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Accountability guard: the boundary between essential variety
(warranted, reproducible, attributable — fine however complex) and arbitrary
variety (unwarranted or non-deterministic — an accountability violation).

The point: high variety is a problem only when responsibility becomes
deviant through computational ARBITRARINESS, not when the function is genuinely
complex. Brooks' essential vs accidental complexity; the non-arbitrariness
standard. The guard admits essential variety, flags accidental variety, and
reports complexity SEPARATELY from arbitrariness so the two are never conflated.

Claims under test (written BEFORE the logic):
  C1  a fully-warranted deterministic structure is accountable REGARDLESS of
      size — complexity alone is never a violation
  C2  a node with no warrant is 'unwarranted-variety' — not accountable
  C3  a warrant outside the known kinds is flagged
  C4  complexity (node count) is reported apart from accountability — a large
      warranted structure is accountable AND high-complexity
  C5  a non-reproducible structure (same input → different topology) is
      'nondeterministic-structure' — the core arbitrariness failure
  C6  variety amplification is measurable: covered variety GROWS as retention
      supplies solvers (building variety by learning, each unit warranted
      'recalled')
  C7  the audit is deterministic
"""
from __future__ import annotations


from workspaces.solver_topology import SolverNode
from workspaces.accountability import audit_accountability, amplification_curve


def _n(i, warrant="detected", kind="solve", grade="auto"):
    return SolverNode(i, fingerprint={"issue_type": i}, solver=f"skill:{i}",
                      kind=kind, grade=grade, warrant=warrant)


def test_warranted_deterministic_is_accountable_at_any_size():    # C1 + C4
    nodes = [_n(f"issue{k}") for k in range(50)]
    rep = audit_accountability(nodes)
    assert rep["accountable"] is True
    assert rep["complexity"] == 50            # complexity reported, not penalised
    assert rep["warranted"] == 50
    assert rep["findings"] == []


def test_unwarranted_node_is_not_accountable():                   # C2
    nodes = [_n("a"), _n("b", warrant="")]    # b just appears
    rep = audit_accountability(nodes)
    assert rep["accountable"] is False
    assert any(f["kind"] == "unwarranted-variety" and f["id"] == "b"
               for f in rep["findings"])


def test_unknown_warrant_kind_is_flagged():                       # C3
    nodes = [_n("a", warrant="because-i-said-so")]
    rep = audit_accountability(nodes)
    assert any(f["kind"] == "unknown-warrant" for f in rep["findings"])
    assert rep["accountable"] is False


def test_complexity_is_separate_from_arbitrariness():             # C4
    # one arbitrary node in an otherwise large, legitimate structure
    nodes = [_n(f"issue{k}") for k in range(20)] + [_n("ghost", warrant="")]
    rep = audit_accountability(nodes)
    assert rep["complexity"] == 21            # complexity high — fine
    assert rep["accountable"] is False        # but ONE arbitrary node fails it
    assert rep["warranted"] == 20


def test_nondeterministic_structure_is_flagged():                 # C5
    nodes = [_n("a")]
    calls = {"n": 0}
    def reproduce():
        # same 'input', different structure each call → arbitrary
        calls["n"] += 1
        extra = [_n("b")] if calls["n"] % 2 == 0 else []
        return nodes + extra
    rep = audit_accountability(nodes, reproduce_fn=reproduce)
    assert rep["accountable"] is False
    assert any(f["kind"] == "nondeterministic-structure"
               for f in rep["findings"])


def test_reproducible_structure_passes_determinism():             # C5 ok-side
    nodes = [_n("a"), _n("b")]
    rep = audit_accountability(nodes, reproduce_fn=lambda: [_n("a"), _n("b")])
    assert not any(f["kind"] == "nondeterministic-structure"
                   for f in rep["findings"])
    assert rep["accountable"] is True


def test_variety_amplification_is_measurable():                   # C6
    # start with detectors covering 1 type; retention adds 2 more over time
    base_types = ["liability_cap"]
    retained = [["data_processing"], ["ip_assignment"]]
    curve = amplification_curve(base_types, retained)
    assert curve == [1, 2, 3]                 # V(R) grows by learning


def test_audit_is_deterministic():                               # C7
    nodes = [_n("a"), _n("b")]
    assert audit_accountability(nodes) == audit_accountability(nodes)
