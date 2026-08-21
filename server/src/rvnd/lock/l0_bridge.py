# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Bridge between Privacy Lock and Workspace L0 memory — dual transport.

Two transports are supported:

1. **In-process Python import** (fast path) — when ``workspace-l0-memory`` is
   installed in the same Python interpreter as ``agent-tool-lock``. Direct
   function calls; sub-millisecond.

2. **MCP stdio subprocess** (cross-process path) — when ``workspace-l0-memory``
   runs as a separate MCP server. The bridge spawns ``workspace-l0-mcp``, does
   the JSON-RPC round-trip, returns the result. ~100–300 ms per call.

Transport selection
-------------------

The selector consults, in order:

1. ``AGENT_TOOL_LOCK_L0_TRANSPORT`` env var: ``"mcp"`` / ``"inprocess"`` /
   ``"auto"`` (default).
2. In ``"auto"`` mode:

   - If ``AGENT_TOOL_LOCK_L0_MCP_CMD`` is set, use MCP.
   - Else if ``workspaces`` is importable, use in-process.
   - Else no-op.

3. If the configured transport fails at runtime, fall through to the next.

The public functions (:func:`try_load_policy`, :func:`try_capture_llm`,
:func:`try_capture_web`) preserve the same signatures and return shapes as
before — callers in :mod:`.gate` and :mod:`.gate_and_capture` don't need
to change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Optional-import availability check (in-process transport)
# ---------------------------------------------------------------------------


_L0_AVAILABLE: bool | None = None


def is_l0_available() -> bool:
    """Return True if the host wired the in-process L0 hooks. Cached after
    first call.

    This reflects only the **in-process** transport. The MCP transport is
    checked separately via :func:`rvnd.lock.l0_mcp_client.mcp_is_available`.
    """
    global _L0_AVAILABLE
    if _L0_AVAILABLE is not None:
        return _L0_AVAILABLE
    try:
        from . import host_deps
        host_deps.ensure_wired()
        _L0_AVAILABLE = host_deps.l0_load_policy is not None
    except Exception:
        _L0_AVAILABLE = False
    return _L0_AVAILABLE


def _set_l0_available(value: bool | None) -> None:
    global _L0_AVAILABLE
    _L0_AVAILABLE = value


# ---------------------------------------------------------------------------
# Transport selection
# ---------------------------------------------------------------------------


Transport = Literal["mcp", "inprocess", "noop"]


def _select_transport() -> Transport:
    """Decide which L0 transport this call should use."""
    forced = (os.environ.get("AGENT_TOOL_LOCK_L0_TRANSPORT") or "auto").lower().strip()
    if forced == "mcp":
        return "mcp"
    if forced == "inprocess":
        return "inprocess" if is_l0_available() else "noop"
    if forced == "noop":
        return "noop"

    # Auto: prefer MCP if a command is configured (cross-process deployment),
    # else fall back to in-process when both plugins are in the same venv.
    if os.environ.get("AGENT_TOOL_LOCK_L0_MCP_CMD"):
        return "mcp"
    if is_l0_available():
        return "inprocess"
    return "noop"


# ---------------------------------------------------------------------------
# Policy read — used by gate_for_cloud
# ---------------------------------------------------------------------------


@dataclass
class PolicySnapshot:
    """A compact snapshot of the folder policy for the gate's decision path."""

    lock_is_active: bool
    oversight_is_active: bool
    oversight_default_level: str = "approve"
    source: str = "default"
    """``"default"`` | ``"policy_file"`` | ``"policy_file_via_mcp"`` |
    ``"l0_not_installed"``"""
    # Tier-M moderation rules from the folder policy (None = none declared). Carried
    # here so the gate passes them to lock_text without a second policy read.
    # Populated by BOTH transports (in-process and the cross-process MCP policy
    # snapshot); only the fail-safe default leaves it None — and there lock+oversight
    # already force full protection, so an absent moderation layer is not a widening.
    moderation_rules: dict | None = None


