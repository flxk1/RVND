# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Solver guards as Workspaces building blocks: the theory-grounded
guards reach the MCP surface as OPS on the existing workspace_audit facade — no new
tool (tool-count budget). Proves the pipeline composes from existing building
blocks: each new module is the implementation behind a facade op.

Claims under test (written BEFORE the wiring):
  F1  workspace_audit('completeness', ...) returns a completeness report
  F2  workspace_audit('variety', ...) returns a requisite-variety check from plain
      type lists (JSON-clean, no token objects needed)
  F3  workspace_audit('accountability', ...) audits a warrant list
  F4  the three ops appear in workspace_audit('help')
  F5  an unknown op still errors cleanly (facade contract preserved)
  F6  the registered tool surface is UNCHANGED — these are ops, not tools
"""
from __future__ import annotations

import pytest

from workspaces import mcp_server


def test_completeness_op():                                       # F1
    r = mcp_server.workspace_audit("completeness", {
        "doc_type": "services-contract-de",
        "detected_types": ["liability_cap", "data_processing"],
        "covered_chars": 800, "total_chars": 1000})
    assert "expected_absent" in r and "band" in r
    assert "ip_assignment" in r["expected_absent"]


def test_variety_op_from_type_lists():                            # F2
    r = mcp_server.workspace_audit("variety", {
        "covered_types": ["liability_cap", "data_processing"],
        "problem_types": ["liability_cap", "ip_assignment"]})
    assert r["ok"] is False
    assert r["uncovered"] == ["ip_assignment"]


def test_accountability_op_from_warrants():                       # F3
    ok = mcp_server.workspace_audit("accountability", {
        "warrants": ["detected", "recalled", "dependency"]})
    assert ok["accountable"] is True and ok["complexity"] == 3
    bad = mcp_server.workspace_audit("accountability", {
        "warrants": ["detected", ""]})
    assert bad["accountable"] is False


def test_ops_self_describe():                                     # F4
    ops = {o["op"] for o in mcp_server.workspace_audit("help")["ops"]}
    assert {"completeness", "variety", "accountability"} <= ops


def test_unknown_op_errors_cleanly():                             # F5
    r = mcp_server.workspace_audit("not-an-op", {})
    assert "error" in r


def test_tool_surface_unchanged():                                # F6
    info = mcp_server.server_info()
    assert "workspace_audit" in info["tools"]
    # the guards are ops on workspace_audit, not new tools
    for not_a_tool in ("completeness", "variety", "accountability"):
        assert not_a_tool not in info["tools"]
