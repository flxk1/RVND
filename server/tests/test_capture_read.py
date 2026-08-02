# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P7: the capture ledger is readable — what left vs stayed (workspace_capture read)."""
from __future__ import annotations

import os
import pytest

from workspaces import mcp_server as M

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    w = str(tmp_path / "org"); os.makedirs(w)
    return w


def test_capture_write_then_read(ws):
    # empty ledger reads clean
    r0 = M.workspace_capture(op="read", params={"folder_context": ws})
    assert r0["count"] == 0 and r0["captures"] == [] and r0["spend_cents"] == 0.0
    # record an exchange (agentic audit floor), then read it back
    M.workspace_capture(op="llm", params={"folder_context": ws, "model": "test-model",
                                     "prompt_context": "what is 2+2?", "response": "4",
                                     "mode": "agentic", "oversight": "autonomous"})
    r = M.workspace_capture(op="read", params={"folder_context": ws})
    assert r["count"] >= 1
    assert any(c["scope"] == "llm" and "2+2" in c["summary"] for c in r["captures"])


def test_read_is_an_op_on_the_capture_tool():
    ops = {o["op"] if isinstance(o, dict) else o for o in M.workspace_capture(op="help")["ops"]}
    assert "read" in ops and {"llm", "web"} <= ops
    assert len(M._DECLARED_TOOLS) == 24
