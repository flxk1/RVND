# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tier M — moderation detective. Sits BESIDE Tier B/C in ``lock_text`` and
composes into the same findings list (strictest-wins, high severity → refuse).

Driven entirely by the folder policy's ``moderation_rules`` (a no-op when absent,
so legacy/unconfigured folders are unaffected):

  - ``banned_terms``    — case-insensitive substring matches (deterministic, no
                          backend; over-matching is the safe direction).
  - ``banned_patterns`` — regex matches (deterministic, no backend). A pattern
                          that will not compile is a rule we cannot enforce →
                          FAIL CLOSED, never silently skipped.
  - ``categories``      — semantic categories (e.g. hate, self-harm) that can only
                          be judged by a classifier backend. When categories are
                          declared the backend is REQUIRED, so an absent /
                          unavailable / erroring backend FAILS CLOSED (D8) — a
                          would-be moderation refuse must not degrade to allow.

Every Tier-M finding is ``severity="high"`` → the decision layer refuses, and the
egress oversight translation routes that refuse to a human at APPROVE+ for free; a
separate "minimise" action is deliberately omitted in v1 because the regex redactor
only knows PII spans and could not actually scrub arbitrary banned content.

Findings NEVER carry the matched text or the scanned content (they are persisted to
the audit chain) — they name the RULE (index) or the policy-defined category label,
and on failure carry only the exception CLASS name, mirroring Tier C's discipline.
"""
from __future__ import annotations

import re
from typing import Any

from .core import Finding


class ModerationBackendError(Exception):
    """A configured moderation classifier backend could not be constructed."""


def make_moderation_backend(spec: str) -> Any:
    """Hook for a moderation classifier backend. No classifier ships with Tier M
    v1, so the default ALWAYS raises — a policy that declares ``categories`` (which
    require a backend) therefore FAILS CLOSED until a real backend is wired and this
    hook is overridden to return one. Tests monkeypatch this. A backend must expose
    ``is_available() -> bool`` and ``classify(text, categories) -> dict`` returning
    ``{"flagged": [<category-label>, ...]}``."""
    raise ModerationBackendError(f"no moderation backend for spec {spec!r}")


def tier_m_requires_real_backend(rules: dict | None) -> bool:
    """True when the rules declare semantic ``categories`` — which can only be
    enforced by a classifier backend, so an absent/unavailable backend must FAIL
    CLOSED (D8). Deterministic banned_terms/banned_patterns need no backend, so
    rules with only those do not require one."""
    return bool(isinstance(rules, dict) and rules.get("categories"))


def tier_m_unavailable_finding(detail: str) -> Finding:
    """A high-severity, fail-closed Finding: a required moderation check could not
    run, so the decision layer refuses rather than silently allowing (D8)."""
    return Finding(
        tier="M",
        type="tier_m_unavailable",
        severity="high",
        field=None,
        detail=f"Tier-M moderation check could not run — failing closed: {detail}",
        confidence=1.0,
    )


def _match_finding(detail: str) -> Finding:
    return Finding(tier="M", type="moderation_match", severity="high",
                   field=None, detail=detail, confidence=1.0)


def tier_m_check_moderation(text: str, *, rules: dict | None) -> list[Finding]:
    """Detective moderation over ``text`` driven by policy ``rules``.

    No-op (``[]``) when ``rules`` is absent or empty — Tier M is opt-in. Otherwise
    deterministic banned_terms/banned_patterns always enforce, and declared
    ``categories`` require a classifier backend that FAILS CLOSED when unavailable.

    Never raises; never leaks the matched text or scanned content into a Finding.
    """
    if not text.strip() or not isinstance(rules, dict) or not rules:
        return []

    findings: list[Finding] = []
    low = text.lower()

    # Deterministic — banned terms (case-insensitive substring; over-match is safe).
    # A present-but-malformed rule (not a list) is one we cannot enforce → FAIL
    # CLOSED, never silently skipped.
    terms = rules.get("banned_terms")
    if terms is not None and not isinstance(terms, (list, tuple)):
        findings.append(tier_m_unavailable_finding("banned_terms must be a list"))
    elif isinstance(terms, (list, tuple)):
        for i, term in enumerate(terms):
            t = str(term).strip().lower()
            if t and t in low:
                findings.append(_match_finding(f"banned term rule #{i} matched"))

    # Deterministic — banned regex patterns. A malformed rules shape, or a pattern
    # that will not compile (or blows up the engine), is a rule we cannot enforce →
    # FAIL CLOSED, not skipped. NOTE: Python `re` has no execution timeout, so a
    # pathological author-supplied pattern over long egress text is a known ReDoS
    # footgun (patterns are policy-author-trusted); a real regex timeout is a follow-up.
    patterns = rules.get("banned_patterns")
    if patterns is not None and not isinstance(patterns, (list, tuple)):
        findings.append(tier_m_unavailable_finding("banned_patterns must be a list"))
    elif isinstance(patterns, (list, tuple)):
        for i, pat in enumerate(patterns):
            try:
                rx = re.compile(str(pat))
            except re.error as e:
                findings.append(tier_m_unavailable_finding(
                    f"banned_patterns rule #{i} is not a valid regex ({type(e).__name__})"))
                continue
            try:
                if rx.search(text):
                    findings.append(_match_finding(f"banned pattern rule #{i} matched"))
            except Exception as e:  # noqa: BLE001 — catastrophic engine failure
                findings.append(tier_m_unavailable_finding(
                    f"banned_patterns rule #{i} failed to evaluate ({type(e).__name__})"))

    # Semantic — categories REQUIRE a classifier backend. Fail closed when declared
    # but unavailable/erroring (D8). Detail carries only class names + the
    # policy-defined category labels, never the scanned text.
    categories = rules.get("categories") or []
    if categories:
        spec = str(rules.get("backend", "")).strip()
        try:
            backend = make_moderation_backend(spec)
        except Exception as e:  # noqa: BLE001
            return findings + [tier_m_unavailable_finding(
                f"moderation backend unavailable ({type(e).__name__})")]
        try:
            available = bool(backend.is_available())
        except Exception as e:  # noqa: BLE001
            return findings + [tier_m_unavailable_finding(
                f"moderation backend availability check failed ({type(e).__name__})")]
        if not available:
            return findings + [tier_m_unavailable_finding(
                f"moderation backend is not available")]
        try:
            result = backend.classify(text, list(categories))
        except Exception as e:  # noqa: BLE001
            return findings + [tier_m_unavailable_finding(
                f"moderation backend classify failed ({type(e).__name__})")]
        if not isinstance(result, dict) or "flagged" not in result:
            return findings + [tier_m_unavailable_finding(
                "classifier backend returned a malformed result")]
        wanted = {str(c) for c in categories}
        for cat in (result.get("flagged") or []):
            label = str(cat)
            if label in wanted:
                findings.append(_match_finding(
                    f"moderation classifier flagged category {label!r}"))

    return findings