def _safe_default_snapshot(reason: str) -> PolicySnapshot:
    """Fail-safe: any error or missing transport → full protection."""
    return PolicySnapshot(
        lock_is_active=True,
        oversight_is_active=True,
        source=reason,
    )


def try_load_policy(folder_context: str | Path | None) -> PolicySnapshot:
    """Best-effort policy read across either transport.

    Returns a snapshot with sensible defaults when:

    - ``folder_context`` is None.
    - The selected transport is unavailable.
    - The policy file is corrupt or unreadable.

    Default snapshot: ``lock_is_active=True``, ``oversight_is_active=True``.
    Safe-on-failure direction.
    """
    if folder_context is None:
        return _safe_default_snapshot("default")

    transport = _select_transport()
    if transport == "noop":
        return _safe_default_snapshot("l0_not_installed")

    if transport == "mcp":
        return _load_policy_via_mcp(folder_context)

    return _load_policy_inprocess(folder_context)


def _load_policy_inprocess(folder_context: str | Path) -> PolicySnapshot:
    try:
        from . import host_deps
        host_deps.ensure_wired()
        if host_deps.l0_load_policy is None:
            return _safe_default_snapshot("default")
        policy = host_deps.l0_load_policy(folder_context)
        return PolicySnapshot(
            lock_is_active=policy["lock_is_active"],
            oversight_is_active=policy["oversight_is_active"],
            oversight_default_level=policy["oversight_default_level"],
            moderation_rules=policy.get("moderation_rules"),
            source="policy_file",
        )
    except Exception:
        return _safe_default_snapshot("default")


def _load_policy_via_mcp(folder_context: str | Path) -> PolicySnapshot:
    try:
        from .l0_mcp_client import mcp_try_load_policy
        result = mcp_try_load_policy(folder_context)
    except Exception:
        return _safe_default_snapshot("default")
    if not result.success:
        # MCP unreachable / timed out: fall back to in-process if installed,
        # else default-safe.
        if is_l0_available():
            return _load_policy_inprocess(folder_context)
        return _safe_default_snapshot("default")
    p = result.payload or {}
    mod = p.get("moderation_rules")
    return PolicySnapshot(
        lock_is_active=bool(p.get("lock_is_active", True)),
        oversight_is_active=bool(p.get("oversight_is_active", True)),
        oversight_default_level=str(p.get("oversight_default_level", "approve")),
        moderation_rules=mod if isinstance(mod, dict) else None,
        source="policy_file_via_mcp",
    )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


@dataclass
class BridgeCaptureResult:
    """What the bridge reports back to the caller."""

    attempted: bool
    captured: bool
    pair_id: str | None = None
    verbosity: str = ""
    audit_id: str = ""
    skipped_reason: str = ""
    transport: str = ""
    """``"inprocess"`` / ``"mcp"`` / ``""`` (no-op)."""


def try_capture_llm(
    *,
    folder_context: str | Path | None,
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
) -> BridgeCaptureResult:
    """Capture an LLM exchange via the configured L0 transport.

    Falls back to no-op when no transport is reachable or ``folder_context``
    is None.
    """
    if folder_context is None:
        return BridgeCaptureResult(
            attempted=False,
            captured=False,
            skipped_reason="no_folder_context",
            transport="",
        )

    transport = _select_transport()
    if transport == "noop":
        return BridgeCaptureResult(
            attempted=False,
            captured=False,
            skipped_reason="l0_unavailable",
            transport="",
        )

    if transport == "mcp":
        result = _capture_llm_via_mcp(
            folder_context=folder_context,
            model=model,
            prompt_context=prompt_context,
            response=response,
            cited_sources=cited_sources,
            cost_estimate_cents=cost_estimate_cents,
            tool_call_trace=tool_call_trace,
            request_id=request_id,
            oversight_level=oversight_level,
            mode=mode,
            actor=actor,
        )
        # Fall back to in-process if MCP couldn't even be reached.
        if not result.attempted and is_l0_available():
            return _capture_llm_inprocess(
                folder_context=folder_context,
                model=model,
                prompt_context=prompt_context,
                response=response,
                cited_sources=cited_sources,
                cost_estimate_cents=cost_estimate_cents,
                tool_call_trace=tool_call_trace,
                request_id=request_id,
                oversight_level=oversight_level,
                mode=mode,
                actor=actor,
            )
        return result

    return _capture_llm_inprocess(
        folder_context=folder_context,
        model=model,
        prompt_context=prompt_context,
        response=response,
        cited_sources=cited_sources,
        cost_estimate_cents=cost_estimate_cents,
        tool_call_trace=tool_call_trace,
        request_id=request_id,
        oversight_level=oversight_level,
        mode=mode,
        actor=actor,
    )


