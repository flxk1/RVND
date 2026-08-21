# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P7b: the transport/clock primitive — every run has one external trigger
(nothing self-starts). Read-only audit over the chain."""
from __future__ import annotations

import os
import pytest

from rvnd import mcp_server as M

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    w = str(tmp_path / "org"); os.makedirs(w)
    return w


def test_empty_holds_vacuously(ws):
    r = M.workspace_workflow(op="transport_audit", params={"folder_context": ws})
    assert r["total"] == 0 and r["missing_actor"] == 0 and r["holds"] is True
    assert "audit, not enforcement" in r["basis"]


def test_run_is_externally_triggered(ws):
    M.workspace_policy(op="party_register", params={"folder_context": ws, "party_id": "bot7", "kind": "agent", "grade": "L2", "actor": "f"})
    M.workspace_workflow(op="use_case_register", params={"folder_context": ws, "use_case_id": "uc-draft", "name": "d", "fingerprint": {"issue_type": "liability_cap"}, "risk": "low", "allowed_agents": ["bot7"], "actor": "f"})
    M.workspace_workflow(op="operate", params={"folder_context": ws, "use_case_id": "uc-draft", "agent_id": "bot7", "issues": [{"issue_id": "i1", "issue_type": "liability_cap", "completeness": "high"}], "now_epoch": 1000})
    r = M.workspace_workflow(op="transport_audit", params={"folder_context": ws})
    assert r["total"] >= 1
    assert r["missing_actor"] == 0 and r["holds"] is True
    assert all(x["actor_present"] for x in r["runs"])
    assert any(x["actor"] == "bot7" for x in r["runs"])
    assert len(M._DECLARED_TOOLS) == 24
