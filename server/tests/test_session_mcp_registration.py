# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""S12 wiring — workspace_session is a registered, dispatchable MCP tool."""
from __future__ import annotations

from pathlib import Path

from workspaces import mcp_server, parties


def test_registered_in_tool_set():
    assert "workspace_session" in mcp_server._DECLARED_TOOLS
    assert hasattr(mcp_server, "workspace_session")


def test_help_op_lists_the_ops():
    out = mcp_server.workspace_session("help")
    ops = {o["op"] for o in out["ops"]}
    assert {"save", "verify", "restore", "export", "import", "forensic",
            "draft_save", "draft_load", "draft_discard",
            "template_list", "template_new"} <= ops


def test_end_to_end_through_the_facade(tmp_path):
    folder = tmp_path / "ws"
    folder.mkdir()
    lr = str(tmp_path / "log")
    parties.register_party(str(folder), "bot-1", "agent", log_root=lr)
    path = str(tmp_path / "e.rvnd")
    saved = mcp_server.workspace_session("save", {
        "workspaces": [{"folder_context": str(folder), "id": "ws", "log_root": lr}],
        "rail": {"order": ["ws"], "focused": "ws"}, "path": path, "name": "e"})
    assert saved["ok"] and Path(path).exists()
    assert mcp_server.workspace_session("verify", {"path": path})["ok"]