def _capture_llm_inprocess(
    *,
    folder_context: str | Path,
    model: str,
    prompt_context: str,
    response: str,
    cited_sources: list[str] | None,
    cost_estimate_cents: float | None,
    tool_call_trace: list[dict[str, Any]] | None,
    request_id: str,
    oversight_level: str,
    mode: str,
    actor: str,
) -> BridgeCaptureResult:
    from . import host_deps
    host_deps.ensure_wired()
    if host_deps.l0_capture_llm is None:
        return BridgeCaptureResult(
            attempted=False,
            captured=False,
            skipped_reason="l0_import_failed",
            transport="",
        )

    try:
        result = host_deps.l0_capture_llm(
            folder_context=folder_context,
            model=model,
            prompt_context=prompt_context,
            response=response,
            cited_sources=cited_sources,
            cost_estimate_cents=cost_estimate_cents,
            tool_call_trace=tool_call_trace,
            request_id=request_id,
            oversight_level=oversight_level,
            mode=mode,
            actor=actor,
        )
    except Exception as e:
        return BridgeCaptureResult(
            attempted=True,
            captured=False,
            skipped_reason=f"capture_raised:{e}",
            transport="inprocess",
        )

    return BridgeCaptureResult(
        attempted=True,
        captured=result["captured"],
        pair_id=result["pair_id"],
        verbosity=result["verbosity"],
        audit_id=result["audit_id"],
        skipped_reason=result["skipped_reason"],
        transport="inprocess",
    )


def _capture_llm_via_mcp(
    *,
    folder_context: str | Path,
    model: str,
    prompt_context: str,
    response: str,
    cited_sources: list[str] | None,
    cost_estimate_cents: float | None,
    tool_call_trace: list[dict[str, Any]] | None,
    request_id: str,
    oversight_level: str,
    mode: str,
    actor: str,
) -> BridgeCaptureResult:
    try:
        from .l0_mcp_client import mcp_try_capture_llm
    except ImportError:
        return BridgeCaptureResult(
            attempted=False,
            captured=False,
            skipped_reason="mcp_client_unavailable",
            transport="",
        )

    result = mcp_try_capture_llm(
        folder_context=folder_context,
        model=model,
        prompt_context=prompt_context,
        response=response,
        cited_sources=cited_sources,
        cost_estimate_cents=cost_estimate_cents,
        tool_call_trace=tool_call_trace,
        request_id=request_id,
        oversight_level=oversight_level,
        mode=mode,
        actor=actor,
    )
    if not result.success:
        return BridgeCaptureResult(
            attempted=False,
            captured=False,
            skipped_reason=f"mcp_call_failed:{result.error}",
            transport="",
        )
    p = result.payload or {}
    return BridgeCaptureResult(
        attempted=True,
        captured=bool(p.get("captured", False)),
        pair_id=p.get("pair_id"),
        verbosity=str(p.get("verbosity", "")),
        audit_id=str(p.get("audit_id", "")),
        skipped_reason=str(p.get("skipped_reason", "")),
        transport="mcp",
    )


