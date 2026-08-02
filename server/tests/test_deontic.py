# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the deontic-logic formalisation layer (deontic.py)."""

from __future__ import annotations

from workspaces.deontic import (
    DeonticFormula,
    DeonticFormulaND,
    OP_OBLIGATION,
    OP_PERMISSION,
    OP_PROHIBITION,
    OP_RIGHT,
    detect_conflicts,
    extract_formulae,
    formula_from_rule,
)
from workspaces.nd_routing import Classification
from workspaces.rule_extractor import RuleFacet


def _normative_classification(facets=None):
    return Classification(primary_type="normative", facets=facets or [],
                          confidence=0.9, metadata={})


# --- formula model ---------------------------------------------------------

def test_obligation_renders_core_with_bearer_and_action():
    f = DeonticFormula(operator=OP_OBLIGATION, bearer="controller",
                       action="implement measures")
    assert f.core() == "O(controller : implement measures)"
    assert f.render() == "O(controller : implement measures)"


def test_conditional_obligation_renders_if_then():
    f = DeonticFormula(operator=OP_OBLIGATION, bearer="controller",
                       action="notify the authority", condition="a breach occurs")
    assert f.render() == "if [a breach occurs] then O(controller : notify the authority)"


def test_exception_renders_unless():
    f = DeonticFormula(operator=OP_OBLIGATION, bearer="provider",
                       action="register the system", exception="Art. 11 applies")
    assert f.render().endswith("unless [Art. 11 applies]")


def test_prohibition_dual_is_o_not():
    f = DeonticFormula(operator=OP_PROHIBITION, bearer="processor",
                       action="engage another processor")
    assert f.dual() == "O(¬ engage another processor)"


def test_permission_dual_is_not_o_not():
    f = DeonticFormula(operator=OP_PERMISSION, bearer="user", action="object")
    assert f.dual() == "¬O(¬ object)"


def test_unknown_operator_falls_back_to_obligation():
    f = DeonticFormula(operator="ZZZ", bearer="x", action="y")
    assert f.operator == OP_OBLIGATION


def test_to_dict_carries_formula_and_flags():
    f = DeonticFormula(operator=OP_OBLIGATION, bearer="controller",
                       action="act", condition="c", exception="e")
    d = f.to_dict()
    assert d["formula"].startswith("if [c] then O(controller : act)")
    assert d["conditional"] is True
    assert d["defeasible"] is True
    assert d["operator_gloss"] == "obligatory"


# --- mapping from RuleFacet ------------------------------------------------

def test_formula_from_rule_maps_modal_classes():
    cases = {
        "obligation": OP_OBLIGATION,
        "permission": OP_PERMISSION,
        "prohibition": OP_PROHIBITION,
        "right": OP_RIGHT,
    }
    for modal, op in cases.items():
        r = RuleFacet(subject="controller", modal=modal, modal_phrase="x",
                      action="do thing", confidence=0.8)
        assert formula_from_rule(r).operator == op


def test_formula_from_rule_unknown_modal_lowers_confidence():
    r = RuleFacet(subject="x", modal="weird", modal_phrase="x", action="y",
                  confidence=0.8)
    f = formula_from_rule(r)
    assert f.operator == OP_OBLIGATION
    assert f.confidence < 0.8


# --- extraction over real text ---------------------------------------------

def test_extract_formulae_from_gdpr_text():
    content = (
        "The controller shall implement appropriate technical and "
        "organisational measures. The processor must not engage another "
        "processor without prior authorisation."
    )
    formulae = extract_formulae(content)
    ops = {f.operator for f in formulae}
    assert OP_OBLIGATION in ops
    assert OP_PROHIBITION in ops


# --- conflict detection ----------------------------------------------------

def test_detect_conflicts_flags_obligation_vs_prohibition():
    formulae = [
        DeonticFormula(operator=OP_OBLIGATION, bearer="provider",
                       action="disclose the data", confidence=0.9),
        DeonticFormula(operator=OP_PROHIBITION, bearer="provider",
                       action="disclose the data", confidence=0.8),
    ]
    conflicts = detect_conflicts(formulae)
    assert len(conflicts) == 1
    assert conflicts[0]["resolution"] == "genuine-conflict-escalate"


def test_no_conflict_when_actions_differ():
    formulae = [
        DeonticFormula(operator=OP_OBLIGATION, bearer="provider", action="disclose"),
        DeonticFormula(operator=OP_PROHIBITION, bearer="provider", action="delete"),
    ]
    assert detect_conflicts(formulae) == []


def test_no_conflict_for_two_obligations():
    formulae = [
        DeonticFormula(operator=OP_OBLIGATION, bearer="x", action="a"),
        DeonticFormula(operator=OP_OBLIGATION, bearer="x", action="a"),
    ]
    assert detect_conflicts(formulae) == []


# --- ND dispatcher ---------------------------------------------------------

def test_deontic_nd_handles_normative():
    nd = DeonticFormulaND()
    assert nd.can_handle(_normative_classification(["gdpr"])) is True


def test_deontic_nd_emits_formula_pairs_with_edges():
    nd = DeonticFormulaND()
    content = "The controller shall implement appropriate measures where processing occurs."
    pairs = nd.extract(content, _normative_classification(["gdpr"]),
                       source_document="gdpr.txt")
    assert len(pairs) >= 1
    p = pairs[0]
    assert p["problem"]["kind"] == "deontic-formula"
    assert p["solution"]["operator"] in (OP_OBLIGATION, OP_PERMISSION,
                                         OP_PROHIBITION, OP_RIGHT)
    assert "formula" in p["solution"]
    assert p["edges"]
    # every edge carries a dimension
    assert all("dimension" in e for e in p["edges"])


def test_deontic_nd_conflict_mode_emits_conflict_pair():
    nd = DeonticFormulaND(detect_conflicts=True)
    content = (
        "The provider shall disclose the data. "
        "The provider must not disclose the data."
    )
    pairs = nd.extract(content, _normative_classification(["ai-act"]),
                       source_document="x.txt")
    kinds = {p["problem"].get("kind") for p in pairs}
    assert "deontic-conflict" in kinds


def test_deontic_nd_pairs_are_idempotent_by_content():
    nd = DeonticFormulaND()
    content = "The controller shall act."
    a = nd.extract(content, _normative_classification(["gdpr"]), source_document="d.txt")
    b = nd.extract(content, _normative_classification(["gdpr"]), source_document="d.txt")
    assert [p["id"] for p in a] == [p["id"] for p in b]
