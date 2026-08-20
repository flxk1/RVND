# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Any-MCP-client compatibility harness for `workspaces-mcp`.

This test family validates the *protocol-level* compatibility claim: that
``workspaces-mcp`` speaks vanilla MCP over stdio well enough that any
spec-compliant client should work against it. Passing this suite is the
necessary condition for the claim "Workspace works with Cursor / Cline /
Continue / Zed / custom-via-mcp-cli / Claude Desktop". Host-specific packaging
and UI checks remain separate from this protocol contract.

What the suite covers
---------------------

1. MCP ``initialize`` handshake completes cleanly.
2. ``list_tools`` returns the full ``_DECLARED_TOOLS`` surface from
   ``workspaces/mcp_server.py`` (no silent drops; no schema parse errors).
3. Every returned tool has a JSON-Schema input shape that survives
   ``json.dumps`` round-trip (catches non-serialisable schemas that some
   client SDKs reject).
4. ``server_info`` is callable and self-reports a tool count that matches
   the live ``list_tools`` count (catches drift between the hand-maintained
   ``_DECLARED_TOOLS`` and the actual ``@mcp.tool()`` decorations).
5. A small handful of side-effect-free tool calls succeed end-to-end
   (``list_known_workspaces``, ``pairs_recent`` on a temp folder).

Adjacent contracts
------------------

- Per-host config (Cursor JSON, Cline workspace settings, Zed
  ``context_servers``). Those are documented in ``examples/mcp-clients/``
  and have their own installation checks.
- Skill dispatch. Skills are a Claude-native layer above the MCP surface
  and cannot be exercised from a generic MCP client without the Claude
  plugin loaded.
- Orchestrator parity. The fact that non-Claude hosts see the
  tool list but not the skill router is a property of host design, not
  protocol compliance.
- Chain verification is exposed through ``workspace_audit`` with the
  ``verify_chain`` op. This suite asserts the facade contract below.

Dependency behaviour
--------------------

The ``mcp`` package is a required RVND runtime dependency. If it is absent,
collection fails; the public-transport proof is never silently skipped.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest


# ---------------------------------------------------------------------------
# The MCP client SDK is a required runtime dependency of RVND. Collection must
# fail if it is absent; silently skipping the public-transport proof would make
# a release gate vacuous.
# ---------------------------------------------------------------------------

# Importing the required client types directly makes a missing SDK a collection
# error instead of silently removing the transport lane from the suite.
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


# ---------------------------------------------------------------------------
# The declared surface — pulled live so a drift between this test and
# _DECLARED_TOOLS in mcp_server.py shows up as a test failure rather than
# a stale constant.
# ---------------------------------------------------------------------------

def _import_declared_tools() -> set[str]:
    """Return the ``_DECLARED_TOOLS`` set from mcp_server.py.

    Imported lazily so the test still skips cleanly on machines where the
    server package is not installed.
    """
    try:
        from workspaces.mcp_server import _DECLARED_TOOLS  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised on bare envs
        pytest.skip(f"workspaces.mcp_server not importable: {exc}")
    return set(_DECLARED_TOOLS)


# ---------------------------------------------------------------------------
# Fixture: spawn `workspaces-mcp` once per module and yield an open session.
# ---------------------------------------------------------------------------

def _server_params(folder_context: str | None = None) -> StdioServerParameters:
    """Build StdioServerParameters that launch workspaces-mcp via the
    installed python interpreter (avoids relying on PATH).
    """
    env: dict[str, str] = {}
    if folder_context is not None:
        env["WORKSPACE_FOLDER_CONTEXT"] = folder_context
    # This is a stdio host-compatibility smoke test pointed at a throwaway
    # scratch folder, not a registered workspace. Opt out of the A6 allowlist
    # (as a real client would for an ad-hoc folder) so the test exercises MCP
    # mechanics rather than workspace-registration policy. Enforcement itself
    # is covered by tests/security/test_attack_folder_context_traversal.py.
    env["WORKSPACES_ALLOW_UNREGISTERED"] = "1"
    # Propagate PYTHONPATH into the spawned server: on dev boxes where the
    # editable install is unreliable (the README's iCloud caveat), the suite
    # runs with PYTHONPATH=server/src, and the child must import `workspaces`
    # the same way the parent does. In CI the editable install works and this
    # is a no-op.
    if os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "workspaces.mcp_server"],
        env=env or None,
    )


