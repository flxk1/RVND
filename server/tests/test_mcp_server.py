# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the MCP-server wrapper.

We don't run the MCP protocol over stdio here — that's framework-level.
Instead we test:
1. The server registers the expected tools.
2. The tool functions (the underlying callables FastMCP exposes) behave correctly
   when invoked directly with dict inputs, matching the egress/ingress contract.
"""

from __future__ import annotations

import json
import time

import pytest

from workspaces.lock.mcp_server import (
    audit_query,
    egress_check,
    ingress_check,
    mcp,
)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_registers_three_tools():
    """The FastMCP instance should expose exactly the three tools."""
    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}
    assert "egress_check" in tool_names
    assert "ingress_check" in tool_names
    assert "audit_query" in tool_names


# ---------------------------------------------------------------------------
# Direct invocation — mirrors the core egress/ingress semantics
# ---------------------------------------------------------------------------


def _make_token_dict(*, exp_offset: int = 60, aud: str = "hr.get_employee") -> dict:
    now = int(time.time())
    return {
        "iss": "identity.example",
        "sub": "agent:test",
        "aud": aud,
        "iat": now,
        "exp": now + exp_offset,
        "scope": {
            "regions": ["EU-WEST"],
            "identifier_classes": ["employee_id"],
            "retention_class": "task-bound",
            "fields": ["name", "role"],
            "purpose": "scheduling",
        },
        "controller": "deployer.example",
        "task_id": "task-mcp-001",
    }


def test_egress_check_strips_over_collection_in_standard():
    result = egress_check(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1", "include_salary_band": True},
        task_scope=["employee_id"],
        mode="standard",
        capability_token=_make_token_dict(),
    )
    assert result["action"] == "strip"
    assert "include_salary_band" in result["stripped_fields"]
    assert "modified_call" in result
    assert "include_salary_band" not in result["modified_call"]["arguments"]


def test_egress_check_strict_refuses():
    result = egress_check(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1", "include_salary_band": True},
        task_scope=["employee_id"],
        mode="strict",
        capability_token=_make_token_dict(),
    )
    assert result["action"] == "refuse"


def test_egress_check_audit_only_never_blocks():
    result = egress_check(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1", "include_salary_band": True},
        task_scope=["employee_id"],
        mode="audit_only",
        capability_token=_make_token_dict(),
    )
    assert result["action"] == "allow"


def test_egress_check_allows_clean_call():
    result = egress_check(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1"},
        task_scope=["employee_id"],
        mode="standard",
        capability_token=_make_token_dict(),
    )
    assert result["action"] == "allow"


def test_egress_check_handles_missing_token():
    result = egress_check(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1"},
        task_scope=["employee_id"],
        mode="permissive",
        capability_token=None,
    )
    # Permissive never blocks; the missing-token finding is surfaced but doesn't change action.
    assert result["action"] == "allow"
    finding_details = " ".join(f["detail"] for f in result["findings"])
    assert "no capability token" in finding_details


def test_egress_check_handles_malformed_token():
    result = egress_check(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1"},
        task_scope=["employee_id"],
        mode="permissive",
        capability_token={"iss": "only this key"},  # missing required fields
    )
    # Malformed token treated as missing — should not crash.
    assert result["action"] == "allow"


def test_egress_check_unknown_mode_defaults_to_standard():
    result = egress_check(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1", "include_salary_band": True},
        task_scope=["employee_id"],
        mode="zzz-invalid",
        capability_token=_make_token_dict(),
    )
    # Unknown mode → STANDARD → strips
    assert result["action"] == "strip"


def test_ingress_check_redacts_unrequested_fields():
    result = ingress_check(
        payload={"name": "Maria", "role": "Engineer", "salary_band": "L4"},
        task_scope=["name", "role"],
        mode="standard",
    )
    assert result["action"] == "redact"
    assert result["redacted_payload"]["salary_band"] == "[REDACTED]"
    assert result["redacted_payload"]["name"] == "Maria"


def test_ingress_check_allows_clean_response():
    result = ingress_check(
        payload={"name": "Maria", "role": "Engineer"},
        task_scope=["name", "role"],
        mode="standard",
    )
    assert result["action"] == "allow"


def test_ingress_check_passes_task_id_through():
    # We don't have a way to read the audit without configuring it,
    # but at least the call should not error when task_id is provided.
    result = ingress_check(
        payload={"name": "Maria"},
        task_scope=["name"],
        mode="standard",
        task_id="task-mcp-correlate-9",
    )
    assert result["action"] == "allow"


# ---------------------------------------------------------------------------
# audit_query
# ---------------------------------------------------------------------------


def test_audit_query_when_log_not_configured(monkeypatch):
    monkeypatch.delenv("AGENT_TOOL_LOCK_AUDIT_LOG", raising=False)
    result = audit_query()
    assert result["entries"] == []
    assert result["audit_log_path"] is None
    assert "note" in result


def test_audit_query_reads_log_when_configured(monkeypatch, tmp_path):
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text(
        json.dumps({"ts": 1.0, "kind": "egress", "tool": "x", "action": "allow"}) + "\n" +
        json.dumps({"ts": 2.0, "kind": "ingress", "tool": "x", "action": "redact"}) + "\n"
    )
    monkeypatch.setenv("AGENT_TOOL_LOCK_AUDIT_LOG", str(log_path))
    result = audit_query(limit=10)
    assert result["total_lines_in_log"] == 2
    assert len(result["entries"]) == 2
    assert result["entries"][0]["tool"] == "x"


def test_audit_query_respects_limit(monkeypatch, tmp_path):
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text(
        "\n".join(json.dumps({"ts": i, "n": i}) for i in range(20)) + "\n"
    )
    monkeypatch.setenv("AGENT_TOOL_LOCK_AUDIT_LOG", str(log_path))
    result = audit_query(limit=5)
    assert len(result["entries"]) == 5
    # Most-recent semantic: tail of file
    assert result["entries"][-1]["n"] == 19


def test_audit_query_caps_limit_at_500(monkeypatch, tmp_path):
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text(
        "\n".join(json.dumps({"n": i}) for i in range(1000)) + "\n"
    )
    monkeypatch.setenv("AGENT_TOOL_LOCK_AUDIT_LOG", str(log_path))
    result = audit_query(limit=5000)  # absurd limit
    assert len(result["entries"]) == 500
