# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Host-injected dependencies — the outbound half of the lock boundary.

The lock never imports host modules for these needs. ``ensure_wired()`` pulls
the host's wiring module (``workspaces.lock_wiring``) once, which fills the
hooks; that import is the boundary's single declared outbound channel. When
the lock runs without its host (the extraction case), the import fails
silently, hooks stay ``None``, and every call site keeps its historical
fail-safe — degraded, never fail-open.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

models_for_role: Optional[Callable[[str], Sequence[str]]] = None
llm_classify: Optional[Callable[..., Any]] = None
key_root_dir: Optional[Callable[[], Any]] = None
record_decision: Optional[Callable[..., str]] = None
list_connectors: Optional[Callable[..., Any]] = None
l0_load_policy: Optional[Callable[..., Any]] = None
l0_capture_llm: Optional[Callable[..., Any]] = None
l0_capture_web: Optional[Callable[..., Any]] = None
list_models: Optional[Callable[[], Any]] = None
registry_models_for_role: Optional[Callable[[str], Any]] = None
capability_verifier_factory: Optional[Callable[[], Any]] = None
record_capability_refusal: Optional[Callable[..., Any]] = None
# Workspace-hierarchy policy composition for egress (decide_action via
# govern_egress) — injected so the lock never imports governance directly.
govern_egress: Optional[Callable[..., Any]] = None
# Web Bot Auth / RFC 9421 signature verification for per-request agent identity —
# injected so the lock never imports the verifier or the key registry directly.
# Absent (extraction case) → the proxy keeps the DECLARED identity, never fails open.
verify_agent_identity: Optional[Callable[..., Any]] = None
# Reporting sink for an audit write that failed -- injected so the lock never
# imports `audit_drop` directly. Absent (extraction case) -> the call site
# falls back to stderr, which is degraded (no durable marker for `doctor`)
# but never silent, and never fail-open.
record_audit_drop: Optional[Callable[..., Any]] = None

_wired = False


def ensure_wired() -> None:
    global _wired
    if _wired:
        return
    _wired = True
    try:
        from .. import lock_wiring  # noqa: F401 — fills the hooks above
    except Exception:
        pass
