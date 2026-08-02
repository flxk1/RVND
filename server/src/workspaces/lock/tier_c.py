# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tier C semantic PII check — backend-agnostic dispatcher.

Replaces the older tier_c_ollama module. Uses the backend factory to pick a
local LLM implementation. Default backend = "mock" so tests + onboarding
work without any model installed.

Configuration via env vars (read at backend-construction time):
- AGENT_TOOL_LOCK_LLM_BACKEND — spec string for make_local_llm()
                                  e.g. "llama_cpp:/path/to/model.gguf"
                                  e.g. "ollama:llama3.2:3b"
                                  e.g. "mock"
                                  Default: "mock"
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from .backends import LLMBackend, make_local_llm, BackendError
from .core import Finding


_DEFAULT_SPEC = "mock"
_AUTO = "auto"


def _registry_gguf_for_lock() -> str:
    """Resolve a real local GGUF for Tier C from the workspace models registry, as a
    ``llama_cpp:<path>`` spec. Prefers a model registered for the lock role;
    else the smallest registered GGUF (cheapest for a per-string check). Returns
    "" when the runtime/registry is absent or no GGUF is on disk."""
    try:
        from . import host_deps
        host_deps.ensure_wired()
        if host_deps.list_models is None:
            return ""
    except Exception:
        return ""

    def _path_for(mid: str) -> str:
        for e in host_deps.list_models():
            if e.id == mid and e.artifact_path:
                p = Path(e.artifact_path).expanduser()
                if p.suffix == ".gguf" and p.exists():
                    return str(p)
        return ""

    try:
        for role in ("lock-c", "lock-tier-C"):
            for mid in host_deps.registry_models_for_role(role):
                p = _path_for(mid)
                if p:
                    return f"llama_cpp:{p}"
        found: list[tuple[int, str]] = []
        for e in host_deps.list_models():
            if e.artifact_path:
                p = Path(e.artifact_path).expanduser()
                if p.suffix == ".gguf" and p.exists():
                    found.append((p.stat().st_size, str(p)))
        if found:
            found.sort(key=lambda t: t[0])
            return f"llama_cpp:{found[0][1]}"
    except Exception:
        return ""
    return ""


def _resolve_spec() -> str:
    """The backend spec Tier C should use. ``AGENT_TOOL_LOCK_LLM_BACKEND``:
    unset -> "mock" (no model loaded; fast, the safe default for per-ingest
    scanning); an explicit ``llama_cpp:<path>`` / ``onnx_genai:<dir>`` /
    ``auto`` resolves a real local GGUF from
    the workspace models registry (so the pulled model serves Tier C too) and falls
    back to "mock" if none is installed."""
    spec = (os.environ.get("AGENT_TOOL_LOCK_LLM_BACKEND", _DEFAULT_SPEC).strip()
            or _DEFAULT_SPEC)
    if spec != _AUTO:
        return spec
    return _registry_gguf_for_lock() or _DEFAULT_SPEC


# Module-level cache. One backend instance per spec string per process.
_backend_cache: dict[str, LLMBackend] = {}


def tier_c_requires_real_backend() -> bool:
    """True when a REAL semantic backend is the effective configuration — i.e.
    the resolved spec is not ``mock``. When this is True, an unavailable/erroring
    backend must FAIL CLOSED (D8): a would-be refuse must not silently degrade to
    regex-only/allow. When False (mock is the effective backend — the documented
    onboarding/test default, or ``auto`` with no model installed), Tier C is
    permissive: ``mock`` is the explicit opt-in to "no semantic check"."""
    return _resolve_spec() != _DEFAULT_SPEC


def _build_backend(spec: str) -> LLMBackend:
    """Construct (and cache) the backend for ``spec``. Raises BackendError on a
    construction failure — does NOT silently swap to mock (that swap is what made
    a configured-but-broken backend fail OPEN, D8)."""
    cached = _backend_cache.get(spec)
    if cached is not None:
        return cached
    backend = make_local_llm(spec)          # may raise BackendError
    _backend_cache[spec] = backend
    return backend


def _get_backend() -> LLMBackend:
    """A USABLE backend for diagnostics/health (``describe``/``is_available``):
    falls back to mock if the configured backend won't construct, so those probes
    never raise. The *enforcement* path (``tier_c_check_semantic``) does NOT use
    this fallback — it fails closed instead (see ``tier_c_requires_real_backend``)."""
    spec = _resolve_spec()
    try:
        return _build_backend(spec)
    except BackendError:
        return _build_backend("mock")


