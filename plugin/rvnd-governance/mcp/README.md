# mcp/ — connecting the RVND governance server

These skills do not contain a governance engine. They drive the **RVND governance MCP server**,
which runs locally on the operator's own hardware. This directory names that server and describes
the connection; it does not bundle it.

## What you need

- RVND `>=0.6.8.4,<0.7`, installed in the Python environment that launches the server.
  RVND is free and open-source software under AGPL-3.0-only.
- Python matching RVND's requirements.

## Wiring it up

`rvnd.mcp.json` is a connection descriptor in the common `mcpServers` shape. Register it with your
MCP-capable host; it launches the installed `rvnd.mcp_server` module. The server speaks the
Model Context Protocol over stdio and binds to the local machine only.

The governance layer is on by default here (`RVND_GOVERNANCE_LAYER=on`). Setting it to `off`
disables the governance interface, at which point these skills fail closed — they will not
fabricate verdicts.

## How the skills find the tools

The skills never hardcode tool names. At run time they read the host's live MCP tool list for the
`rvnd-governance` server and resolve the canonical verbs to whatever the server currently exposes
(`references/catalogue.md` holds the mapping and the discovery procedure). If an expected
operation is absent, the skills treat it as unavailable and stop — they do not emulate governance
locally.

## Boundaries

- The server decides; the host renders. Nothing in this plugin computes a verdict.
- Local-first: no cloud endpoint is contacted for a folder marked local-only, and the server
  builds no network request on those paths.
- Fail-closed: if the server is unreachable, the governance layer is off, or a principal cannot be
  resolved, consequential actions do not proceed.
