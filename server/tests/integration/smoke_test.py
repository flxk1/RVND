# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Local stdio smoke test for the agent-tool-lock MCP server.

Spawns `agent-tool-lock-mcp` as a subprocess, speaks the MCP initialize +
list_tools handshake over stdio, and confirms three tools are exposed.

Run from the repository root:
    python server/tests/integration/smoke_test.py

Exit code 0 = success, non-zero = failure. Suitable for CI gates and for the
post-change smoke test described in MCP_SMOKE_TEST.md.
"""

from __future__ import annotations

import asyncio
import sys


async def _run() -> int:
    # The mcp client lib is the supported way to drive an MCP server over stdio.
    try:
        from mcp.client.stdio import stdio_client, StdioServerParameters
        from mcp.client.session import ClientSession
    except ImportError:
        print("ERROR: mcp client SDK not installed. Run: pip install mcp", file=sys.stderr)
        sys.exit(2)

    # Run the server via `python -m` to avoid relying on the entry-point being on PATH
    # during CI / sandboxed test runs.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "rvnd.lock.mcp_server"],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = {t.name for t in tools_result.tools}
            expected = {"egress_check", "ingress_check", "audit_query"}
            missing = expected - tool_names
            if missing:
                print(f"FAIL: missing tools: {missing}", file=sys.stderr)
                print(f"      registered: {sorted(tool_names)}", file=sys.stderr)
                return 1
            print(f"OK: server responded to list_tools with {len(tool_names)} tools registered.")
            print(f"    {sorted(tool_names)}")
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
