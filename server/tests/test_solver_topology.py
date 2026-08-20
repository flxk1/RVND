# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Validated solver topology: the primitive that makes a DERIVED
solver structure clean and governed, rather than an arbitrary per-input flow.

The thesis: a fixed flow (n8n / Power Automate) walks the same nodes every
time; a Workspaces solver derives its topology from the problem's fingerprints, so
it is situational. The risk of derivation is arbitrary, non-reproducible
structure. This module makes the topology a TYPED DAG whose validity is
checked, so flexibility stays auditable. Governance rides on node KIND, not
flow position: a judgment node must keep a human in the loop wherever it
appears.

Invariants first:
  V1  a cycle is rejected — a solver topology is a DAG, never a loop
  V2  an orphan (node unreachable from a root problem) is flagged
  V3  a dangling dependency (edge to/from an unknown node) is rejected
  V4  GOVERNANCE: a judgment node graded 'auto' is a violation — important
      nodes stay in the human loop by kind, not by position
  V5  execution order is a deterministic topological sort; a node always
      follows everything that feeds it
  V6  same fingerprints in → same topology + same order (stability: derived,
      not arbitrary)
  V7  a valid topology projects to the existing graph viz with its
      dependency edges visible (feeds / conditions / requires)
"""
from __future__ import annotations


from workspaces.solver_topology import (
    Dep, SolverNode, build_topology, topo_order,
)


def _nodes():
    return [
        SolverNode("root", fingerprint={"issue_type": "ship_gate"},
                   solver="gate", kind="gate", grade="ask"),
        SolverNode("gdpr", fingerprint={"issue_type": "data_processing"},
                   solver="skill:dpa-nd", kind="solve", grade="auto"),
        SolverNode("airisk", fingerprint={"issue_type": "ai_risk_tier"},
                   solver="skill:aiact-nd", kind="solve", grade="auto"),
        SolverNode("balance", fingerprint={"issue_type": "proportionality"},
                   solver="skill:balancer", kind="judgment", grade="ask"),
    ]


def _deps():
    # airisk feeds balance; gdpr + balance feed root.
    return [Dep("airisk", "balance", "feeds"),
            Dep("gdpr", "root", "feeds"),
            Dep("balance", "root", "feeds")]


def test_cycle_is_rejected():                                     # V1
    nodes = _nodes()
    deps = _deps() + [Dep("root", "airisk", "feeds")]   # closes a loop
    rep = build_topology(nodes, deps)
    assert not rep["ok"]
    assert any(f["kind"] == "cycle" for f in rep["findings"])


def test_orphan_is_flagged():                                     # V2
    nodes = _nodes() + [SolverNode("stray", fingerprint={}, solver="x",
                                   kind="solve", grade="auto")]
    rep = build_topology(nodes, _deps(), roots=["root"])
    orphans = {f["id"] for f in rep["findings"] if f["kind"] == "orphan"}
    # only the truly disconnected node is an orphan — connected nodes that
    # feed the root (directly or transitively) must NOT be flagged
    assert orphans == {"stray"}, orphans
    for connected in ("gdpr", "airisk", "balance", "root"):
        assert connected not in orphans


def test_dangling_dependency_is_rejected():                       # V3
    rep = build_topology(_nodes(), _deps() + [Dep("ghost", "root", "feeds")])
    assert not rep["ok"]
    assert any(f["kind"] == "dangling-dep" for f in rep["findings"])


def test_auto_judgment_node_is_a_governance_violation():          # V4
    nodes = _nodes()
    nodes[3] = SolverNode("balance", fingerprint={"issue_type": "proportionality"},
                          solver="skill:balancer", kind="judgment",
                          grade="auto")               # judgment must not be auto
    rep = build_topology(nodes, _deps(), roots=["root"])
    assert not rep["ok"]
    assert any(f["kind"] == "ungoverned-judgment" and f["id"] == "balance"
               for f in rep["findings"])


def test_execution_order_respects_dependencies():                # V5
    order = topo_order(_nodes(), _deps())
    assert order.index("airisk") < order.index("balance")
    assert order.index("balance") < order.index("root")
    assert order.index("gdpr") < order.index("root")


def test_topology_is_stable_for_same_fingerprints():             # V6
    a = build_topology(_nodes(), _deps(), roots=["root"])
    b = build_topology(_nodes(), _deps(), roots=["root"])
    assert a == b
    assert topo_order(_nodes(), _deps()) == topo_order(_nodes(), _deps())


def test_valid_topology_projects_to_graph():                     # V7
    from workspaces.kg_export import validate_graph
    rep = build_topology(_nodes(), _deps(), roots=["root"])
    assert rep["ok"], rep["findings"]
    g = rep["graph"]
    v = validate_graph(g)
    assert v["ok"], v["findings"]
    labels = {e["data"]["rel_label"] for e in g["edges"]}
    assert "feeds" in labels
    # the dependency structure is visible: balance has an inbound feed
    assert any(e["data"]["target"] == "balance" for e in g["edges"])
