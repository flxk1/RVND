# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace Gateway — curated governance surface for workflow engines.

A SECOND FastMCP instance ("workspace-gateway") exposing exactly seven tools of
the full workspaces server over streamable HTTP, so workflow builders (n8n via
its MCP Client node, Langdock via custom-MCP URL) can call Shield/Lock
gates, oversight approvals, the Grounder, and the audit chain as workflow
steps.

Standing decisions:

- DETECTIVE, NOT PREVENTIVE. A flow that does not route through these
  gates produces no findings and no approvals. The gateway provides
  auditable governance for flows that opt in — never market it as
  enforcement.
- TRIFECTA INVARIANT. The profile must never contain a tool that reads
  private folder memory (workspace_memory / workspace_folder / workspace_mirror / search
  / by_id / write paths). No gateway token may both read private data and
  reach an egress-capable op. Enforced by test_gateway.py.
- PER-HOST TOKENS. Each connecting host tool gets its own bearer token
  (label:token), so audit events attribute calls to the host and a leaked
  token has minimal blast radius.
- RECEIPTS. Every gateway response carries ``gateway_meta`` with the
  gateway version and (when a folder context is resolvable) the audit
  event id, so the host workflow can store its own copy of the evidence
  (two ledgers, mutually referencing).
- LOOPBACK BY DEFAULT. Binding beyond 127.0.0.1 requires an explicit env
  override; starting without any token is refused.

Environment:

- ``WORKSPACES_GATEWAY_TOKEN``   — single bearer token (label "default").
- ``WORKSPACES_GATEWAY_TOKENS``  — per-host tokens, ``label:token[,label:token...]``
  (takes precedence; both may be combined).
- ``WORKSPACES_GATEWAY_HOST``    — bind host, default ``127.0.0.1``.
- ``WORKSPACES_GATEWAY_PORT``    — bind port, default ``8787``.
- ``WORKSPACE_FOLDER_CONTEXT``   — fallback folder for audit receipts when a
  call carries no ``folder_context`` param.

