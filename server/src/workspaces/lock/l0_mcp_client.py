# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""MCP-stdio client for workspace-l0-memory — sync facade.

Why this exists
---------------

The in-process Python bridge (:mod:`.l0_bridge`'s in-process path) only works
when ``agent-tool-lock`` and ``workspace-l0-memory`` are installed in the same
Python interpreter. When a host (Claude Desktop, Cursor, Cowork) launches
each plugin in its own process, the two plugins cannot reach each other via
Python import.

In that case the integration is **transport-mediated**: agent-tool-lock
spawns the ``workspace-l0-mcp`` server as a subprocess, performs the MCP
initialise handshake, calls the tools, and parses the JSON-RPC responses.

This module wraps the asynchronous MCP Python SDK in a synchronous facade.
Each call spawns a fresh subprocess, does one round-trip, and exits. That
adds ~100–300 ms of overhead per call — acceptable for an audit-floor
capture path which fires once per LLM call, not once per token.

The same public shapes are produced as :mod:`.l0_bridge`'s in-process
functions, so callers can treat both transports interchangeably.

If the ``mcp`` client SDK is not installed (it's an optional dependency),
:func:`mcp_is_available` returns False and the facade no-ops.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


__all__ = [
    "mcp_is_available",
    "mcp_try_load_policy",
    "mcp_try_capture_llm",
    "mcp_try_capture_web",
    "MCPClientResult",
    "DEFAULT_TIMEOUT_SECONDS",
]


DEFAULT_TIMEOUT_SECONDS = 15.0
"""Per-call ceiling. If the L0 MCP server doesn't respond in this long,
the facade gives up and returns a failure. Keeps the gate path from blocking
forever on a stuck subprocess."""


_MCP_AVAILABLE: bool | None = None


def mcp_is_available() -> bool:
    """Return True if the ``mcp`` client SDK is importable. Cached after first call."""
    global _MCP_AVAILABLE
    if _MCP_AVAILABLE is not None:
        return _MCP_AVAILABLE
    try:
        # The async stdio client lives here in mcp >= 1.0.
        from mcp import ClientSession  # noqa: F401
        from mcp.client.stdio import stdio_client  # noqa: F401
        from mcp import StdioServerParameters  # noqa: F401
        _MCP_AVAILABLE = True
    except ImportError:
        _MCP_AVAILABLE = False
    return _MCP_AVAILABLE


# Test hook — mirrors the in-process bridge's :func:`._set_l0_available`.
def _set_mcp_available(value: bool | None) -> None:
    global _MCP_AVAILABLE
    _MCP_AVAILABLE = value


@dataclass
class MCPClientResult:
    """Outcome of a single MCP tool call from the bridge."""

    success: bool
    payload: dict[str, Any]
    """The tool's return dict on success. Empty dict on failure."""
    error: str = ""


def _server_command() -> list[str] | None:
    """Resolve the command to launch the L0 MCP server.

    Priority:
      1. ``AGENT_TOOL_LOCK_L0_MCP_CMD`` env var (shell-split with ``shlex``).
      2. ``workspace-l0-mcp`` on PATH (the installed console-script entry point).

    Returns None if neither is reachable.
    """
    explicit = os.environ.get("AGENT_TOOL_LOCK_L0_MCP_CMD")
    if explicit:
        parts = shlex.split(explicit)
        if parts:
            return parts
    # Fallback to the console-script name.
    import shutil
    found = shutil.which("workspace-l0-mcp")
    if found:
        return [found]
    return None


async def _call_tool_async(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    command: list[str],
    timeout_seconds: float,
) -> MCPClientResult:
    """Spawn the MCP server, call one tool, return the parsed result."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=command[0], args=command[1:])

    async def _do_call():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool_name, tool_args)

    try:
        response = await asyncio.wait_for(_do_call(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return MCPClientResult(success=False, payload={}, error="timeout")
    except Exception as e:
        return MCPClientResult(success=False, payload={}, error=f"call_failed:{e}")

    # FastMCP returns CallToolResult — extract structured content.
    payload: dict[str, Any] = {}
    structured = getattr(response, "structuredContent", None)
    if isinstance(structured, dict):
        payload = structured
    else:
        # Fallback: some servers return the dict inside the first text content block.
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                import json
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        payload = parsed
                        break
                except json.JSONDecodeError:
                    continue
    return MCPClientResult(success=True, payload=payload)


def _call_tool_sync(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> MCPClientResult:
    """Synchronous wrapper. Spawns its own event loop per call."""
    if not mcp_is_available():
        return MCPClientResult(success=False, payload={}, error="mcp_sdk_unavailable")
    command = _server_command()
    if command is None:
        return MCPClientResult(success=False, payload={}, error="no_l0_mcp_command")
    try:
        return asyncio.run(
            _call_tool_async(
                tool_name,
                tool_args,
                command=command,
                timeout_seconds=timeout_seconds,
            )
        )
    except RuntimeError as e:
        # Nested event loop (asyncio.run inside running loop). Caller is
        # responsible for running us off the hot loop.
        return MCPClientResult(success=False, payload={}, error=f"event_loop:{e}")


# ---------------------------------------------------------------------------
# Public facade — mirrors the in-process bridge's three functions
# ---------------------------------------------------------------------------


def mcp_try_load_policy(folder_context: str | Path) -> MCPClientResult:
    """Call ``policy_snapshot`` on the L0 MCP server."""
    return _call_tool_sync(
        "policy_snapshot",
        {"folder_context": str(folder_context)},
    )


def mcp_try_capture_llm(
    *,
    folder_context: str | Path,
    model: str,
    prompt_context: str,
    response: str,
    cited_sources: list[str] | None = None,
    cost_estimate_cents: float | None = None,
    tool_call_trace: list[dict[str, Any]] | None = None,
    request_id: str = "",
    oversight_level: str = "approve",
    mode: str = "agentic",
    actor: str = "agent:lock",
) -> MCPClientResult:
    """Call ``capture_llm`` on the L0 MCP server."""
    args: dict[str, Any] = {
        "folder_context": str(folder_context),
        "model": model,
        "prompt_context": prompt_context,
        "response": response,
        "cited_sources": list(cited_sources or []),
        "tool_call_trace": list(tool_call_trace or []),
        "request_id": request_id,
        "mode": mode,
        "oversight": oversight_level,
        "actor": actor,
    }
    if cost_estimate_cents is not None:
        args["cost_estimate_cents"] = cost_estimate_cents
    return _call_tool_sync("capture_llm", args)


def mcp_try_capture_web(
    *,
    folder_context: str | Path,
    query: str,
    engine: str,
    results: list[dict[str, Any]],
    cost_estimate_cents: float | None = None,
    request_id: str = "",
    oversight_level: str = "approve",
    mode: str = "agentic",
    actor: str = "agent:lock",
) -> MCPClientResult:
    """Call ``capture_web`` on the L0 MCP server."""
    args: dict[str, Any] = {
        "folder_context": str(folder_context),
        "query": query,
        "engine": engine,
        "results": list(results or []),
        "request_id": request_id,
        "mode": mode,
        "oversight": oversight_level,
        "actor": actor,
    }
    if cost_estimate_cents is not None:
        args["cost_estimate_cents"] = cost_estimate_cents
    return _call_tool_sync("capture_web", args)
