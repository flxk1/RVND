# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""MCP-server wrapper for agent-tool-lock.

Exposes the core middleware as MCP tools so any MCP-compatible host
(Claude Desktop, Cowork, an agent framework, etc.) can call them.

Tools exposed:
- egress_check  — pre-call: detect over-collection / PII / capability-token issues
- ingress_check — post-call: redact over-returned fields and PII in responses
- audit_query   — read recent audit-log entries (for debugging and DPIA evidence)

Configuration via environment variables:
- AGENT_TOOL_LOCK_AUDIT_LOG   — path to JSONL audit log (optional; default no logging)
- AGENT_TOOL_LOCK_DEFAULT_MODE — fallback mode if a tool call omits it
                                     (standard | strict | permissive | audit_only)

Run:
    python -m workspaces.lock.mcp_server
or via the installed entry point:
    agent-tool-lock-mcp
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .core import (
    AuditLog,
    CapabilityToken,
    Mode,
    ToolCall,
    ToolResponse,
    egress,
    ingress,
)

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP("agent-tool-lock")


def _get_default_mode() -> Mode:
    value = (os.environ.get("AGENT_TOOL_LOCK_DEFAULT_MODE") or "standard").lower()
    try:
        return Mode(value)
    except ValueError:
        return Mode.STANDARD


def _get_audit() -> AuditLog | None:
    path = os.environ.get("AGENT_TOOL_LOCK_AUDIT_LOG")
    return AuditLog(path) if path else None


def _parse_mode(mode: str | None) -> Mode:
    if mode is None:
        return _get_default_mode()
    try:
        return Mode(mode.lower())
    except ValueError:
        return Mode.STANDARD


def _parse_token(token_dict: dict | None) -> CapabilityToken | None:
    if token_dict is None:
        return None
    try:
        return CapabilityToken.from_dict(token_dict)
    except (KeyError, TypeError):
        # Caller passed a malformed token; treat as missing rather than crash.
        return None


def _decision_to_dict(decision) -> dict:
    """Serialise an EgressDecision or IngressDecision to a plain dict for MCP transport."""
    d = asdict(decision)
    # findings are already dataclass-converted by asdict; trim verbosity
    return d


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def egress_check(
    tool: str,
    arguments: dict,
    task_scope: list[str],
    mode: str | None = None,
    capability_token: dict | None = None,
) -> dict:
    """Pre-call middleware. Check whether a tool invocation is safe to send.

    Args:
        tool: Identifier of the tool the agent is about to call (e.g. "hr.get_employee").
        arguments: Dict of arguments the agent is about to pass to the tool.
        task_scope: List of field names legitimately needed by the current task.
        mode: One of "standard" (default), "strict", "permissive", "audit_only".
            STANDARD strips over-collection + warns. STRICT refuses on any violation.
            PERMISSIVE detects only. AUDIT_ONLY records, never mutates.
        capability_token: Optional dict of capability-token claims (see
            docs/reference/capability-token-format.md). Claim semantics are
            always checked. Set LOCK_BETA_STRICT_TOKEN_SIG=1 and configure
            LOCK_CAPABILITY_TRUST_STORE to require issuer signatures.

    Returns:
        Dict with keys:
        - action: "allow" | "strip" | "refuse"
        - findings: list of {tier, type, severity, field, detail, confidence}
        - modified_call: present if action="strip"; the sanitised ToolCall as dict
        - stripped_fields: list of field names removed
        - reason: short human-readable rationale
    """
    call = ToolCall(
        tool=tool,
        arguments=arguments,
        capability_token=_parse_token(capability_token),
    )
    decision = egress(
        call=call,
        task_scope=set(task_scope),
        mode=_parse_mode(mode),
        audit=_get_audit(),
    )
    out = _decision_to_dict(decision)
    # modified_call is a ToolCall — re-serialise the call portion as a transport-friendly dict
    if decision.modified_call is not None:
        out["modified_call"] = {
            "tool": decision.modified_call.tool,
            "arguments": decision.modified_call.arguments,
        }
    return out


@mcp.tool()
def ingress_check(
    payload: dict,
    task_scope: list[str],
    mode: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Post-call middleware. Check a tool response before it enters the agent's context.

    Args:
        payload: Dict the tool returned.
        task_scope: List of field names legitimately needed by the current task.
        mode: As in egress_check.
        task_id: Optional task identifier for audit-trail correlation across calls.

    Returns:
        Dict with keys:
        - action: "allow" | "redact"
        - findings: list of {tier, type, severity, field, detail, confidence}
        - redacted_payload: present if action="redact"; the response with
            over-returned or PII fields replaced by "[REDACTED]"
        - reason: short human-readable rationale
    """
    response = ToolResponse(payload=payload)
    decision = ingress(
        response=response,
        task_scope=set(task_scope),
        mode=_parse_mode(mode),
        audit=_get_audit(),
        task_id=task_id,
    )
    return _decision_to_dict(decision)


@mcp.tool()
def audit_query(limit: int = 50) -> dict:
    """Read the most recent entries from the audit log.

    Args:
        limit: Maximum number of entries to return (default 50, max 500).

    Returns:
        Dict with keys:
        - entries: list of audit-log records (most recent last)
        - total_lines_in_log: total number of lines currently in the audit log
        - audit_log_path: path to the audit log, or None if not configured
    """
    path = os.environ.get("AGENT_TOOL_LOCK_AUDIT_LOG")
    if not path:
        return {
            "entries": [],
            "total_lines_in_log": 0,
            "audit_log_path": None,
            "note": "AGENT_TOOL_LOCK_AUDIT_LOG env var not set; no audit log to read.",
        }
    limit = max(1, min(limit, 500))
    log_path = Path(path)
    if not log_path.exists():
        return {
            "entries": [],
            "total_lines_in_log": 0,
            "audit_log_path": str(log_path),
            "note": "Audit log path configured but file does not exist yet.",
        }
    lines = log_path.read_text().splitlines()
    tail = lines[-limit:]
    entries = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append({"_parse_error": True, "raw": line[:200]})
    return {
        "entries": entries,
        "total_lines_in_log": len(lines),
        "audit_log_path": str(log_path),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
