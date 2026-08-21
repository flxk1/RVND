# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Host-side wiring for the lock's injected dependencies.

Imported once by ``lock.host_deps.ensure_wired()``; assigning the hooks here
is what turns the lock's outbound needs into injected dependencies. Every
hook body imports its host module at call time, so tests that monkey-patch
host symbols (e.g. ``rvnd.local_llm.resolve_models_for_role``) keep
working exactly as they did against the old lazy imports.
"""
from __future__ import annotations

from pathlib import Path

from .lock import host_deps
from .policy import effective_policy


def _models_for_role(role):
    from .local_llm import resolve_models_for_role
    return resolve_models_for_role(role)


def _llm_classify(**kwargs):
    from .mcp_server import local_llm_classify
    return local_llm_classify(**kwargs)


def _record_audit_drop(where, exc, **context):
    from .audit_drop import record
    return record(where, exc, **context)


def _key_root_dir():
    from .signing import _key_root_dir
    return _key_root_dir()


def _record_decision(folder_context, decision, *, model="", mode=None,
                     request_id="", actor="agent:lock", log_root=None):
    from . import verdict as _vd
    from .mutation_log import LogEvent, MutationLog
    action = str(getattr(decision, "action", "") or "")
    canon = _vd.from_lock(action).value
    log = MutationLog(Path(folder_context),
                      log_root=Path(log_root) if log_root else None)
    return log.append(LogEvent(
        event="system", folder_path=str(folder_context),
        pair_id=f"lock:{request_id or action}", channel="system", actor=actor,
        extra={"kind": "lock-decision", "action": action, "verdict": canon,
               "mode": getattr(mode, "name", str(mode)) if mode is not None else "",
               "model": model,
               "reason": str(getattr(decision, "reason", "") or ""),
               # CL2: record a lock-OFF bypass on the signed chain too, with
               # what the gate WOULD have done — so the chain shows the
               # protection that was bypassed, not just the permissive outcome.
               "lock_bypassed": bool(getattr(decision, "lock_bypassed", False)),
               "would_have": str(getattr(decision, "would_have", "") or "")}))


def _list_connectors(folder_context, log_root=None):
    from .connectors import list_connectors
    return list_connectors(folder_context, log_root=log_root)


def _l0_load_policy(folder_context):
    from . import load_policy
    p = effective_policy(folder_context)
    return {"lock_is_active": p.lock_is_active,
            "oversight_is_active": p.oversight_is_active,
            "oversight_default_level": p.oversight_default_level,
            "moderation_rules": getattr(p, "moderation_rules", None)}


def _capture_result_dict(result):
    return {"captured": result.captured, "pair_id": result.pair_id,
            "verbosity": (result.verbosity.value
                          if hasattr(result.verbosity, "value")
                          else str(result.verbosity)),
            "audit_id": result.audit_id,
            "skipped_reason": result.skipped_reason}


def _l0_capture_llm(*, folder_context, model, prompt_context, response,
                    cited_sources, cost_estimate_cents, tool_call_trace,
                    request_id, oversight_level, mode, actor):
    from . import IngestMode, LLMExchange, capture_llm_exchange
    mode_enum = (IngestMode.AGENTIC if str(mode).lower() == "agentic"
                 else IngestMode.INTERACTIVE)
    result = capture_llm_exchange(
        LLMExchange(model=model, prompt_context=prompt_context,
                    response=response, cited_sources=list(cited_sources or []),
                    cost_estimate_cents=cost_estimate_cents,
                    tool_call_trace=list(tool_call_trace or []),
                    request_id=request_id),
        mode=mode_enum, oversight=oversight_level,
        folder_context=folder_context, actor=actor)
    return _capture_result_dict(result)


def _l0_capture_web(*, folder_context, query, engine, results,
                    cost_estimate_cents, request_id, oversight_level,
                    mode, actor):
    from . import (IngestMode, WebSearchExchange, WebSearchResult,
                   capture_web_search)
    mode_enum = (IngestMode.AGENTIC if str(mode).lower() == "agentic"
                 else IngestMode.INTERACTIVE)
    web_results = [WebSearchResult(url=str(r.get("url", "")),
                                   title=str(r.get("title", "")),
                                   snippet=str(r.get("snippet", "")),
                                   full_text=str(r.get("full_text", "")),
                                   rank=int(r.get("rank", 0)))
                   for r in results]
    result = capture_web_search(
        WebSearchExchange(query=query, engine=engine, results=web_results,
                          cost_estimate_cents=cost_estimate_cents,
                          request_id=request_id),
        mode=mode_enum, oversight=oversight_level,
        folder_context=folder_context, actor=actor)
    return _capture_result_dict(result)


def _list_models():
    from . import models_registry
    return models_registry.list_models()


def _registry_models_for_role(role):
    from . import models_registry
    return models_registry.models_for_role(role)


def _capability_verifier_factory():
    from .session_capability import CapabilityVerifier
    return CapabilityVerifier.from_key_dir()


def _record_capability_refusal(folder_context, *, reason, path, log_root=None):
    """Write refusal evidence outside the lock package's import boundary."""
    from .mutation_log import LogEvent, MutationLog
    folder = Path(folder_context).expanduser().resolve()
    return MutationLog(
        folder,
        log_root=Path(log_root) if log_root else None,
    ).append(LogEvent(
        event="system",
        folder_path=str(folder),
        pair_id="incident:oversight-bypassed",
        channel="system",
        actor="egress-proxy",
        extra={
            "kind": "Incident",
            "incident_type": "oversight-bypassed",
            "reason": reason,
            "path": path,
        },
    ))


def _govern_egress(folder, **kwargs):
    # Lazy import at call time (like every hook here) so the lock's outbound
    # channel stays a single wiring edge, not a static lock→governance import.
    from .governance import govern_egress
    return govern_egress(folder, **kwargs)


def _verify_agent_identity(headers, *, authority="", method="", path="",
                           expected_agent=None, now=None):
    # Cryptographically verify a per-request agent identity (Web Bot Auth /
    # RFC 9421). Lazy import so lock→web_bot_auth stays a wiring edge. Returns a
    # plain dict so the lock never handles the verifier's own types.
    from .web_bot_auth import RequestContext, verify
    hlow = {str(k).lower(): v for k, v in dict(headers).items()}
    ctx = RequestContext(authority=authority, method=method, path=path,
                         headers=hlow)
    v = verify(hlow, ctx=ctx, expected_agent=expected_agent, now=now)
    return {"verified": v.verified, "agent": v.agent,
            "keyid": v.keyid, "reason": v.reason}


host_deps.models_for_role = _models_for_role
host_deps.llm_classify = _llm_classify
host_deps.key_root_dir = _key_root_dir
host_deps.record_audit_drop = _record_audit_drop
host_deps.record_decision = _record_decision
host_deps.list_connectors = _list_connectors
host_deps.l0_load_policy = _l0_load_policy
host_deps.l0_capture_llm = _l0_capture_llm
host_deps.l0_capture_web = _l0_capture_web
host_deps.list_models = _list_models
host_deps.registry_models_for_role = _registry_models_for_role
host_deps.capability_verifier_factory = _capability_verifier_factory
host_deps.record_capability_refusal = _record_capability_refusal
host_deps.govern_egress = _govern_egress
host_deps.verify_agent_identity = _verify_agent_identity
