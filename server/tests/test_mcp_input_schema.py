# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""MCP tool-input schema (workspace_policy): a known op missing a declared-required param
is rejected up front with a clean, declared error — not via a KeyError raised partway
through the handler. The required-param schema is enforced AND kept in sync with the
help catalogue. MCP-integration panel."""
from __future__ import annotations

from workspaces.mcp_server import workspace_policy, _require_op_params, _WORKSPACE_POLICY_REQUIRED


def test_missing_required_param_is_rejected_up_front():
    r = workspace_policy("party_register", {"folder_context": "/x"})  # missing party_id, kind
    assert "missing required param" in r["error"]
    assert r["op"] == "party_register"
    assert "party_id" in r["required"]


def test_empty_required_param_is_rejected():
    r = workspace_policy("snapshot", {"folder_context": ""})          # present but empty
    assert "missing required param" in r["error"]


def test_unknown_op_falls_through_to_facade_handling():
    # _require_op_params does not invent errors for ops it doesn't know.
    assert _require_op_params(_WORKSPACE_POLICY_REQUIRED, "no_such_op", {}) is None


def test_valid_call_passes_the_schema_gate(tmp_path):
    # a complete call clears the schema gate (it does not return the missing-param error)
    r = workspace_policy("party_list", {"folder_context": str(tmp_path)})
    assert "missing required param" not in (r.get("error", ""))


def test_schema_matches_help_catalogue_no_drift():
    # the enforced required-map must mirror the help catalogue's "required" lists.
    cat = {o["op"]: o.get("required", []) for o in workspace_policy("help")["ops"]}
    for op, req in _WORKSPACE_POLICY_REQUIRED.items():
        assert cat.get(op) == req, f"drift for {op!r}: catalogue={cat.get(op)} schema={req}"
