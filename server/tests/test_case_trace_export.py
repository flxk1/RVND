# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Case-trace projection for the problem-solution graph: one walker
result rendered as a Cytoscape trace — the task's actual path through
question, facts, norm rooms (receipted vs gap), schema, readings, and the
human boundary. Pure projection over the walker payload; no new store.

Invariants (written BEFORE the logic):
  T1  the trace validates: no dangling edges, known dimensions, labelled
      nodes, and EVERY edge carries a basis note (100%)
  T2  gaps are visually distinct: required rooms without receipt project as
      kind 'gap', receipted rooms as kind 'norm'
  T3  the walk order is on the edges and strictly increases along phases
  T4  the human boundary is explicit: readings point at a resolution node;
      a residual is labelled as awaiting a human, never as an answer
  T5  the projection is pure — same payload, same graph
  T6  a REAL deterministic walk (no model) projects and validates
"""
from __future__ import annotations

import os


from workspaces.kg_export import case_trace_to_cytoscape, validate_graph

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def _payload():
    """Synthetic walker-shaped payload exercising every trace element."""
    return {
        "case": {
            "problem": {"text": "Must we notify within 72 hours?",
                        "document": "", "pinpoint": ""},
            "grounds": [{"pinpoint": "Art. 33(1)", "text": "notify…"}],
            "chain": [{"step": "Norm", "text": "Art. 33(1) GDPR", "schema": True},
                      {"step": "Tatbestand", "text": "breach known?", "schema": True}],
            "gaps": ["Art. 34(1)"],
            "resolution": {"type": "residual"},
            "coverage": 0.5, "facts": [{"text": "breach detected 2026-06-10",
                                        "source": "incident-log"}],
            "actions": [], "profile": "legal-de", "contract": {}, "waivers": [],
        },
        "inputs": {"question": "Must we notify within 72 hours?",
                   "rooms": ["Art. 33(1)", "Art. 34(1)"],
                   "readings": [{"label": "notify now"},
                                {"label": "await assessment"}]},
        "transcript": [],
    }


def test_trace_validates_with_full_basis():                       # T1
    g = case_trace_to_cytoscape(_payload())
    v = validate_graph(g)
    assert v["ok"], v["findings"]
    assert v["edges_with_basis_pct"] == 100.0
    assert v["nodes"] >= 7 and v["edges"] >= 6


def test_gap_rooms_are_distinct_from_receipted_rooms():           # T2
    g = case_trace_to_cytoscape(_payload())
    kinds = {n["data"]["id"]: n["data"]["kind"] for n in g["nodes"]}
    assert kinds.get("room:Art. 33(1)") == "norm"
    assert kinds.get("room:Art. 34(1)") == "gap"


def test_walk_order_on_edges_strictly_increases():                # T3
    g = case_trace_to_cytoscape(_payload())
    orders = [e["data"]["order"] for e in g["edges"]]
    assert all(isinstance(o, int) for o in orders)
    assert orders == sorted(orders)
    assert len(set(orders)) == len(orders)


def test_human_boundary_explicit_never_an_answer():               # T4
    g = case_trace_to_cytoscape(_payload())
    res = [n for n in g["nodes"] if n["data"]["kind"] == "resolution"]
    assert len(res) == 1
    assert "await" in res[0]["data"]["label"].lower()
    awaiting = [e for e in g["edges"] if e["data"]["target"] == res[0]["data"]["id"]]
    assert len(awaiting) == 2                  # both readings point at it
    assert all("human" in e["data"]["note"].lower() for e in awaiting)


def test_projection_is_pure():                                    # T5
    assert case_trace_to_cytoscape(_payload()) == case_trace_to_cytoscape(_payload())


def test_real_deterministic_walk_projects_and_validates(tmp_path):  # T6
    from workspaces import legal_corpus, reasoning_walker as rw
    from workspaces.rule_registry import RuleRegistry

    legal_corpus.seed_registry(tmp_path)
    reg = RuleRegistry(tmp_path, user="alex")
    reg.place_legal_text(
        "REGULATION (EU) 2016/679 (General Data Protection Regulation)\n"
        "Article 33\n1. The controller shall notify the personal data breach "
        "to the supervisory authority within 72 hours.", "gdpr",
        source_document="gdpr.txt")
    result = rw.walk("When must we notify a data breach?", registry=reg)

    g = case_trace_to_cytoscape({"case": result["case"].to_dict(),
                                 "inputs": result["inputs"],
                                 "transcript": result["transcript"]})
    v = validate_graph(g)
    assert v["ok"], v["findings"]
    assert v["edges_with_basis_pct"] == 100.0
    # deterministic walk: no model → OPEN resolution node present
    res = [n for n in g["nodes"] if n["data"]["kind"] == "resolution"]
    assert len(res) == 1