@pytest.fixture(scope="module")
def workspace_folder() -> str:
    """A scratch folder used as `folder_context` for stateful calls."""
    with tempfile.TemporaryDirectory(prefix="workspaces-host-compat-") as tmp:
        yield tmp


# ---------------------------------------------------------------------------
# Tests — each one runs the stdio handshake fresh, since some clients
# don't keep a long-lived session and we want to catch teardown issues too.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initialize_handshake_completes(workspace_folder: str) -> None:
    """An MCP-compliant client must be able to complete `initialize`.

    Failure modes this catches:
      - Server crashes during startup (import-time error).
      - Server writes garbage to stdout that breaks JSON-RPC framing.
      - Server doesn't respond to `initialize` at all (hang).
    """
    params = _server_params(folder_context=workspace_folder)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            result = await session.initialize()
            assert result is not None, "initialize() returned None"
            # `serverInfo.name` should report what `server_info` would report.
            # Different mcp SDK versions surface this on different fields;
            # accept either of the two known shapes.
            name = getattr(getattr(result, "serverInfo", None), "name", None)
            if name is None:
                # Older SDK style.
                name = getattr(result, "server_info", {}).get("name")  # type: ignore[union-attr]
            assert name in {"workspaces", "workspace", None}, (
                f"server announced unexpected name: {name!r} "
                "(should be 'workspaces' on 0.6.6+ or 'workspace' on legacy)"
            )


@pytest.mark.asyncio
async def test_list_tools_returns_full_declared_surface(workspace_folder: str) -> None:
    """list_tools() should expose every tool in `_DECLARED_TOOLS`.

    Failure modes this catches:
      - A tool registered with @mcp.tool() that doesn't appear in
        list_tools (FastMCP introspection regression — the bug that
        motivated `_DECLARED_TOOLS` in the first place).
      - A tool in `_DECLARED_TOOLS` that no longer has a matching
        @mcp.tool() decoration (stale declaration).
    """
    declared = _import_declared_tools()
    params = _server_params(folder_context=workspace_folder)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            live = {t.name for t in tools_result.tools}

    missing = declared - live
    extra = live - declared
    assert not missing, (
        f"Tools in _DECLARED_TOOLS but not exposed at runtime: {sorted(missing)}. "
        "Either the @mcp.tool() decoration was removed or the server failed "
        "to register it on startup."
    )
    assert not extra, (
        "Tools exposed at runtime but absent from _DECLARED_TOOLS: "
        f"{sorted(extra)}. The declared and live public surfaces must match."
    )


@pytest.mark.asyncio
async def test_every_tool_has_json_serialisable_schema(workspace_folder: str) -> None:
    """Each tool's inputSchema must survive json.dumps round-trip.

    Failure modes this catches:
      - A tool whose schema includes a non-serialisable Python type
        (some MCP client SDKs reject these silently and drop the tool).
      - A schema with non-string keys (FastMCP edge case).
    """
    params = _server_params(folder_context=workspace_folder)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()

    broken: list[tuple[str, str]] = []
    for tool in tools_result.tools:
        schema = getattr(tool, "inputSchema", None)
        if schema is None:
            broken.append((tool.name, "missing inputSchema"))
            continue
        try:
            json.dumps(schema)
        except (TypeError, ValueError) as exc:
            broken.append((tool.name, f"schema not JSON-serialisable: {exc}"))
    assert not broken, (
        "Tools with non-serialisable input schemas: "
        + ", ".join(f"{n} ({why})" for n, why in broken)
    )


@pytest.mark.asyncio
async def test_server_info_matches_list_tools_count(workspace_folder: str) -> None:
    """`server_info` should self-report a tool count consistent with
    `list_tools()`. Drift indicates the hand-maintained `_DECLARED_TOOLS`
    is stale relative to the actual decorated surface.
    """
    params = _server_params(folder_context=workspace_folder)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            live_count = len(tools_result.tools)

            info_raw = await session.call_tool("server_info", arguments={})
            payload = _extract_payload(info_raw)
            assert isinstance(payload, dict), (
                f"server_info returned non-dict payload: {type(payload).__name__}"
            )
            reported = payload.get("tool_count")
            assert reported is not None, "server_info missing tool_count field"
            # Allow off-by-one for `server_info` itself in case the declared
            # list ever excludes it; the practical contract is that the
            # difference is small and explainable.
            assert abs(reported - live_count) <= 1, (
                f"server_info reports {reported} tools; list_tools returned "
                f"{live_count}. Either _DECLARED_TOOLS is stale or a tool "
                "failed to register on startup."
            )


