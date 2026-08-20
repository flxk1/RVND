# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the 5D reasoning engine (workspaces.reasoning)."""

from __future__ import annotations

import pytest

from workspaces.dimensions import Dimension
from workspaces.reasoning import compose_paths, extract_edges


def _pair(pid, edges, confidence=1.0):
    return {
        "id": pid,
        "problem": {"id": f"{pid}-p", "scope": "s", "type": "rule", "summary": pid},
        "solution": {"id": pid, "problem_id": f"{pid}-p", "body": "b",
                     "authority_tier": 1, "confidence": confidence,
                     "body_format": "prose"},
        "edges": edges,
    }


def _edge(s, p, o, dim):
    return {"subject": s, "predicate": p, "object": o, "dimension": dim.value}


# ── extract_edges ────────────────────────────────────────────────

def test_extract_edges_reads_well_formed_and_inherits_confidence():
    pairs = [
        _pair("a", [_edge("X", "causes", "Y", Dimension.CAUSAL)], confidence=0.8),
        _pair("b", [_edge("Y", "causes", "Z", Dimension.CAUSAL)], confidence=0.5),
    ]
    edges = extract_edges(pairs)
    assert len(edges) == 2
    assert edges[0].weight == pytest.approx(0.8)
    assert edges[0].source_pair == "a"
    assert edges[0].dimension == Dimension.CAUSAL


def test_extract_edges_skips_garbage():
    pairs = [
        {"id": "x", "edges": "not-a-list"},
        {"id": "y", "edges": [123, None, {"subject": "A"}, {"subject": "A", "object": "B", "dimension": "bogus"}]},
        "not-a-pair",
        _pair("z", [_edge("A", "p", "B", Dimension.STRUCTURAL)]),
    ]
    edges = extract_edges(pairs)
    assert [e.subject for e in edges] == ["A"]
    assert edges[0].dimension == Dimension.STRUCTURAL


# ── compose_paths ────────────────────────────────────────────────

def test_two_hop_causal_chain_composes_to_causal():
    pairs = [
        _pair("a", [_edge("X", "causes", "Y", Dimension.CAUSAL)], confidence=0.8),
        _pair("b", [_edge("Y", "causes", "Z", Dimension.CAUSAL)], confidence=0.8),
    ]
    inf = compose_paths(extract_edges(pairs))
    assert len(inf) == 1
    i = inf[0]
    assert (i.subject, i.object) == ("X", "Z")
    assert i.dimension == Dimension.CAUSAL
    assert i.confidence == pytest.approx(0.64)   # 0.8 * 0.8
    assert i.hops == 2
    assert i.dimension_chain == ["causal", "causal"]
    # provenance: the two source edges, in order, with their pairs
    assert [h["source_pair"] for h in i.path] == ["a", "b"]


def test_mixed_dimension_composes_per_algebra():
    # structural then causal -> causal (compose(S, C) == C)
    pairs = [
        _pair("a", [_edge("X", "part-of", "Y", Dimension.STRUCTURAL)]),
        _pair("b", [_edge("Y", "causes", "Z", Dimension.CAUSAL)]),
    ]
    inf = compose_paths(extract_edges(pairs))
    assert inf[0].dimension == Dimension.CAUSAL


def test_three_hop_uses_left_fold():
    # causal, intentional, structural -> left-fold:
    # compose(compose(causal,intentional)=intentional, structural)=structural
    pairs = [
        _pair("a", [_edge("A", "p", "B", Dimension.CAUSAL)]),
        _pair("b", [_edge("B", "p", "C", Dimension.INTENTIONAL)]),
        _pair("c", [_edge("C", "p", "D", Dimension.STRUCTURAL)]),
    ]
    inf = compose_paths(extract_edges(pairs), start="A", max_depth=3)
    a_to_d = [i for i in inf if i.object == "D"]
    assert a_to_d and a_to_d[0].dimension == Dimension.STRUCTURAL
    assert a_to_d[0].hops == 3


def test_cycles_do_not_loop_forever():
    pairs = [
        _pair("a", [_edge("X", "p", "Y", Dimension.RELATIONAL)]),
        _pair("b", [_edge("Y", "p", "X", Dimension.RELATIONAL)]),
    ]
    inf = compose_paths(extract_edges(pairs), max_depth=5)
    # bounded, no infinite recursion
    assert all(i.hops <= 5 for i in inf)


def test_min_confidence_prunes():
    pairs = [
        _pair("a", [_edge("X", "p", "Y", Dimension.CAUSAL)], confidence=0.5),
        _pair("b", [_edge("Y", "p", "Z", Dimension.CAUSAL)], confidence=0.5),
    ]
    assert compose_paths(extract_edges(pairs), min_confidence=0.5) == []   # 0.25 < 0.5
    assert len(compose_paths(extract_edges(pairs), min_confidence=0.2)) == 1


def test_single_edge_is_not_an_inference():
    pairs = [_pair("a", [_edge("X", "p", "Y", Dimension.CAUSAL)])]
    assert compose_paths(extract_edges(pairs)) == []


def test_results_sorted_by_confidence():
    pairs = [
        _pair("a", [_edge("X", "p", "Y", Dimension.CAUSAL)], confidence=0.9),
        _pair("b", [_edge("Y", "p", "Z", Dimension.CAUSAL)], confidence=0.9),
        _pair("c", [_edge("X", "p", "M", Dimension.CAUSAL)], confidence=0.3),
        _pair("d", [_edge("M", "p", "N", Dimension.CAUSAL)], confidence=0.3),
    ]
    inf = compose_paths(extract_edges(pairs))
    confs = [i.confidence for i in inf]
    assert confs == sorted(confs, reverse=True)
