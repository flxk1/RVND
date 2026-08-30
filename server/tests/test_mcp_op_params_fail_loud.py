# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fail-LOUD on a top-level-instead-of-nested MCP call.

``workspace_workspace`` / ``workspace_folder`` / ``workspace_policy`` share the
``(op: str, params: dict|None = None)`` facade convention: FastMCP builds each
tool's pydantic model from that signature, so a caller that passes a required
field like ``path`` / ``folder_context`` as a TOP-LEVEL keyword instead of
nested inside ``params`` has it silently dropped as an extra field before the
handler ever runs. Before this fix that produced a bare KeyError or a
silent/unhelpful failure deep in the handler. Now every op with a declared
required param rejects a missing one up front with a structured, named error
that also hints at the nesting fix — never a raised exception, never a silent
success.
"""
from __future__ import annotations

import pytest

mcp_server = pytest.importorskip("rvnd.mcp_server")


# ── workspace_workspace ─────────────────────────────────────────────────────

def test_workspace_workspace_missing_required_param_fails_loud():
    r = mcp_server.workspace_workspace("add", {})   # folder_context missing
    assert r["ok"] is False
    assert "folder_context" in r["error"]
    assert "params" in r["error"]        # nesting hint present
    assert "workspace_workspace" in r["error"]


def test_workspace_workspace_correctly_nested_call_succeeds(tmp_path):
    r = mcp_server.workspace_workspace("add", {"folder_context": str(tmp_path)})
    assert "error" not in r or r.get("ok") is not False
    assert r.get("ok") is True


def test_workspace_workspace_help_needs_no_params():
    r = mcp_server.workspace_workspace("help")
    assert isinstance(r, dict) and r.get("ops")
    assert {o["op"] for o in r["ops"]} >= {"add", "remove", "list", "bootstrap", "route"}


# ── workspace_folder ─────────────────────────────────────────────────────────

def test_workspace_folder_missing_required_param_fails_loud():
    r = mcp_server.workspace_folder("scan", {})     # folder_context missing
    assert r["ok"] is False
    assert "folder_context" in r["error"]
    assert "params" in r["error"]
    assert "workspace_folder" in r["error"]


def test_workspace_folder_correctly_nested_call_succeeds(tmp_path):
    r = mcp_server.workspace_folder("list", {"path": str(tmp_path)})
    assert "error" not in r


def test_workspace_folder_help_needs_no_params():
    r = mcp_server.workspace_folder("help")
    assert isinstance(r, dict) and r.get("ops")
    cat = {o["op"]: o["required"] for o in r["ops"]}
    assert cat == mcp_server._WORKSPACE_FOLDER_REQUIRED


# ── workspace_policy ─────────────────────────────────────────────────────────

def test_workspace_policy_missing_required_param_fails_loud():
    # op="add" style bug report: folder_context (and everything else) missing.
    r = mcp_server.workspace_policy("party_register", {"folder_context": "/x"})
    assert r["ok"] is False
    assert "party_id" in r["error"] and "kind" in r["error"]
    assert "params" in r["error"]
    assert "workspace_policy" in r["error"]


def test_workspace_policy_correctly_nested_call_succeeds(tmp_path):
    r = mcp_server.workspace_policy("snapshot", {"folder_context": str(tmp_path)})
    assert "error" not in r


def test_workspace_policy_help_needs_no_params():
    r = mcp_server.workspace_policy("help")
    assert isinstance(r, dict) and r.get("ops")
    assert {o["op"] for o in r["ops"]} >= {"snapshot", "party_register"}


# ── shared helper regression ────────────────────────────────────────────────

def test_require_op_params_facade_arg_is_optional_and_positional_compatible():
    # existing callers (e.g. test_mcp_input_schema.py) call with 3 positional
    # args and no facade — must keep working unchanged.
    r = mcp_server._require_op_params({"x": ["a"]}, "x", {})
    assert r["ok"] is False
    assert "a" in r["error"]
    assert r["facade"] == ""


def test_facade_required_from_table_matches_op_call_help():
    table = {"add": mcp_server.add_known_workspace, "list": mcp_server.list_known_workspaces}
    got = mcp_server._facade_required_from_table(table)
    assert got["add"] == ["folder_context"]
    assert got["list"] == []
