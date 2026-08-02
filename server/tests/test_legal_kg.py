# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The legal-system projection into the 5D/ND knowledge graph.

Checks (a) the projection emits the right dimensioned edges, (b) DE pulls EU into
the graph, (c) the result is a real, traversable 5D graph that reasoning.py
composes over, and (d) instrument data adds TEMPORAL supersedes edges.
"""

from __future__ import annotations

from workspaces import legal_kg
from workspaces.dimensions import Dimension
from workspaces import reasoning


def _edge_triples(kg):
    return {(e["subject"], e["predicate"], e["object"]) for e in kg.edges()}


def test_DE_projection_pulls_EU_into_the_graph():
    kg = legal_kg.project("DE")
    assert kg.systems == ("DE", "EU")
    sys_nodes = {n["id"] for n in kg.nodes if n["kind"] == "system"}
    assert sys_nodes == {"legal-system:DE", "legal-system:EU"}
    # the GDPR/AI-Act room (directly-applicable EU regulation) is a node
    assert any(n["id"] == "source:EU:supranational_regulation" for n in kg.nodes)


def test_membership_and_primacy_are_structural_edges():
    kg = legal_kg.project("DE")
    triples = _edge_triples(kg)
    assert ("legal-system:DE", "member-of", "legal-system:EU") in triples
    assert ("legal-system:EU", "outranks", "legal-system:DE") in triples
    member = next(e for e in kg.edges() if e["predicate"] == "member-of")
    assert member["dimension"] == Dimension.STRUCTURAL.value


def test_all_five_dimensions_present_with_instruments():
    # temporal is instrument-level → supply one real supersession
    kg = legal_kg.project("DE", instruments=[
        {"celex": "32016R0679", "label": "GDPR",
         "source_class": "supranational_regulation", "supersedes": "31995L0046"},
    ])
    dims = kg.dimensions_present()
    assert dims == {d.value for d in Dimension}, dims   # all 5


def test_structural_causal_intentional_relational_without_instruments():
    kg = legal_kg.project("DE")
    dims = kg.dimensions_present()
    assert {Dimension.STRUCTURAL.value, Dimension.CAUSAL.value,
            Dimension.INTENTIONAL.value, Dimension.RELATIONAL.value} <= dims
    # temporal NOT fabricated at class level when no instruments given
    assert Dimension.TEMPORAL.value not in dims


def test_directive_transposition_is_a_causal_cross_system_edge():
    kg = legal_kg.project("DE")
    triples = _edge_triples(kg)
    assert ("source:DE:national_statute", "transposes",
            "source:EU:supranational_directive") in triples
    e = next(e for e in kg.edges()
             if e["predicate"] == "transposes")
    assert e["dimension"] == Dimension.CAUSAL.value


def test_treaty_incorporation_and_standard_presumption_edges():
    kg = legal_kg.project("DE")
    preds = {(e["predicate"], e["dimension"]) for e in kg.edges()}
    assert ("incorporates", Dimension.CAUSAL.value) in preds        # Art 59(2)/25 GG
    assert ("presumes-conformity", Dimension.INTENTIONAL.value) in preds


def test_projection_is_traversable_by_reasoning():
    kg = legal_kg.project("DE")
    edges = reasoning.extract_edges(kg.pairs)
    assert edges
    infs = reasoning.compose_paths(edges, start="source:DE:constitution", max_depth=3)
    # constitution → national_statute → national_regulation composes a multi-hop
    # structural inference
    assert any(i.subject == "source:DE:constitution" and i.hops >= 2 for i in infs)


def test_no_overlay_when_disabled():
    kg = legal_kg.project("DE", include_overlay=False)
    assert kg.systems == ("DE",)
    assert all("EU" not in n["id"] for n in kg.nodes)


def test_uk_projection_has_no_eu_and_still_5d_minus_temporal():
    kg = legal_kg.project("UK")
    assert kg.systems == ("UK",)
    dims = kg.dimensions_present()
    assert Dimension.STRUCTURAL.value in dims
    assert "legal-system:EU" not in {n["id"] for n in kg.nodes}


def test_to_dict_reports_edge_count_and_dims():
    d = legal_kg.project("DE").to_dict()
    assert d["edge_count"] > 0 and "structural" in d["dimensions"]
