# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the five-dimensional edge model (workspaces.dimensions)."""

import itertools

import pytest

from workspaces.dimensions import (
    COMPOSITION_TABLE,
    DEFAULT_DIMENSION,
    Dimension,
    classify_predicate,
    compose,
    compose_weights,
)


# ── Federation parity ────────────────────────────────────────────

def test_exactly_five_dimensions_with_canonical_strings():
    """String values must match the Federation cell graph for 1:1 mapping."""
    assert {d.value for d in Dimension} == {
        "structural", "causal", "intentional", "temporal", "relational",
    }
    assert len(Dimension) == 5


def test_dimension_is_a_str_enum():
    # str-Enum so a dimension serialises to its plain string in JSON/triples.
    assert Dimension.CAUSAL == "causal"
    assert Dimension("causal") is Dimension.CAUSAL


# ── Composition algebra ──────────────────────────────────────────

def test_composition_table_is_total():
    """Every ordered pair of dimensions has a composition result."""
    for a, b in itertools.product(Dimension, Dimension):
        assert (a, b) in COMPOSITION_TABLE
    assert len(COMPOSITION_TABLE) == 25


def test_composition_results_are_dimensions():
    for result in COMPOSITION_TABLE.values():
        assert isinstance(result, Dimension)


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (Dimension.STRUCTURAL, Dimension.STRUCTURAL, Dimension.STRUCTURAL),
        (Dimension.STRUCTURAL, Dimension.CAUSAL, Dimension.CAUSAL),
        (Dimension.CAUSAL, Dimension.INTENTIONAL, Dimension.INTENTIONAL),
        (Dimension.INTENTIONAL, Dimension.STRUCTURAL, Dimension.STRUCTURAL),
        (Dimension.TEMPORAL, Dimension.RELATIONAL, Dimension.TEMPORAL),
        (Dimension.RELATIONAL, Dimension.RELATIONAL, Dimension.RELATIONAL),
    ],
)
def test_compose_matches_federation_table(a, b, expected):
    assert compose(a, b) == expected


def test_compose_accepts_strings():
    assert compose("structural", "causal") == Dimension.CAUSAL


def test_compose_weights_multiplicative():
    assert compose_weights(0.8, 0.8) == pytest.approx(0.64)


# ── Predicate classification ─────────────────────────────────────

@pytest.mark.parametrize(
    "predicate,expected",
    [
        ("part-of", Dimension.STRUCTURAL),
        ("depends_on", Dimension.STRUCTURAL),
        ("triggers", Dimension.CAUSAL),
        ("enables", Dimension.CAUSAL),
        ("aims-at", Dimension.INTENTIONAL),
        ("before", Dimension.TEMPORAL),
        ("expires", Dimension.TEMPORAL),
        ("cites", Dimension.RELATIONAL),
        ("similar-to", Dimension.RELATIONAL),
    ],
)
def test_classify_known_predicates(predicate, expected):
    assert classify_predicate(predicate) == expected


def test_classify_normalises_case_and_separators():
    assert classify_predicate("Triggers") == Dimension.CAUSAL
    assert classify_predicate("part of") == Dimension.STRUCTURAL


def test_classify_unknown_defaults_to_relational():
    assert classify_predicate("frobnicates") == DEFAULT_DIMENSION
    assert classify_predicate("") == DEFAULT_DIMENSION
    assert DEFAULT_DIMENSION == Dimension.RELATIONAL
