# MCP-host smoke test — 10-minute manual verification

Run this once after every material change to `mcp_server.py` or the lock modules.
Confirms the runtime works end-to-end through a real MCP host (Claude Desktop), not
just at the unit-test level.

> Since 0.6.6 the lock tools are **merged into the main `workspaces-mcp` server** as
> ops of the `workspace_lock` facade; the standalone lock server survives only as a
> module (`python -m workspaces.lock.mcp_server`) for the stdio smoke test below.

## Prerequisites

- The runtime is installed (`pip install -e ".[test]"` from the repo root — see
  [server/INSTALL.md](../../INSTALL.md); `mcp` is a required dependency, not an extra).
- The entry point `workspaces-mcp` is on PATH (check with `which workspaces-mcp`).
- Claude Desktop is installed.

## Step 1 — Local stdio smoke test (no host required, 2 min)

```bash
python server/tests/integration/smoke_test.py
```

Expected: `OK: server responded to list_tools with 3 tools registered.`
(This drives the lock module directly over stdio.)

If this fails, the MCP layer doesn't start cleanly — fix before touching Claude
Desktop config.

## Step 2 — Configure Claude Desktop (3 min)

Find the Claude Desktop config file:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add (or merge into) the `mcpServers` block:

```json
{
  "mcpServers": {
    "rvnd": {
      "command": "workspaces-mcp"
    }
  }
}
```

Restart Claude Desktop fully (Cmd+Q, relaunch).

## Step 3 — Verify the tools are registered (1 min)

In a new Claude Desktop chat, type:

> List the MCP tools available to you.

Expected: Claude lists the `workspace_*` facade family (`workspace_lock`,
`workspace_audit`, `workspace_workflow`, …; the exact count is pinned by
`test_mcp_facades.py::test_declared_equals_registered`). If not, check the Claude
Desktop log for MCP-server startup errors.

## Step 4 — Functional check: egress (2 min)

In the same chat, type:

> Use the workspace_lock MCP tool with op="egress_check" and params
> {"tool": "hr.get_employee", "arguments": {"employee_id": "E-1",
> "include_salary_band": true}, "task_scope": ["employee_id"], "mode": "standard"}.
> Tell me what action it returned.

Expected: action `"strip"`, with `"include_salary_band"` listed in `stripped_fields`,
and a modified_call without that field.

## Step 5 — Functional check: ingress (1 min)

> Use workspace_lock with op="ingress_check" and params {"payload": {"name": "Maria",
> "role": "Engineer", "salary_band": "L4"}, "task_scope": ["name", "role"],
> "mode": "standard"}. Tell me what action it returned.

Expected: action `"redact"`, redacted_payload has `"salary_band": "[REDACTED]"`.

## Step 6 — Audit verification (1 min)

> Use workspace_lock with op="audit_query" and params
> {"reason_for_query": "post-change smoke test", "limit": 10}.

Expected: Claude reports the two entries from steps 4 and 5 with their timestamps and
actions. Argument schemas present; raw values absent.

## What to do on failure

- **Server doesn't start**: check `workspaces-mcp --help` runs from CLI. Check the
  Python path is correct (`workspaces-doctor` reports which interpreter each script
  resolves to).
- **Tools not visible in Claude Desktop**: check JSON syntax in the config file. Check
  Claude Desktop logs (Help → View Logs).
- **Action returns "allow" when it should "strip"**: confirm `mode="standard"` is
  being passed; the op defaults from env if omitted.

## Frequency

Run this:
- After any change to `mcp_server.py` or the lock modules
- After every dependency bump (mcp, pytest-asyncio, etc.)
- Once before tagging a new version
