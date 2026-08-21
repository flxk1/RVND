# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Trace enrichment for the problem-solution graph: two upgrades to the
projection so the graph is more detailed and method-aware.

A. METHOD ON STEPS — every schema step carries the Rule-ND method that
   produced it (FRMA / IRAC / Gutachten / generic-Toulmin), read from the
   case profile. The viewer can then SEE which method governed each step.
B. PROBLEM SETS — a gate question decomposed into sub-problems projects as
   a parent problem node fanning out (`decomposes_to`) to one sub-problem
   node per sub-case, each annotated with its rooms/gaps/resolution. More
   nodes, the set structure explicit.

Invariants (written BEFORE the logic):
  M1  each schema-step node carries `method` (the profile label) and the
      profile slug in facets
  M2  an unknown/empty profile degrades to method "generic", never crashes
  M3  the method is on the node, deterministic and stable
  P1  a problem set projects parent + one sub-problem node per sub-case,
      joined by `decomposes_to` edges (count == sub-cases)
  P2  a sub-problem with an open gap projects kind 'gap-bearing'; a fully
      receipted one projects kind 'subproblem'
  P3  the set validates (no dangling edges, known dims, 100% basis)
  P4  pure projection — same inputs, same graph
  P5  a REAL gate over two real sub-walks projects and validates
"""
from __future__ import annotations

import os


from rvnd.kg_export import (
    case_set_to_cytoscape, case_trace_to_cytoscape, validate_graph,
)

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def _payload(profile="frma", rooms=("Art. 5(1)",)):
    return {
        "case": {"problem": {"text": "Is the processing lawful?"},
                 "grounds": [{"pinpoint": r} for r in rooms],
                 "chain": [{"step": "Fact", "text": "data collected"},
                           {"step": "Rule", "text": "Art. 5(1)(a) lawfulness"},
                           {"step": "Meaning", "text": "what counts as lawful"}],
                 "gaps": [], "resolution": {"type": "residual"},
                 "facts": [], "profile": profile, "actions": [],
                 "contract": {}, "waivers": []},
        "inputs": {"question": "Is the processing lawful?",
                   "rooms": list(rooms), "profile": profile,
                   "readings": [{"label": "lawful"}, {"label": "needs basis"}]},
    }


# ── A: method on steps ────────────────────────────────────────────────────────

def test_steps_carry_their_method(self_profile="frma"):           # M1
    g = case_trace_to_cytoscape(_payload(profile="frma"))
    steps = [n for n in g["nodes"] if n["data"]["kind"] == "schema_step"]
    assert steps and all("Fact" in s["data"]["facets"].get("method", "") or
                         s["data"]["facets"].get("profile") == "frma"
                         for s in steps)
    assert all(s["data"]["facets"]["profile"] == "frma" for s in steps)
    assert all("Action" in s["data"]["facets"]["method"] for s in steps)  # FRMA label


def test_unknown_profile_degrades_to_generic():                   # M2
    g = case_trace_to_cytoscape(_payload(profile="not-a-profile"))
    steps = [n for n in g["nodes"] if n["data"]["kind"] == "schema_step"]
    assert all(s["data"]["facets"]["method"] == "generic" for s in steps)


def test_method_tag_is_stable():                                  # M3
    a = case_trace_to_cytoscape(_payload(profile="legal-irac"))
    b = case_trace_to_cytoscape(_payload(profile="legal-irac"))
    assert a == b
    s = [n for n in a["nodes"] if n["data"]["kind"] == "schema_step"][0]
    assert s["data"]["facets"]["method"] == "IRAC"


# ── B: problem sets ───────────────────────────────────────────────────────────

def _set_payload():
    parent = {"case": {"problem": {"text": "Can we ship the product?"},
                       "resolution": {"type": "residual"}, "profile": "generic"},
              "inputs": {"question": "Can we ship the product?",
                         "readings": [{"label": "ship"}, {"label": "hold"}]}}
    subs = [
        {"case": {"problem": {"text": "GDPR lawful?"},
                  "grounds": [{"pinpoint": "Art. 6(1)"}], "gaps": [],
                  "resolution": {"type": "determinate"}, "profile": "legal-de"},
         "inputs": {"rooms": ["Art. 6(1)"], "profile": "legal-de"}},
        {"case": {"problem": {"text": "AI Act risk tier?"},
                  "grounds": [], "gaps": ["Annex III"],
                  "resolution": {"type": "residual"}, "profile": "legal-de"},
         "inputs": {"rooms": ["Annex III"], "profile": "legal-de"}},
    ]
    return parent, subs


def test_problem_set_fans_out_to_subproblems():                   # P1
    parent, subs = _set_payload()
    g = case_set_to_cytoscape(parent, subs)
    deco = [e for e in g["edges"] if e["data"]["rel_label"] == "decomposes to"]
    assert len(deco) == 2
    subnodes = [n for n in g["nodes"]
                if n["data"]["kind"] in ("subproblem", "gap-bearing")]
    assert len(subnodes) == 2


def test_open_subproblem_is_marked():                             # P2
    parent, subs = _set_payload()
    g = case_set_to_cytoscape(parent, subs)
    kinds = {n["data"]["label"]: n["data"]["kind"] for n in g["nodes"]}
    assert kinds["GDPR lawful?"] == "subproblem"
    assert kinds["AI Act risk tier?"] == "gap-bearing"


def test_problem_set_validates():                                 # P3
    parent, subs = _set_payload()
    v = validate_graph(case_set_to_cytoscape(parent, subs))
    assert v["ok"], v["findings"]
    assert v["edges_with_basis_pct"] == 100.0


def test_problem_set_is_pure():                                   # P4
    parent, subs = _set_payload()
    assert case_set_to_cytoscape(parent, subs) == case_set_to_cytoscape(parent, subs)


def test_real_gate_over_two_subwalks(tmp_path):                   # P5
    from rvnd import legal_corpus, problem_kg, reasoning_walker as rw
    from rvnd.rule_registry import RuleRegistry

    legal_corpus.seed_registry(tmp_path)
    reg = RuleRegistry(tmp_path, user="alex")
    reg.place_legal_text(
        "REGULATION (EU) 2016/679\nArticle 6\n1. Processing is lawful only if "
        "a legal basis applies.", "gdpr", source_document="gdpr.txt")

    sub1 = rw.walk("Is there a legal basis under Art. 6?", registry=reg)["case"]
    sub2 = rw.walk("Is there a legal basis for the second flow?",
                   registry=reg)["case"]
    gate = problem_kg.gate_case("Can we ship?", [sub1, sub2], registry=reg)

    parent = {"case": gate["case"].to_dict(), "inputs": gate.get("inputs", {})}
    subs = [{"case": sub1.to_dict(), "inputs": {}},
            {"case": sub2.to_dict(), "inputs": {}}]
    g = case_set_to_cytoscape(parent, subs)
    v = validate_graph(g)
    assert v["ok"], v["findings"]
    deco = [e for e in g["edges"] if e["data"]["rel_label"] == "decomposes to"]
    assert len(deco) == 2
