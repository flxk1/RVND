# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Connectors — the boundary ports of a Loomground patch.

C1  register/list round-trips; role validated fail-closed
C2  governance_graph emits a connector node per registered connector
C3  ingress connector -> use-case edge; oversight use-case -> connector (notify);
    egress master -> connector (deliver)
C4  reachable through workspace_workflow; surface stays 23 tools
"""
from __future__ import annotations

import os
import pytest

from workspaces import parties as pt
from workspaces.connectors import register_connector, list_connectors
from workspaces.governance_graph import governance_graph
from workspaces.use_case import register_use_case

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    ws = tmp_path / "org"; ws.mkdir()
    lr = str(tmp_path / "logs")
    pt.register_party(str(ws), "bot7", "agent", name="bot7", actor="alex", log_root=lr)
    register_use_case(str(ws), use_case_id="uc-draft", name="uc-draft",
                      fingerprint={"issue_type": "liability_cap"}, risk="low",
                      allowed_agents=["bot7"], actor="alex", log_root=lr)
    register_use_case(str(ws), use_case_id="uc-decide", name="uc-decide",
                      fingerprint={"issue_type": "automated_decision"}, risk="high",
                      allowed_agents=["bot7"], actor="alex", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def test_register_list_and_validate(env):
    register_connector(env["ws"], connector_id="inbox", role="ingress",
                       channel="email", use_cases=["uc-draft"], actor="alex", log_root=env["lr"])
    rows = list_connectors(env["ws"], log_root=env["lr"])
    assert [c["connector_id"] for c in rows] == ["inbox"]
    with pytest.raises(ValueError):
        register_connector(env["ws"], connector_id="x", role="bogus", channel="email",
                           actor="alex", log_root=env["lr"])


def test_graph_emits_connector_edges(env):
    ws, lr = env["ws"], env["lr"]
    register_connector(ws, connector_id="inbox", role="ingress", channel="email",
                       use_cases=["uc-draft"], actor="alex", log_root=lr)
    register_connector(ws, connector_id="reviewer", role="oversight", channel="ticket",
                       use_cases=["uc-decide"], actor="alex", log_root=lr)
    register_connector(ws, connector_id="reply", role="egress", channel="email",
                       actor="alex", log_root=lr)
    g = governance_graph(ws, log_root=lr)
    conns = [n for n in g["nodes"] if n["kind"] == "connector"]
    assert len(conns) == 3                                                   # C2
    kinds = {(e["kind"], e["from"], e["to"]) for e in g["edges"]}
    assert ("ingress", "conn:inbox", "uc:uc-draft") in kinds                 # C3
    assert ("notify", "uc:uc-decide", "conn:reviewer") in kinds             # C3
    assert ("deliver", "master", "conn:reply") in kinds                      # C3
    assert g["summary"]["connectors"] == 3


def test_facade_and_surface(env):
    from workspaces import mcp_server as M
    assert len(M._DECLARED_TOOLS) == 24                                      # C4
    M.workspace_workflow(op="connector_register", params={
        "folder_context": env["ws"], "connector_id": "inbox", "role": "ingress",
        "channel": "email", "use_cases": ["uc-draft"], "actor": "alex"})
    r = M.workspace_workflow(op="connector_list", params={"folder_context": env["ws"]})
    assert any(c["connector_id"] == "inbox" for c in r["connectors"])
