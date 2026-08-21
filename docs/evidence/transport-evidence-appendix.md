<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Transport evidence

Companion to `docs/evidence/capability-register.json`. The register lists every declared
operation as **deferred**: no committed test crosses that operation's public
transport for *both* a validated success *and* a validated refusal, so nothing is
classified supported. This document records the partial real-transport evidence
that exists. None of it is sufficient for a supported classification on its own.

## What "real transport" means here

An in-process call — `getattr(mcp_server, facade)(op, params)`, `gateway._dispatch(...)`,
`serve._facade_call(...)` — does **not** cross a transport. The
`docs/evidence/capability-coverage-matrix.json` smoke matrix is exactly this kind of
in-process call and proves only that the Python function does not crash; it is
recorded as the register's `callable` fact and confers no support.

Real-transport evidence crosses a process/serialization boundary:

| transport | boundary crossed | committed test |
|---|---|---|
| CLI | `python -m rvnd.cli` subprocess (argv in, text/exit out) | `server/tests/test_cli_channel.py` |
| MCP stdio | started MCP host; `ClientSession` initialize → `call_tool` → deserialize | `server/tests/integration/test_host_mcp_protocol.py` |
| HTTP `/tool` | booted `serve.py`; jsdom `fetch('/tool')` from the console | `app/{shell,panels}/*_render_test.py` (reconciled in `app/tests/ui_walk_reconcile_test.py`) |

## Evidence that exists (success unless noted)

### CLI — success **and** refusal (8 operations)
`test_cli_channel.py` drives each mapped op as a real subprocess with a valid and
an invalid argv, asserting exit/refusal. These are the only ops with *both*
outcomes proven across a real boundary — but the boundary is the CLI, which is not
one of the register's public tiers (ui/gateway/mcp), so they remain deferred for
those tiers:
`cross_workspace_read`, `workspace_audit/shadow_scan`, `workspace_audit/verify_chain`,
`workspace_lens/precedent_declare`, `workspace_lens/precedent_revoke`,
`workspace_lens/precedent_list`, `workspace_lens/budget_cap_set`,
`workspace_dispatch/list_pinned`.

### MCP stdio — success only (2 operations + discovery)
`test_host_mcp_protocol.py` calls and validates over a started host:
`workspace_workspace/list`, `workspace_memory/pairs_recent`. It also proves
`initialize`, `list_tools` discovery of the full declared tool surface, and
JSON-schema serialisability of every tool. Discovery is not callability; no
refusal is exercised over the transport.

### HTTP `/tool` — render-gate transport
The render gates boot a real `serve.py` and drive the console through jsdom, whose
`fetch('/tool')` crosses real HTTP. The current generated UI-walk evidence is
`docs/evidence/ui-walk-matrix.json`. Success-only execution does not establish
support: a supported operation also needs a validated refusal over the same
public boundary, including bridge-auth and principal enforcement.

### Gateway serving boundary — none
No committed test crosses the gateway *serving* transport; the smoke matrix and
the deferred-exposure probe use the private `gateway._dispatch` function only.

## Consequence

Under the strict rule, an operation remains deferred until its public transport
has both a validated success and a validated refusal. MCP, HTTP `/tool`, and the
gateway serving boundary are assessed independently; in-process evidence cannot
substitute for any of them.