Requires ``mcp>=1.8`` (streamable-http server transport; suite verified
green on 1.27.2).
"""

from __future__ import annotations

import contextvars
import os
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

GATEWAY_VERSION = "0.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

gateway = FastMCP("workspace-gateway")
# FastMCP leaves the low-level server version unset; mcp >= 1.28 rejects a None
# server_version when the streamable-http gateway initializes. Declare it.
try:
    gateway._mcp_server.version = GATEWAY_VERSION
except AttributeError:
    pass

# Token label of the host tool serving the current request. Set by the
# auth middleware; "local" covers direct/in-process calls (tests, CLI).
_CURRENT_LABEL: contextvars.ContextVar[str] = contextvars.ContextVar(
    "workspace_gateway_label", default="local")

# ---------------------------------------------------------------------------
# Profile: facade -> allowed ops. Every entry names its ops (None = every op
# is deliberately unsupported): a new facade op stays stdio-only until someone
# adds it here and answers for its wire-safety. State-changing dials,
# seal/unseal, threshold writes, erasure (subject.forget) and source ingestion
# stay stdio-only.
# ---------------------------------------------------------------------------

ALLOWED_OPS: dict[str, set[str] | None] = {
    "workspace_lock": {"classify", "egress_check", "ingress_check",
                  "audit_query", "threshold_get"},
    "workspace_contract": {"review", "list_reviews", "request_approval",
                      "record_approval", "list_approvals"},
    "workspace_grounder": {"ground", "work.register", "claim.status",
                      "provenance.add", "provenance.trace", "swarm.frontier",
                      "bibliography", "coverage", "entities.link"},
    "workspace_audit": {"verify_chain", "get_event", "discipline"},
    "workspace_model": {"complete", "classify", "list"},
    "workspace_policy": {"snapshot"},
}

# Tools that must NEVER appear in the gateway profile (trifecta invariant —
# private-data readers and write surfaces). test_gateway.py asserts the
# registered profile is disjoint from this set.
FORBIDDEN_TOOLS = frozenset({
    "workspace_memory", "workspace_folder", "workspace_mirror", "workspace_dispatch",
    "workspace_ingest", "workspace_erase", "workspace_workspace", "workspace_workflow",
    "workspace_legal", "workspace_capture", "write_file_to_folder", "search",
    "by_id", "route_to_workspace", "recent_dispatches",
    # reason composes RAW private triple content (subjects/objects from
    # all_pairs, with provenance) and — with record=True — MUTATES the signed
    # log; it has no place on the detective/read-only egress gateway (N1). It
    # stays available locally via workspace_memory(op="reason"); workspace_memory is
    # itself forbidden over the gateway, so reason is fully off the wire.
    "reason",
})


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


def _audit_receipt(tool: str, op: str, params: dict[str, Any]) -> dict[str, Any]:
    """Append a gateway-call event to the folder's mutation log; return the
    receipt the host workflow can store. Best-effort: a logging failure
    degrades to a skipped receipt, never blocks the governed call itself."""
    folder = str(params.get("folder_context", "") or
                 os.environ.get("WORKSPACE_FOLDER_CONTEXT", ""))
    label = _CURRENT_LABEL.get()
    if not folder:
        return {"audit": "skipped", "reason": "no folder_context resolvable"}
    try:
        from .mutation_log import LogEvent, MutationLog
        log = MutationLog(folder)
        audit_id = log.append(LogEvent(
            event="system",
            folder_path=folder,
            pair_id=f"gateway-call:{uuid.uuid4()}",
            channel="system",
            actor=f"gateway:{label}",
            extra={"action": "gateway_call", "tool": tool, "op": op,
                   "gateway_version": GATEWAY_VERSION},
        ))
        return {"audit": "recorded", "audit_event_id": audit_id,
                "folder_context": folder}
    except Exception as exc:  # noqa: BLE001 — receipt must never break the call
        return {"audit": "skipped", "reason": f"{type(exc).__name__}: {exc}"}


def _envelope(result: dict[str, Any], tool: str, op: str,
              params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {"result": result}
    result.setdefault("gateway_meta", {})
    result["gateway_meta"].update({
        "gateway_version": GATEWAY_VERSION,
        "host_label": _CURRENT_LABEL.get(),
        **_audit_receipt(tool, op, params),
    })
    return result


def _dispatch(tool: str, op: str, params: dict[str, Any] | None) -> dict[str, Any]:
    p = params or {}
    allowed = ALLOWED_OPS[tool]
    if op in ("help", "ops", "catalogue"):
        from . import mcp_server
        full = getattr(mcp_server, tool)("help")
        if allowed is not None and isinstance(full, dict) and "ops" in full:
            full["ops"] = [o for o in full["ops"] if o.get("op") in allowed]
        return _envelope(full, tool, "help", p)
    if allowed is not None and op not in allowed:
        return {"error": f"op {op!r} not available over the gateway",
                "valid_ops": sorted(allowed),
                "note": "state-changing ops stay on the local stdio server"}
    from . import mcp_server
    result = getattr(mcp_server, tool)(op, p)
    return _envelope(result, tool, op, p)


# ---------------------------------------------------------------------------
# The seven tools
# ---------------------------------------------------------------------------


@gateway.tool()
def workspace_lock(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Shield gate (detective): classify | egress_check | ingress_check |
    audit_query | threshold_get. workspace_lock(op="help") lists params."""
    return _dispatch("workspace_lock", op, params)


