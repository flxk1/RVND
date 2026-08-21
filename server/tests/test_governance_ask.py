# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Natural-language ask over the governance map.

The tests cover facet parsing, synonym handling, off-contract input handling,
resolved payloads, determinism, and the serve path.
"""
from __future__ import annotations

import os

from rvnd import governance_ask as ASK
from rvnd import governance_map as GM
from rvnd import duty_identification as DI

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

AI_ACT = {
    "Art. 5":  "AI systems that deploy subliminal techniques to distort behaviour shall be prohibited.",
    "Art. 9":  "A risk management system shall be established for high-risk AI systems.",
    "Art. 16": "Providers of high-risk AI systems shall ensure conformity assessment.",
    "Art. 26": "Deployers of high-risk AI systems shall ensure human oversight by natural persons.",
    "Art. 50": "Providers shall ensure that persons are informed they are interacting with an AI system.",
}


def _map():
    duties = [DI.identify_duties(t, source=a)[0] for a, t in AI_ACT.items()]
    by = {d.source: d for d in duties}
    DI.ratify(by["Art. 5"], operator="F", rationale="prohibited practice")
    return GM.project(duties, instrument="AI Act")


def test_question_maps_to_facet_filters():
    fv = _map().facet_values()
    v = ASK.parse("show provider high-risk rules grouped by demand", facet_values=fv)
    assert v.group_by == "demand"
    assert "provider" in v.filters.get("role", [])
    assert "high-risk" in v.filters.get("risk", [])


def test_status_and_demand_synonyms():
    fv = _map().facet_values()
    human = ASK.parse("which cards need a human?", facet_values=fv)
    assert "interpreter — needs a read" in human.filters.get("status", [])
    ban = ASK.parse("show the prohibited practices", facet_values=fv)
    assert "guard" in ban.filters.get("demand", [])


def test_never_off_contract():
    v = ASK.parse("give me the purple bananas by colour", facet_values=_map().facet_values())
    assert set(v.filters).issubset(set(GM.FACETS))       # no invented facet
    assert v.group_by in GM.FACETS                        # 'colour' is not a facet → default room
    assert v.group_by == "room"


def test_ask_resolves_and_echoes():
    gm = _map()
    r = ASK.ask(gm, "which rules need a human?")
    assert r["version"] == GM.SCHEMA_VERSION
    assert r["question"] == "which rules need a human?"   # the run query is visible
    # the answer is the interpreter-queued rules (Art 9/11-style), resolved deterministically
    shown = [rr["pinpoint"] for g in r["groups"] for rr in g["rules"]]
    assert "Art. 9" in shown or "Art. 5" in shown


def test_deterministic_and_via_serve():
    a = ASK.parse("deployer rules by risk", facet_values=_map().facet_values())
    b = ASK.parse("deployer rules by risk", facet_values=_map().facet_values())
    assert a == b and a.group_by == "risk" and "deployer" in a.filters.get("role", [])
    # the op path: serve(question=…) runs the same ask
    out = GM.serve(question="deployer rules by risk", policy_text="\n".join(AI_ACT.values()),
                   instrument="AI Act")
    assert out["grouped_by"] == "risk" and out["question"] == "deployer rules by risk"
