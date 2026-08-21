# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P6: the editor's text surface — the chain renders to a v0.5 .lg that
round-trips through parse/validate/apply."""
from __future__ import annotations

import os
import pytest

from rvnd import mcp_server as M
from rvnd import loomground_lang as L

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

V05 = ("actor bot7\nhuman alice role legal\n"
       "gate draft risk low grant bot7\ngate decide risk high grant bot7\n"
       "cord bot7 -> draft\ncord bot7 -> decide\ncord draft -> master\ncord decide -> master\n")


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    w = str(tmp_path / "org"); os.makedirs(w)
    M.workspace_workflow(op="patch_apply", params={"folder_context": w, "actor": "f", "netlist": V05})
    return w


def test_netlist_roundtrips(ws):
    r = M.workspace_workflow(op="governance_netlist", params={"folder_context": ws})
    net = r["netlist"]
    rp = L.parse(net)
    assert L.validate(rp)["ok"], L.validate(rp)["errors"]
    gates = {n["id"] for n in rp["nodes"] if n["class"] == "gate"}
    assert {"draft", "decide"} <= gates
    actors = {n["id"] for n in rp["nodes"] if n["class"] == "actor"}
    assert "bot7" in actors
    assert len(M._DECLARED_TOOLS) == 24


def test_editor_validate_applies_via_facade(ws):
    # fail-closed validate of an ill-formed patch
    bad = M.workspace_workflow(op="patch_validate", params={"folder_context": ws, "netlist": "frobnicate x"})
    assert bad["ok"] is False
    # a well-formed edit applies and lands on the chain
    edit = "actor auditor\ngate review risk medium\ncord auditor -> review\ncord review -> master\n"
    ap = M.workspace_workflow(op="patch_apply", params={"folder_context": ws, "actor": "f", "netlist": edit})
    assert ap["ok"]
    g = M.workspace_workflow(op="governance_graph", params={"folder_context": ws})
    assert any(n["kind"] == "use_case" and n["id"] == "uc:review" for n in g["nodes"])
