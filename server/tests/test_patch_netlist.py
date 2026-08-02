# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""patch_netlist — the textual surface of a governance patch (placeholder-named).

Claims under test:
  N1  parse: a well-formed netlist yields the declared parties/use-cases/wires
  N2  validate classifies cords: agent->use-case = authority, use-case->master
      = egress; fail-closed on bad shapes (human as authority source, unknown
      node, bad risk)
  N3  ROUND-TRIP: netlist -> apply -> governance_graph reproduces the declared
      topology (agents, humans, use-cases, authority cords) exactly
  N4  apply is fail-closed: an invalid patch writes NOTHING
  N5  to_netlist -> parse_netlist is stable (text writer round-trips)
  N6  facade ops patch_validate/patch_apply reachable; surface stays 23 tools
"""
from __future__ import annotations

import os
import pytest

from workspaces import patch_netlist as pn
from workspaces.governance_graph import governance_graph

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

NETLIST = """
# a small patch
agent    bot7   grade L2
human    alice  competence legal
use-case uc-draft  risk low   issue liability_cap   allow bot7
use-case uc-decide risk high  issue automated_decision

wire bot7 -> uc-decide        # authority via wire (not via allow)
wire uc-draft  -> master      # egress
wire uc-decide -> master
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"; ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def test_parse(env):                                            # N1
    patch = pn.parse_netlist(NETLIST)
    assert {p["party_id"] for p in patch["parties"]} == {"bot7", "alice"}
    assert {u["use_case_id"] for u in patch["use_cases"]} == {"uc-draft", "uc-decide"}
    assert len(patch["wires"]) == 3


def test_validate_classifies_and_rejects():                    # N2
    patch = pn.parse_netlist(NETLIST)
    v = pn.validate_patch(patch)
    assert v["ok"], v["errors"]
    kinds = sorted(w["kind"] for w in v["wires"])
    assert kinds == ["authority", "egress", "egress"]

    bad = pn.parse_netlist(
        "agent a1\nhuman h1\nuse-case uc risk low\n"
        "wire h1 -> uc\n"          # human as authority source -> invalid
        "wire a1 -> nope\n")       # unknown target -> invalid
    vb = pn.validate_patch(bad)
    assert not vb["ok"]
    assert any("human" in e for e in vb["errors"])
    assert any("nope" in e for e in vb["errors"])

    badrisk = pn.parse_netlist("use-case uc risk spicy")
    assert not pn.validate_patch(badrisk)["ok"]


def test_roundtrip_apply_to_graph(env):                        # N3
    ws, lr = env["ws"], env["lr"]
    patch = pn.parse_netlist(NETLIST)
    res = pn.apply_patch(ws, patch, actor="alex", log_root=lr)
    assert res["ok"], res.get("errors")

    g = governance_graph(ws, log_root=lr)
    agents = {n["id"] for n in g["nodes"] if n["kind"] == "agent"}
    humans = {n["id"] for n in g["nodes"] if n["kind"] == "human"}
    ucs = {n["id"] for n in g["nodes"] if n["kind"] == "use_case"}
    assert agents == {"party:bot7"}
    assert humans == {"party:alice"}
    assert ucs == {"uc:uc-draft", "uc:uc-decide"}

    auth = {(e["from"], e["to"]) for e in g["edges"] if e["kind"] == "authority"}
    # bot7 -> uc-decide via wire, bot7 -> uc-draft via `allow`
    assert auth == {("party:bot7", "uc:uc-decide"), ("party:bot7", "uc:uc-draft")}
    egress = {e["from"] for e in g["edges"] if e["kind"] == "egress"}
    assert egress == {"uc:uc-draft", "uc:uc-decide"}


def test_apply_failclosed(env):                                # N4
    ws, lr = env["ws"], env["lr"]
    bad = pn.parse_netlist("agent a1\nhuman h1\nuse-case uc risk low\nwire h1 -> uc\n")
    res = pn.apply_patch(ws, bad, actor="alex", log_root=lr)
    assert not res["ok"]
    # nothing written: the graph has no parties/use-cases
    g = governance_graph(ws, log_root=lr)
    assert g["summary"]["agents"] == 0 and g["summary"]["use_cases"] == 0


def test_text_writer_roundtrip():                              # N5
    patch = pn.parse_netlist(NETLIST)
    reparsed = pn.parse_netlist(pn.to_netlist(patch))
    assert {p["party_id"] for p in reparsed["parties"]} == \
           {p["party_id"] for p in patch["parties"]}
    assert {u["use_case_id"] for u in reparsed["use_cases"]} == \
           {u["use_case_id"] for u in patch["use_cases"]}


def test_facade_ops_now_speak_v05(env):                        # N6
    # The facade ops moved to the published v0.5 language: they now
    # reject this pre-standard agent/use-case/wire dialect. The v0.5 round-trip
    # is covered in test_loom_facade.py. patch_netlist (this module) stays the
    # internal helper the canvas still uses until the canvas migrates.
    from workspaces import mcp_server as M
    assert len(M._DECLARED_TOOLS) == 24
    ops = {o["op"] for o in M.workspace_workflow(op="help")["ops"]}
    assert {"patch_validate", "patch_apply"} <= ops
    r = M.workspace_workflow(op="patch_apply", params={
        "folder_context": env["ws"], "actor": "alex", "netlist": NETLIST})
    assert not r["ok"]  # old dialect no longer accepted by the facade
