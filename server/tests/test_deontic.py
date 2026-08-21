# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for RVND's deontic surface.

RVND no longer owns a deontic *language*: the grammar (formula shape, the
``O`` / ``P`` / ``F`` operators, incidents, conflict detection) is consumed
from the ``deontic`` package through :mod:`rvnd.adapters.deontic`. RVND
owns only the *facet extraction* that wires that grammar to its surface reader
(:mod:`rvnd.deontic_facets`). There is no ``R`` operator — a right is
CONSTRUCTED as ``P`` (privilege) or the correlative ``O`` (claim), carried by
the Hohfeld incident.
"""

from __future__ import annotations

import pytest

from rvnd.adapters.deontic import (
    DeonticFormula,
    VALID_OPERATORS,
    OP_OBLIGATION,
    OP_PERMISSION,
    OP_PROHIBITION,
    correlative,
    detect_conflicts,
)
from rvnd.deontic_facets import (
    extract_deontic_pairs,
    extract_formulae,
    formula_from_rule,
)
from rvnd.rule_extractor import RuleFacet


# --- the consumed grammar is O/P/F only ------------------------------------

def test_operator_vocabulary_is_opf_with_no_right():
    assert VALID_OPERATORS == ("O", "P", "F")
    assert "R" not in VALID_OPERATORS


# --- formula model (consumed grammar) --------------------------------------

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


def test_grammar_rejects_unknown_operator():
    # the consumed grammar is strict — an unknown operator is a construction
    # error, not silently coerced. The safe-default fallback lives one layer
    # up, in the modal→operator mapping (see below).
    with pytest.raises(ValueError):
        DeonticFormula(operator="ZZZ", bearer="x", action="y")


def test_to_dict_carries_formula_and_flags():
    f = DeonticFormula(operator=OP_OBLIGATION, bearer="controller",
                       action="act", condition="c", exception="e")
    d = f.to_dict()
    assert d["formula"].startswith("if [c] then O(controller : act)")
    assert d["conditional"] is True
    assert d["defeasible"] is True
    assert d["operator_gloss"] == "obligatory"


# --- mapping from RuleFacet (RVND extractor) -------------------------------

def test_formula_from_rule_maps_catalogued_modals():
    cases = {
        "obligation": OP_OBLIGATION,
        "permission": OP_PERMISSION,
        "prohibition": OP_PROHIBITION,
    }
    for modal, op in cases.items():
        r = RuleFacet(subject="controller", modal=modal, modal_phrase="x",
                      action="do thing", confidence=0.8)
        assert formula_from_rule(r).operator == op


def test_right_is_constructed_not_a_primitive_operator():
    # "right" is not an operator: it lifts to P (a privilege/liberty) carrying
    # a Hohfeld incident whose correlative is what binds the other party.
    r = RuleFacet(subject="data subject", modal="right", modal_phrase="has the right to",
                  action="obtain erasure", confidence=0.8)
    f = formula_from_rule(r)
    assert f.operator == OP_PERMISSION
    assert f.operator != "R"
    assert f.incident, "a right must carry a Hohfeld incident"
    assert correlative(f.incident)  # the incident has a correlative (what binds the counterparty)


def test_formula_from_rule_unknown_modal_falls_back_to_obligation():
    r = RuleFacet(subject="x", modal="weird", modal_phrase="x", action="y",
                  confidence=0.8)
    f = formula_from_rule(r)
    assert f.operator == OP_OBLIGATION          # safe legal default
    assert f.confidence < 0.8                    # uncatalogued modal lowers confidence


# --- extraction over real text (RVND extractor over consumed grammar) ------

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
    assert "R" not in ops


# --- conflict detection (consumed grammar) ---------------------------------

def test_detect_conflicts_flags_obligation_vs_prohibition():
    formulae = [
        DeonticFormula(operator=OP_OBLIGATION, bearer="provider",
                       action="disclose the data", confidence=0.9),
        DeonticFormula(operator=OP_PROHIBITION, bearer="provider",
                       action="disclose the data", confidence=0.8),
    ]
    conflicts = detect_conflicts(formulae)
    assert len(conflicts) == 1
    # the grammar flags candidates; it never resolves them
    assert conflicts[0]["resolution"] == "candidate-escalate"


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


# --- text -> ND pair dicts (RVND extractor; replaces the retired ND) -------

def test_extract_deontic_pairs_emits_formula_pairs_with_edges():
    content = "The controller shall implement appropriate measures where processing occurs."
    pairs = extract_deontic_pairs(content, source_document="gdpr.txt")
    assert len(pairs) >= 1
    p = pairs[0]
    assert p["problem"]["kind"] == "deontic-formula"
    assert p["solution"]["operator"] in (OP_OBLIGATION, OP_PERMISSION, OP_PROHIBITION)
    assert "formula" in p["solution"]
    assert p["edges"]
    # every edge carries a dimension
    assert all("dimension" in e for e in p["edges"])


def test_extract_deontic_pairs_conflict_mode_emits_conflict_pair():
    content = (
        "The provider shall disclose the data. "
        "The provider must not disclose the data."
    )
    pairs = extract_deontic_pairs(content, source_document="x.txt", detect_conflicts=True)
    kinds = {p["problem"].get("kind") for p in pairs}
    assert "deontic-conflict" in kinds


def test_extract_deontic_pairs_are_idempotent_by_content():
    content = "The controller shall act."
    a = extract_deontic_pairs(content, source_document="d.txt")
    b = extract_deontic_pairs(content, source_document="d.txt")
    assert [p["id"] for p in a] == [p["id"] for p in b]
