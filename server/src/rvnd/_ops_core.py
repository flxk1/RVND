# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""In-tree ``rvnd-core`` provider (ADR-0004): dogfoods the same seam core and
plugins share. Each handler is a thin wrapper over the EXISTING in-engine
function, preserving today's arg defaults; notes/required/optional are the
verbatim help literal (transcribed from THIS build's own #138 help, not a prior
base). Lifecycle hooks (clientInfo capture, connect/disconnect presence) are
registered here but call the engine's own call sites — nothing moves in Phase 1.
Underscore-named so verify_surface's module roll-up skips it. Imports are lazy
(in-function), matching the facade, so loading this bundle at registry-assembly
never triggers a circular import with mcp_server."""
from __future__ import annotations

from .ops_seam import OpBundle, OpSpec


def _h_connected_agents(p, host):
    from .connected_agents import list_connected
    agents = list_connected()
    return {"ok": True, "count": len(agents), "agents": agents}


def _h_connected_agents_governance(p, host):
    from .governance_live import connected_agents_governance as _cag
    return _cag(p["folder_context"], log_root=host.log_root(),
                chain_limit=int(p.get("chain_limit", 10)))


def _h_session_governance(p, host):
    from .governance_live import session_governance as _sg
    return _sg(p["folder_context"], log_root=host.log_root(),
               chain_limit=int(p.get("chain_limit", 10)))


def _h_transport_audit(p, host):
    from .operations import transport_audit as _ta
    return _ta(p["folder_context"], log_root=host.log_root())


# --- lifecycle hooks: engine owns the call sites, core owns the impls ---

def _pre_capture_client_info(host):
    # clientInfo capture stays scoped to workspace_workflow (Phase 1); the engine
    # invokes this only at that facade's top, exactly as the direct call did.
    from .mcp_server import _capture_client_info
    _capture_client_info()


def _on_connect_presence(ctx, host):
    from .connected_agents import register_connection
    return register_connection(agent=ctx.agent, transport=ctx.transport,
                               session_id=ctx.session_id)


def _on_disconnect_presence(token, host):
    from .connected_agents import deregister_connection
    deregister_connection(token)


def bundle() -> OpBundle:
    return OpBundle(
        provider_id="rvnd-core",
        specs=[
            OpSpec("workspace_workflow", "connected_agents",
                   _h_connected_agents, required=[], mutates=False,
                   note="read-only, SERVER-LEVEL: agents that completed the MCP handshake with this server, independent of any workspace — who is CONNECTED (vs the per-workspace board's who is ADMITTED to act here). Presence, not authority; liveness is the connecting process. No folder."),
            OpSpec("workspace_workflow", "connected_agents_governance",
                   _h_connected_agents_governance,
                   required=["folder_context"], optional=["chain_limit"],
                   mutates=False, note="read-only join of SERVER-LEVEL presence (connected_agents) to this folder's REAL chain governance, per connection. The join actor is the host session_id when the connection carries one (the true per-session key), the agent name only as a fallback. Per connection: real connid/agent/session_id/transport/pid/connected_at, plus a governance object. attributed=true iff that join actor appears as an actor on the signed chain (>=1 event) — only then are verdict/grade/escalation (from lane_capabilities, strictest-wins) and the actor's chain tail (recent[], event_count, last_event_ts) returned, and join_key records which key matched (session_id|agent). Unattributed ⇒ honest-neutral (all nulls/empty); no fabricated or fail-closed verdict, and connid/pid never derive governance. Pure projection."),
            OpSpec("workspace_workflow", "session_governance",
                   _h_session_governance,
                   required=["folder_context"], optional=["chain_limit"],
                   mutates=False, note="read-only per-SESSION governance sourced from the SIGNED CHAIN (the real per-session identity: the actor the PreToolUse hook records). Returns sessions:[{actor, verdict, grade, escalation (REAL lane disposition via lane_capabilities strictest-wins; fail-closed 'refused' when the actor has no approved lane is a real disposition), event_count, last_event_ts, recent[] (the actor's own chain tail), connected/connid/pid (a live connection joined by session_id==actor, the host session id CLAUDE_CODE_SESSION_ID captured on connect; agent name only as a fallback), client{name,version,tier:'observed'} (the MCP clientInfo the connection handshook, captured lazily on first tool call — DESCRIPTIVE only, never a human name; tier 'observed' says RVND saw it at the transport handshake, NOT chain-proven; None where no connection or no clientInfo), and identity_tier:'witnessed' on the chain actor (the signed-chain identity). Provenance tier travels as a value-level property: witnessed=chain, observed=clientInfo — never fused}] plus connected_only:[idle presence that has not acted, each also carrying its client{name,version,tier:'observed'}]. The chain IS keyed by the per-session actor, so chain actors are the primary list; a live connection carrying the same session id surfaces as that actor's real presence. Pure projection; no fabrication."),
            OpSpec("workspace_workflow", "transport_audit",
                   _h_transport_audit, required=["folder_context"],
                   note="the transport/clock primitive: read-only audit that every run originated from one external trigger (nothing self-starts)"),
        ],
        pre_op=(_pre_capture_client_info,),
        on_connect=(_on_connect_presence,),
        on_disconnect=(_on_disconnect_presence,),
    )
