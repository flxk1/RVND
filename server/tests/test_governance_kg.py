# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Universal, zoomable governance KG and reasoning paths.

The tests cover node/edge vocabulary, zoom levels, dimension filters, demand
projection, reasoning paths, and MCP operation reachability.
"""
from __future__ import annotations

import os

from rvnd import governance_kg as KG
from rvnd import governance_map as GM
from rvnd import duty_identification as DI

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

AI_ACT = {
    "Art. 16": "Providers of high-risk AI systems shall ensure conformity assessment.",
    "Art. 26": "Deployers of high-risk AI systems shall ensure human oversight by natural persons.",
    "Art. 50": "Providers shall inform persons they are interacting with an AI system.",
}


def _rules():
    return GM.project([DI.identify_duties(t, source=a)[0] for a, t in AI_ACT.items()],
                      instrument="AI Act").rules


def test_universal_kinds_and_dimensions():
    g = KG.project(_rules(), level="detail")
    kinds = set(g["kinds"])
    assert {"rule", "role", "instrument"} <= kinds        # universal kinds, not "Article"
    dims = {e["dimension"] for e in g["edges"]}
    assert dims <= {"structural", "causal", "intentional", "temporal", "relational"}
    assert "relational" in dims and "structural" in dims  # role=relational, instrument=structural


def test_zoom_out_reduces_nodes():
    rules = _rules()
    detail = KG.project(rules, level="detail")
    cluster = KG.project(rules, level="cluster")
    overview = KG.project(rules, level="overview")
    assert len(overview["nodes"]) <= len(cluster["nodes"]) < len(detail["nodes"])
    assert overview["level"] == "overview"
    # overview is instruments × roles only
    assert {n["kind"] for n in overview["nodes"]} <= {"instrument", "role"}


def test_dimension_filter():
    g = KG.project(_rules(), level="detail", dimensions=["relational"])
    assert g["edges"] and all(e["dimension"] == "relational" for e in g["edges"])
    assert g["dimensions"] == ["relational"]


def test_demand_reifies_to_node_with_artifact():
    g = KG.project(_rules(), level="detail", demand_as="node")
    kinds = {n["kind"] for n in g["nodes"]}
    assert "obligation" in kinds and "artifact" in kinds          # demand node + the artifact it needs
    # the chain: rule →demands(intentional)→ obligation →satisfied-by(causal)→ artifact
    sat = [e for e in g["edges"] if e["label"] == "satisfied-by"]
    assert sat and sat[0]["dimension"] == "causal" and sat[0]["target"].startswith("artifact:")


def test_demand_collapses_to_edge_when_zoomed_out():
    g = KG.project(_rules(), level="detail", demand_as="edge")
    assert g["demand_as"] == "edge"
    # no obligation node — the demand is the LABEL on a rule→artifact edge
    assert not any(n["kind"] == "obligation" for n in g["nodes"])
    assert any(n["kind"] == "artifact" for n in g["nodes"])
    to_artifact = [e for e in g["edges"] if e["target"].startswith("artifact:")]
    assert to_artifact and to_artifact[0]["dimension"] == "intentional"   # demand type = edge label


def test_reasoning_path_has_provenance():
    rules = _rules()
    rid = GM._rule_id("AI Act", "Art. 16")
    p = KG.path(rules, rid, "role:provider")               # rule → its bearer role
    assert p["hops"] >= 1 and p["edges"]                   # the ordered edges = provenance
    assert p["overall_dimension"] in ("structural", "causal", "intentional", "temporal", "relational")
    # a rule to itself is zero hops; an unreachable node has no path
    assert KG.path(rules, rid, "role:nonexistent")["hops"] == 0


# ── the op is mounted and reachable via the MCP dispatch ──
_PROV = [{"pinpoint": a, "text": t} for a, t in AI_ACT.items()]


def test_kg_op_projects_over_the_same_rules_as_the_map():
    from rvnd import mcp_server as M
    g = M.workspace_workflow("governance_kg", {
        "folder_context": "", "provisions": _PROV, "instrument": "AI Act", "level": "detail"})
    assert g["version"] == KG.SCHEMA_VERSION and g["level"] == "detail"
    assert g["nodes"] and g["edges"]
    # identical to projecting the map's own rules directly → one rule source, no parallel builder
    direct = KG.project(_rules(), level="detail")
    assert {n["id"] for n in g["nodes"]} == {n["id"] for n in direct["nodes"]}


def test_kg_op_returns_a_reasoning_path():
    from rvnd import mcp_server as M
    rid = GM._rule_id("AI Act", "Art. 16")
    p = M.workspace_workflow("governance_kg", {
        "folder_context": "", "provisions": _PROV, "instrument": "AI Act",
        "from": rid, "to": "role:provider"})
    assert p["version"] == KG.SCHEMA_VERSION and p["from"] == rid and p["to"] == "role:provider"
    assert p["hops"] >= 1 and p["edges"]


def test_kg_op_is_discoverable_in_the_catalog():
    from rvnd import mcp_server as M
    ops = {row["op"] for row in M.workspace_workflow("ops", {"folder_context": ""})["ops"]}
    assert "governance_kg" in ops