def tier_c_unavailable_finding(detail: str) -> Finding:
    """A high-severity, fail-closed Finding signalling that the configured Tier-C
    backend could not run. High severity → the decision layer refuses (it is
    treated like a confirmed sensitive finding), so an unavailable semantic check
    blocks rather than silently allowing (D8)."""
    return Finding(
        tier="C",
        type="tier_c_unavailable",
        severity="high",
        field=None,
        detail=f"Tier-C semantic check could not run — failing closed: {detail}",
        confidence=1.0,
    )


def reset_backend_cache() -> None:
    """Clear the cached backend. Used by tests + onboarding wizard."""
    _backend_cache.clear()


def tier_c_spec() -> str:
    """The resolved backend spec (after 'auto' resolution). Diagnostics."""
    return _resolve_spec()


def is_tier_c_available() -> bool:
    """Returns True if the configured Tier C backend is ready to run."""
    return _get_backend().is_available()


def describe_tier_c() -> str:
    """Human-readable description of the active Tier C backend."""
    return _get_backend().describe()


def tier_c_check_semantic(text: str, context: str = "") -> List[Finding]:
    """Tier C semantic check. Returns Findings for likely PII or confidential
    content that the regex layer missed.

    Args:
        text: the text to classify.
        context: optional confidential-terms string (e.g. newline-separated KG
            entities, project names, client refs). When the configured backend
            is the production llama_cpp path, this is interpolated into the
            classifier prompt so the model can flag context-specific
            confidential content alongside Art. 4 PII.

    Never raises — Tier C is best-effort by design. Findings list is the only
    contract callers depend on.

    Fail-closed (D8): when a REAL backend is configured (resolved spec != mock)
    but it cannot construct, is unavailable, or its classify raises, this returns
    a single high-severity ``tier_c_unavailable`` Finding so the decision layer
    REFUSES, rather than returning ``[]`` (which reads as "no PII → allow"). When
    mock is the effective backend, it stays permissive (returns ``[]``).
    """
    if not text.strip():
        return []

    spec = _resolve_spec()
    real_required = spec != _DEFAULT_SPEC

    # NB: detail strings below carry ONLY the backend spec + the exception CLASS
    # name — never str(e). An exception message can embed file/model paths or, if
    # a misbehaving backend echoes its input, the scanned text itself; the Finding
    # is persisted, so leaking it would defeat the very privacy guarantee Tier C
    # exists to uphold.
    try:
        backend = _build_backend(spec)
    except BackendError as e:
        if real_required:
            return [tier_c_unavailable_finding(
                f"configured backend {spec!r} failed to load ({type(e).__name__})")]
        return []                               # mock effective → permissive

    if not backend.is_available():
        if real_required:
            return [tier_c_unavailable_finding(
                f"configured backend {spec!r} is not available")]
        return []                               # mock effective → permissive

    try:
        classification = backend.classify(text, context=context)
    except Exception as e:                       # noqa: BLE001 — fail closed if real
        if real_required:
            return [tier_c_unavailable_finding(
                f"backend {spec!r} classify failed ({type(e).__name__})")]
        return []

    # A real backend that returns a malformed result (not a dict, or no
    # ``contains_pii`` verdict at all) has not actually classified the text —
    # treat that as "could not run" and FAIL CLOSED, not as "no PII → allow".
    # An EXPLICIT falsy verdict (``contains_pii: False``) is a real "clean"
    # answer and is honoured.
    if not isinstance(classification, dict):
        if real_required:
            return [tier_c_unavailable_finding(
                "backend returned a non-dict classification")]
        return []
    verdict = classification.get("contains_pii", None)
    if verdict is None:
        if real_required:
            return [tier_c_unavailable_finding(
                "backend returned no contains_pii verdict")]
        return []
    if not verdict:
        return []                               # explicit clean verdict → allow

    finding_type = classification.get("type", "unknown")
    confidence = float(classification.get("confidence", 0.7))
    reason = str(classification.get("reason", ""))[:200]

    severity = "high" if finding_type in ("health", "financial", "confidential") else "medium"

    return [
        Finding(
            tier="C",
            type="pii_in_argument",  # caller re-labels for response side
            severity=severity,
            field=None,
            detail=f"semantic classifier flagged {finding_type}: {reason}",
            confidence=confidence,
        )
    ]