def try_capture_web(
    *,
    folder_context: str | Path | None,
    query: str,
    engine: str,
    results: list[dict[str, Any]],
    cost_estimate_cents: float | None = None,
    request_id: str = "",
    oversight_level: str = "approve",
    mode: str = "agentic",
    actor: str = "agent:lock",
) -> BridgeCaptureResult:
    """Capture a web-search exchange via the configured L0 transport."""
    if folder_context is None:
        return BridgeCaptureResult(
            attempted=False,
            captured=False,
            skipped_reason="no_folder_context",
            transport="",
        )

    transport = _select_transport()
    if transport == "noop":
        return BridgeCaptureResult(
            attempted=False,
            captured=False,
            skipped_reason="l0_unavailable",
            transport="",
        )

    if transport == "mcp":
        result = _capture_web_via_mcp(
            folder_context=folder_context,
            query=query,
            engine=engine,
            results=results,
            cost_estimate_cents=cost_estimate_cents,
            request_id=request_id,
            oversight_level=oversight_level,
            mode=mode,
            actor=actor,
        )
        if not result.attempted and is_l0_available():
            return _capture_web_inprocess(
                folder_context=folder_context,
                query=query,
                engine=engine,
                results=results,
                cost_estimate_cents=cost_estimate_cents,
                request_id=request_id,
                oversight_level=oversight_level,
                mode=mode,
                actor=actor,
            )
        return result

    return _capture_web_inprocess(
        folder_context=folder_context,
        query=query,
        engine=engine,
        results=results,
        cost_estimate_cents=cost_estimate_cents,
        request_id=request_id,
        oversight_level=oversight_level,
        mode=mode,
        actor=actor,
    )


def _capture_web_inprocess(
    *,
    folder_context: str | Path,
    query: str,
    engine: str,
    results: list[dict[str, Any]],
    cost_estimate_cents: float | None,
    request_id: str,
    oversight_level: str,
    mode: str,
    actor: str,
) -> BridgeCaptureResult:
    from . import host_deps
    host_deps.ensure_wired()
    if host_deps.l0_capture_web is None:
        return BridgeCaptureResult(
            attempted=False,
            captured=False,
            skipped_reason="l0_import_failed",
            transport="",
        )

    try:
        result = host_deps.l0_capture_web(
            folder_context=folder_context,
            query=query,
            engine=engine,
            results=results,
            cost_estimate_cents=cost_estimate_cents,
            request_id=request_id,
            oversight_level=oversight_level,
            mode=mode,
            actor=actor,
        )
    except Exception as e:
        return BridgeCaptureResult(
            attempted=True,
            captured=False,
            skipped_reason=f"capture_raised:{e}",
            transport="inprocess",
        )

    return BridgeCaptureResult(
        attempted=True,
        captured=result["captured"],
        pair_id=result["pair_id"],
        verbosity=result["verbosity"],
        audit_id=result["audit_id"],
        skipped_reason=result["skipped_reason"],
        transport="inprocess",
    )


def _capture_web_via_mcp(
    *,
    folder_context: str | Path,
    query: str,
    engine: str,
    results: list[dict[str, Any]],
    cost_estimate_cents: float | None,
    request_id: str,
    oversight_level: str,
    mode: str,
    actor: str,
) -> BridgeCaptureResult:
    try:
        from .l0_mcp_client import mcp_try_capture_web
    except ImportError:
        return BridgeCaptureResult(
            attempted=False,
            captured=False,
            skipped_reason="mcp_client_unavailable",
            transport="",
        )

    result = mcp_try_capture_web(
        folder_context=folder_context,
        query=query,
        engine=engine,
        results=results,
        cost_estimate_cents=cost_estimate_cents,
        request_id=request_id,
        oversight_level=oversight_level,
        mode=mode,
        actor=actor,
    )
    if not result.success:
        return BridgeCaptureResult(
            attempted=False,
            captured=False,
            skipped_reason=f"mcp_call_failed:{result.error}",
            transport="",
        )
    p = result.payload or {}
    return BridgeCaptureResult(
        attempted=True,
        captured=bool(p.get("captured", False)),
        pair_id=p.get("pair_id"),
        verbosity=str(p.get("verbosity", "")),
        audit_id=str(p.get("audit_id", "")),
        skipped_reason=str(p.get("skipped_reason", "")),
        transport="mcp",
    )
