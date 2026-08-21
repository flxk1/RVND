# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Property tests for the control-form algebra (panel/Lamport adoption:
'the algebra ships as tests, not prose'). Exhaustive over the named
vocabulary — small enough that randomization would be theater."""
from __future__ import annotations

import itertools

import pytest

from rvnd.controlforms import (
    FORMS, comparable, compose, compose_all, from_traffic_light,
    guarantees, leq, name_of,
)

NAMES = sorted(FORMS)


# --- partial order axioms ---------------------------------------------------

@pytest.mark.parametrize("a", NAMES)
def test_reflexive(a):
    assert leq(a, a)


@pytest.mark.parametrize("a,b", list(itertools.product(NAMES, NAMES)))
def test_antisymmetric(a, b):
    if leq(a, b) and leq(b, a):
        assert guarantees(a) == guarantees(b) or "block" in (a, b) and a == b


@pytest.mark.parametrize("a,b,c", list(itertools.product(NAMES, NAMES, NAMES)))
def test_transitive(a, b, c):
    if leq(a, b) and leq(b, c):
        assert leq(a, c)


def test_four_eyes_and_expert_review_incomparable():
    """The panel's predicted discovery, pinned: quantity vs competence."""
    assert not comparable("four_eyes", "expert_review")


def test_block_is_top_auto_is_bottom():
    for n in NAMES:
        assert leq(n, "block")
        assert leq("auto", n)


# --- conjunction properties --------------------------------------------------

@pytest.mark.parametrize("a,b", list(itertools.product(NAMES, NAMES)))
def test_commutative(a, b):
    assert compose(a, b) == compose(b, a)


@pytest.mark.parametrize("a,b,c", list(itertools.product(NAMES, NAMES, NAMES)))
def test_associative(a, b, c):
    assert compose(compose(a, b), c) == compose(a, compose(b, c))


@pytest.mark.parametrize("a", NAMES)
def test_idempotent(a):
    assert compose(a, a) == guarantees(a)


@pytest.mark.parametrize("a,b", list(itertools.product(NAMES, NAMES)))
def test_monotone_adding_a_pack_never_loosens(a, b):
    """§ 1.5's promise, proven: composition is ≥ each operand."""
    c = compose(a, b)
    assert leq(a, c) and leq(b, c)


def test_incomparable_pair_composes_to_both():
    c = compose("four_eyes", "expert_review")
    assert leq("four_eyes", c) and leq("expert_review", c)
    assert name_of(c).startswith("composite(")


def test_block_absorbs():
    for n in NAMES:
        assert compose(n, "block") == FORMS["block"]


def test_compose_all_strictest_wins():
    stack = ["notify", "spot_check", "single_approver"]
    c = compose_all(stack)
    for f in stack:
        assert leq(f, c)
    assert compose_all([]) == frozenset()


# --- legacy mapping ----------------------------------------------------------

def test_traffic_light_mapping():
    assert from_traffic_light("go") == FORMS["auto"]
    assert from_traffic_light("ask") == FORMS["single_approver"]
    assert from_traffic_light("block") == FORMS["block"]
    with pytest.raises(ValueError):
        from_traffic_light("maybe")


def test_unknown_form_refused_with_vocabulary():
    with pytest.raises(ValueError) as e:
        guarantees("triple_eyes")
    assert "known:" in str(e.value)