@gateway.tool()
def workspace_contract(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Oversight gate: request_approval | record_approval | list_approvals |
    review | list_reviews. Poll list_approvals to resume a paused flow."""
    return _dispatch("workspace_contract", op, params)


@gateway.tool()
def workspace_grounder(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Grounder — no citation, no claim: ground | work.register |
    claim.status | provenance.add | provenance.trace | swarm.frontier |
    bibliography | coverage | entities.link."""
    return _dispatch("workspace_grounder", op, params)


@gateway.tool()
def workspace_audit(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Flight recorder: verify_chain | get_event | discipline."""
    return _dispatch("workspace_audit", op, params)


@gateway.tool()
def workspace_model(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Local model (no cloud): complete | classify | list."""
    return _dispatch("workspace_model", op, params)


@gateway.tool()
def workspace_policy(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Folder policy, read-only over the gateway: snapshot."""
    return _dispatch("workspace_policy", op, params)


# NB: there is deliberately NO gateway `reason` tool. reason() reads raw private
# pairs and (record=True) mutates the signed log — both disqualify it from the
# detective/read-only egress surface (N1). It is reachable locally only, via the
# MCP workspace_memory(op="reason") facade. reason is in FORBIDDEN_TOOLS so the
# trifecta test fails loudly if it is ever re-added to the profile.


@gateway.tool()
def server_info(request: dict[str, Any] | None = None) -> dict[str, Any]:
    """Gateway diagnostics: version, profile, upstream server identity."""
    if request is not None:
        return {"ok": False, "error": "server_info accepts no request parameters"}
    from . import mcp_server
    upstream = mcp_server.server_info()
    return {
        "server_name": "workspace-gateway",
        "gateway_version": GATEWAY_VERSION,
        "claim": "detective, not preventive — auditable governance for "
                 "flows that opt in",
        "profile": sorted([*ALLOWED_OPS, "server_info"]),
        "allowed_ops": {k: (sorted(v) if v else "all") for k, v in ALLOWED_OPS.items()},
        "upstream": {"server_name": upstream.get("server_name"),
                     "server_version": upstream.get("server_version")},
    }


# ---------------------------------------------------------------------------
# Auth (panel: per-host bearer tokens; refuse to start without any)
# ---------------------------------------------------------------------------


def parse_tokens(env: dict[str, str] | None = None) -> dict[str, str]:
    """Build token -> label table from WORKSPACES_GATEWAY_TOKENS / _TOKEN."""
    env = env if env is not None else dict(os.environ)
    table: dict[str, str] = {}
    multi = env.get("WORKSPACES_GATEWAY_TOKENS", "")
    for entry in filter(None, (e.strip() for e in multi.split(","))):
        label, sep, token = entry.partition(":")
        if sep and label.strip() and token.strip():
            table[token.strip()] = label.strip()
    single = env.get("WORKSPACES_GATEWAY_TOKEN", "").strip()
    if single:
        table.setdefault(single, "default")
    return table


def check_auth(authorization: str | None, table: dict[str, str]) -> str | None:
    """Return the host label for a valid ``Bearer <token>`` header, else None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return table.get(authorization[len("Bearer "):].strip())


class BearerAuthMiddleware:
    """ASGI middleware: reject unauthenticated requests, bind host label."""

    def __init__(self, app: Any, tokens: dict[str, str]):
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        label = check_auth(headers.get("authorization"), self.tokens)
        if label is None:
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body",
                        "body": b'{"error": "missing or invalid bearer token"}'})
            return
        token_ctx = _CURRENT_LABEL.set(label)
        try:
            await self.app(scope, receive, send)
        finally:
            _CURRENT_LABEL.reset(token_ctx)


def build_app() -> Any:
    """Auth-wrapped ASGI app. Raises RuntimeError if no token is configured."""
    tokens = parse_tokens()
    if not tokens:
        raise RuntimeError(
            "refusing to start: no gateway token configured. Set "
            "WORKSPACES_GATEWAY_TOKEN or WORKSPACES_GATEWAY_TOKENS=label:token[,...] "
            "— an unauthenticated gateway is not acceptable even on loopback.")
    return BearerAuthMiddleware(gateway.streamable_http_app(), tokens)


def main() -> None:
    """Run the gateway over streamable HTTP (loopback by default)."""
    import uvicorn
    host = os.environ.get("WORKSPACES_GATEWAY_HOST", DEFAULT_HOST)
    port = int(os.environ.get("WORKSPACES_GATEWAY_PORT", str(DEFAULT_PORT)))
    gateway.settings.host = host
    gateway.settings.port = port
    app = build_app()  # refuses without a token, before binding
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[workspace-gateway] WARNING: binding non-loopback host {host} — "
              "make sure this is intentional and the network is trusted.")
    print(f"[workspace-gateway] v{GATEWAY_VERSION} on http://{host}:{port} "
          f"(detective, not preventive)")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