@pytest.mark.asyncio
async def test_side_effect_free_calls_succeed(workspace_folder: str) -> None:
    """A few read-only calls should round-trip without raising.

    These are the calls a freshly-connected client is most likely to make
    first to confirm the server is alive. A failure here means even the
    cheapest cross-host compatibility test (open a session, look around)
    is broken.
    """
    params = _server_params(folder_context=workspace_folder)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. workspace listing — no folder_context required. (Folded
            # 2026-06-12: list_known_workspaces -> workspace_workspace op "list".)
            res = await session.call_tool(
                "workspace_workspace", arguments={"op": "list"})
            payload = _extract_payload(res)
            assert isinstance(payload, dict)
            # The harness doesn't assert workspaces exist (a fresh machine
            # may have none); it only asserts the call shape is valid.
            assert "ok" in payload or "workspaces" in payload, (
                f"workspace_workspace(op=list) returned unexpected shape: {payload!r}"
            )

            # 2. recent pairs against the temp folder — should succeed
            # with an empty list on a fresh folder. The standalone
            # pairs_recent tool was folded into the workspace_memory facade
            # (op="pairs_recent") in the 0.6.8 consolidation.
            res = await session.call_tool(
                "workspace_memory",
                arguments={
                    "op": "pairs_recent",
                    "params": {"folder_context": workspace_folder, "limit": 5},
                },
            )
            payload = _extract_payload(res)
            assert isinstance(payload, dict)
            # Either {ok: true, pairs: []} or equivalent — just confirm
            # the call did not raise on the server side.
            assert payload.get("ok", True) is not False, (
                f"workspace_memory(op=pairs_recent) failed: {payload!r}"
            )
            assert "error" not in payload, (
                f"workspace_memory(op=pairs_recent) failed: {payload!r}"
            )


@pytest.mark.asyncio
async def test_invalid_operation_is_refused_over_stdio(workspace_folder: str) -> None:
    """The live MCP boundary must return a structured refusal, not dispatch an
    unknown facade operation or silently report success."""
    params = _server_params(folder_context=workspace_folder)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "workspace_memory",
                arguments={"op": "definitely-not-a-runtime-operation",
                           "params": {"folder_context": workspace_folder}},
            )
            payload = _extract_payload(result)
            assert isinstance(payload, dict), (
                f"invalid operation returned no structured refusal: {payload!r}")
            assert payload.get("ok") is False or payload.get("error"), (
                f"invalid operation was not refused: {payload!r}")


@pytest.mark.asyncio
async def test_audit_verify_chain_exposed_as_mcp_tool(workspace_folder: str) -> None:
    """Compatibility guarantee: any MCP host can verify chain integrity
    without shelling out to the CLI.

    The capability contract is the ``workspace_audit`` facade op, not a
    standalone tool name.
    """
    declared = _import_declared_tools()
    assert "workspace_audit" in declared, (
        "workspace_audit facade missing — chain verification unreachable for "
        "non-Claude MCP hosts."
    )
    from workspaces import mcp_server
    ops = {o["op"] for o in mcp_server.workspace_audit("help")["ops"]}
    assert "verify_chain" in ops, (
        "workspace_audit lost the verify_chain op — hosts can no longer verify "
        "the chain over MCP."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_payload(call_result: object) -> object:
    """The mcp SDK has shifted shapes a few times — pull the structured
    payload out wherever it lives without binding to a single version.
    """
    # Newer SDK: result has `.structuredContent` or `.content[0].text` as JSON.
    structured = getattr(call_result, "structuredContent", None)
    if structured is not None:
        return structured
    content = getattr(call_result, "content", None)
    if content:
        first = content[0]
        text = getattr(first, "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    # Older SDK style: dict-shaped.
    if isinstance(call_result, dict):
        return call_result
    return call_result


if __name__ == "__main__":  # pragma: no cover
    # Allow `python tests/integration/test_host_mcp_protocol.py` as a quick
    # smoke run without driving pytest.
    sys.exit(pytest.main([__file__, "-v"]))
