# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""``gate_and_capture_llm`` + ``gate_and_capture_web`` — one-call wrappers.

The shipped pattern: every cloud-LLM call from an agent or skill goes through
``gate_for_cloud`` (Privacy Lock) and is also captured into L0 memory for the
agentic audit floor. Likewise every websearch.

Without these wrappers, callers have to remember to do both. The wrappers make
the right thing the default thing.

If ``workspace-l0-memory`` isn't installed, capture is silently skipped and the
GateDecision is returned alone. Privacy Lock works without L0; the user just
doesn't get the audit memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import AuditLog, Mode
from .decisions import DecisionsStore
from .gate import GateDecision, gate_for_cloud
from .l0_bridge import BridgeCaptureResult, try_capture_llm, try_capture_web
from .oversight import OversightLevel


# ---------------------------------------------------------------------------
# Combined result
# ---------------------------------------------------------------------------


@dataclass
class GateAndCaptureResult:
    """The bundled outcome: the gate's decision + L0's capture outcome."""

    gate: GateDecision
    capture: BridgeCaptureResult


def record_lock_decision_to_chain(
    folder_context, decision, *, model: str = "", mode=None,
    request_id: str = "", actor: str = "agent:lock", log_root=None) -> str:
    """Write the lock egress decision onto the folder's SIGNED mutation chain,
    in the canonical tri-state vocabulary (so a lock refusal is auditable in the
    same words as a gate/grounder decision). Guarded: a logging failure never
    breaks the egress path. Returns the audit_id, or "" on failure."""
    try:
        from . import host_deps
        host_deps.ensure_wired()
        if host_deps.record_decision is None:
            return ""
        return host_deps.record_decision(
            folder_context, decision, model=model, mode=mode,
            request_id=request_id, actor=actor, log_root=log_root)
    except Exception:                               # noqa: BLE001 — never break egress
        return ""


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


def gate_and_capture_llm(
    *,
    prompt: str,
    response: str,
    model: str,
    folder_context: str | Path | None = None,
    vault_path: str | Path | None = None,
    oversight: OversightLevel = OversightLevel.APPROVE,
    mode: Mode = Mode.STANDARD,
    decisions: DecisionsStore | None = None,
    audit: AuditLog | None = None,
    cited_sources: list[str] | None = None,
    cost_estimate_cents: float | None = None,
    tool_call_trace: list[dict[str, Any]] | None = None,
    request_id: str = "",
    capture_mode: str = "agentic",
    capture_actor: str = "agent:lock",
    task_id: str | None = None,
) -> GateAndCaptureResult:
    """Run Privacy Lock on the OUTGOING prompt, then capture the exchange to L0.

    Workflow:

    1. ``gate_for_cloud(prompt, ...)`` runs first. If the gate refuses or
       asks-user, capture is still attempted (audit floor stays for agentic).
    2. If workspace-l0-memory is installed AND ``folder_context`` is set, the
       exchange is captured at the verbosity dictated by ``(mode × oversight)``
       per :func:`rvnd.decide_verbosity`.

    Returns both the gate decision (callers may need to forward / refuse the
    upstream call based on it) and the capture outcome (for audit / debugging).

    Callers MUST inspect ``result.gate.action`` and handle ``ask_user`` — do not
    treat a non-``refuse`` result as an automatic forward. In particular, when a
    folder has Privacy Lock disabled, a would-be refuse at APPROVE+ oversight now
    returns ``ask_user`` with ``gate.lock_bypassed=True`` (route it to a person),
    and at lower oversight it returns ``allow`` with ``lock_bypassed=True`` (the
    bypass is audited) — never a silent auto-allow (CL2).
    """
    gate_decision = gate_for_cloud(
        prompt,
        vault_path=vault_path,
        oversight=oversight,
        mode=mode,
        decisions=decisions,
        audit=audit,
        source="cloud_llm_request",
        task_id=task_id,
        folder_context=folder_context,
    )
    if folder_context:
        record_lock_decision_to_chain(folder_context, gate_decision, model=model,
                                      mode=mode, request_id=request_id or task_id or "")

    # Capture is attempted regardless of gate decision — the agentic audit
    # floor requires recording even refused calls. Verbosity scales naturally
    # via the oversight level.
    capture_result = try_capture_llm(
        folder_context=folder_context,
        model=model,
        prompt_context=prompt,
        response=response,
        cited_sources=cited_sources,
        cost_estimate_cents=cost_estimate_cents,
        tool_call_trace=tool_call_trace,
        request_id=request_id or task_id or "",
        oversight_level=oversight.name.lower() if hasattr(oversight, "name") else str(oversight),
        mode=capture_mode,
        actor=capture_actor,
    )

    return GateAndCaptureResult(gate=gate_decision, capture=capture_result)


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


def gate_and_capture_web(
    *,
    query: str,
    engine: str,
    results: list[dict[str, Any]],
    folder_context: str | Path | None = None,
    vault_path: str | Path | None = None,
    oversight: OversightLevel = OversightLevel.APPROVE,
    mode: Mode = Mode.STANDARD,
    decisions: DecisionsStore | None = None,
    audit: AuditLog | None = None,
    cost_estimate_cents: float | None = None,
    request_id: str = "",
    capture_mode: str = "agentic",
    capture_actor: str = "agent:lock",
    task_id: str | None = None,
) -> GateAndCaptureResult:
    """Same shape as :func:`gate_and_capture_llm`, but for web-search exchanges.

    The gate is run on the QUERY STRING (which can leak intent or contain
    confidential terms — e.g. "GDPR breach notification for ACME 2026" leaks
    the client name). Privacy Lock treats the query as the outgoing text;
    if it refuses, the websearch should not have happened — though the caller
    is responsible for honouring that on the wire.

    After the gate, the exchange is captured into L0 with web-specific
    verbosity (URLs vs snippets vs full content per oversight level).
    """
    gate_decision = gate_for_cloud(
        query,
        vault_path=vault_path,
        oversight=oversight,
        mode=mode,
        decisions=decisions,
        audit=audit,
        source="websearch_query",
        task_id=task_id,
        folder_context=folder_context,
    )
    if folder_context:
        record_lock_decision_to_chain(folder_context, gate_decision, model=engine,
                                      mode=mode, request_id=request_id or task_id or "")

    capture_result = try_capture_web(
        folder_context=folder_context,
        query=query,
        engine=engine,
        results=results,
        cost_estimate_cents=cost_estimate_cents,
        request_id=request_id or task_id or "",
        oversight_level=oversight.name.lower() if hasattr(oversight, "name") else str(oversight),
        mode=capture_mode,
        actor=capture_actor,
    )

    return GateAndCaptureResult(gate=gate_decision, capture=capture_result)
