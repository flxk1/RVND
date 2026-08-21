# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""LLM exchange capture — Phase B4.

Two modes, designed to satisfy two distinct requirements:

- **INTERACTIVE** — user chatting with an LLM. Capture is OPT-IN, gated by
  the folder's oversight dial. At AUTONOMOUS/NOTIFY: not captured (the user
  wanted silence). At REVIEW/APPROVE: prompted. At SUPERVISED/MANUAL: every
  exchange the user wants saved is explicit.

- **AGENTIC** — automated agent or skill making LLM calls as part of a
  workflow. Capture is MANDATORY (audit floor for GDPR Art. 22 explainability,
  Art. 30 records of processing, AI Act Art. 13 documentation). The oversight
  dial controls **verbosity**, not whether-to-capture.

Per-folder policy (:mod:`policy`) interacts with both modes:

- ``lock_is_active`` has no effect on capture (lock is about egress, not memory).
- ``oversight_is_active = False`` (user explicitly disabled prompts):
    * INTERACTIVE: not captured (treat as silent-not-saving — user asked for silence).
    * AGENTIC: still captured, at MAX verbosity. Disable was about prompts,
      not the audit floor. Captures are stamped with ``oversight_bypassed=True``
      so a reviewer can see what was decided without the human in the loop.

The verbosity matrix maps (mode, oversight_level) → what gets stored.
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .memory import WorkspaceMemory
from .policy import load_policy
from .policy import effective_policy


# ---------------------------------------------------------------------------
# Modes + verbosity
# ---------------------------------------------------------------------------


class IngestMode(Enum):
    """How the LLM exchange should be captured."""

    INTERACTIVE = "interactive"
    """User-chat. Capture is opt-in, gated by oversight."""

    AGENTIC = "agentic"
    """Agent / skill workflow. Capture is mandatory; oversight controls verbosity."""


class OversightLevel(Enum):
    """The six-level dial. Local to this module to avoid a hard dep on the
    full oversight package — callers can pass strings or this enum."""

    AUTONOMOUS = 1
    NOTIFY = 2
    REVIEW = 3
    APPROVE = 4
    SUPERVISED = 5
    MANUAL = 6


_OVERSIGHT_BY_NAME = {ol.name.lower(): ol for ol in OversightLevel}


def _coerce_oversight(value: OversightLevel | str | int) -> OversightLevel:
    if isinstance(value, OversightLevel):
        return value
    if isinstance(value, int):
        return OversightLevel(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _OVERSIGHT_BY_NAME:
            return _OVERSIGHT_BY_NAME[v]
    raise ValueError(f"invalid oversight value: {value!r}")


class VerbosityLevel(Enum):
    """What level of detail is captured to memory."""

    METADATA = "metadata"
    """Model id + prompt-context-hash + response-hash + timestamp + cost. No text."""

    PREVIEW = "preview"
    """+ 200-char single-line preview of the response."""

    PREVIEW_PLUS_CITATIONS = "preview+citations"
    """+ full cited_sources list."""

    FULL = "full"
    """+ full response body + full prompt context."""

    FULL_PLUS_TRACE = "full+trace"
    """+ tool_call_trace + per-step instrumentation."""

    NONE = "none"
    """Not captured at all (interactive mode under low oversight)."""


# Agentic mode: oversight level → verbosity. Mandatory floor is METADATA.
_AGENTIC_MATRIX = {
    OversightLevel.AUTONOMOUS: VerbosityLevel.METADATA,
    OversightLevel.NOTIFY: VerbosityLevel.PREVIEW,
    OversightLevel.REVIEW: VerbosityLevel.PREVIEW_PLUS_CITATIONS,
    OversightLevel.APPROVE: VerbosityLevel.FULL,
    OversightLevel.SUPERVISED: VerbosityLevel.FULL_PLUS_TRACE,
    OversightLevel.MANUAL: VerbosityLevel.FULL_PLUS_TRACE,
}


# Interactive mode: oversight level → what to do.
# Returns (verbosity, prompts_user) for each level.
_INTERACTIVE_MATRIX: dict[OversightLevel, tuple[VerbosityLevel, bool]] = {
    OversightLevel.AUTONOMOUS: (VerbosityLevel.NONE, False),
    OversightLevel.NOTIFY: (VerbosityLevel.NONE, False),
    OversightLevel.REVIEW: (VerbosityLevel.FULL, True),       # post-hoc prompt
    OversightLevel.APPROVE: (VerbosityLevel.FULL, True),       # inline prompt
    OversightLevel.SUPERVISED: (VerbosityLevel.FULL, True),
    OversightLevel.MANUAL: (VerbosityLevel.FULL, True),
}


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class LLMExchange:
    """One round-trip with a cloud LLM."""

    model: str
    prompt_context: str
    response: str
    cited_sources: list[str] = field(default_factory=list)
    cost_estimate_cents: float | None = None
    tool_call_trace: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    request_id: str = ""


@dataclass
class CaptureResult:
    """The outcome of a :func:`capture_llm_exchange` call.

    The audit-log entry is ALWAYS written for agentic mode, even when the
    pair itself isn't captured at full fidelity. ``audit_id`` is non-empty
    in that case.
    """

    captured: bool
    pair_id: str | None
    verbosity: VerbosityLevel
    prompted_user: bool
    audit_id: str
    mode: IngestMode
    skipped_reason: str = ""
    oversight_bypassed: bool = False
    """True if the folder policy disabled oversight + we captured anyway
    (agentic) or skipped capture (interactive)."""


# ---------------------------------------------------------------------------
# The verbosity-projection: which fields land in the pair given a level
# ---------------------------------------------------------------------------


def _short(text: str, n: int = 200) -> str:
    """Single-line truncation. Used for PREVIEW verbosity."""
    return " ".join(text.split())[:n]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _redact_obj(obj: Any, redact: "Callable[[str], str]") -> Any:
    """Recursively apply ``redact`` to every string in a nested list/dict
    structure (e.g. a tool-call trace), leaving non-strings untouched."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [_redact_obj(x, redact) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_redact_obj(x, redact) for x in obj)
    if isinstance(obj, dict):
        # Redact string keys too — a secret can ride in a key (e.g. a header name).
        return {(redact(k) if isinstance(k, str) else k): _redact_obj(v, redact)
                for k, v in obj.items()}
    return obj


def _project_pair(
    exchange: LLMExchange,
    verbosity: VerbosityLevel,
    *,
    folder_context: str,
) -> dict[str, Any]:
    """Build a pair dict carrying exactly the fields permitted by ``verbosity``."""
    # Redact secrets/PII BEFORE any persistence (summary is written even at the
    # METADATA floor, so redaction must precede everything). The whole pair —
    # summary, body, prompt_context facet, tool-call trace, and the hashes — is
    # derived from the redacted text, so no credential reaches the ledger or the
    # signed chain in cleartext.
    from .lock import redact_for_capture
    p_ctx = redact_for_capture(exchange.prompt_context)
    resp = redact_for_capture(exchange.response)
    # Identity hashes are over the RAW text (one-way, non-leaking) so two
    # exchanges that differ only in a redacted secret keep DISTINCT ids; only
    # stored CONTENT below is redacted.
    prompt_hash = _hash(exchange.prompt_context)
    response_hash = _hash(exchange.response)
    pid = "sha256:llm-" + hashlib.sha256(
        (folder_context + "\x1f" + exchange.model + "\x1f"
         + prompt_hash + "\x1f" + response_hash).encode("utf-8")
    ).hexdigest()[:32]
    problem_id = "sha256:llm-problem-" + prompt_hash

    problem: dict[str, Any] = {
        "id": problem_id,
        "scope": "llm",
        "type": "llm_exchange",
        "summary": _short(p_ctx, 200),
        "facets": {
            "model": exchange.model,
            "prompt_context_hash": prompt_hash,
            "prompt_context_length": len(p_ctx),
            "response_hash": response_hash,
            "response_length": len(resp),
            "verbosity_level": verbosity.value,
        },
    }
    if exchange.cost_estimate_cents is not None:
        problem["facets"]["cost_estimate_cents"] = exchange.cost_estimate_cents
    if exchange.request_id:
        problem["facets"]["request_id"] = exchange.request_id

    # Body content gated by verbosity.
    if verbosity == VerbosityLevel.METADATA:
        body = ""
    elif verbosity == VerbosityLevel.PREVIEW:
        body = _short(resp, 200)
    elif verbosity == VerbosityLevel.PREVIEW_PLUS_CITATIONS:
        body = _short(resp, 200)
    elif verbosity in (VerbosityLevel.FULL, VerbosityLevel.FULL_PLUS_TRACE):
        body = resp
    else:
        body = ""

    cited = []
    if verbosity in (
        VerbosityLevel.PREVIEW_PLUS_CITATIONS,
        VerbosityLevel.FULL,
        VerbosityLevel.FULL_PLUS_TRACE,
    ):
        # Citations are usually URLs — redact URL-embedded creds / ?api_key=.
        cited = [redact_for_capture(s) for s in exchange.cited_sources]

    solution: dict[str, Any] = {
        "id": pid,
        "problem_id": problem_id,
        "body": body,
        "body_format": "metadata" if not body else "prose",
        "authority_tier": 5,    # LLM output, unverified
        "confidence": 0.6,
        "cited_sources": cited,
        "extractor_chain": [f"llm_capture:{exchange.model}"],
        "extractor_version": "0.1.0",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                    time.gmtime(exchange.timestamp)),
    }
    # Full prompt context only at FULL+ verbosity (redacted).
    if verbosity in (VerbosityLevel.FULL, VerbosityLevel.FULL_PLUS_TRACE):
        problem["facets"]["prompt_context"] = p_ctx
    if verbosity == VerbosityLevel.FULL_PLUS_TRACE:
        # Tool-call args/results can carry secrets too — redact string values.
        solution["tool_call_trace"] = _redact_obj(
            list(exchange.tool_call_trace), redact_for_capture)

    return {
        "id": pid,
        "problem": problem,
        "solution": solution,
    }


# ---------------------------------------------------------------------------
# Verbosity / capture decision — pure function for testability
# ---------------------------------------------------------------------------


def decide_verbosity(
    mode: IngestMode,
    oversight: OversightLevel,
    *,
    oversight_active: bool = True,
) -> tuple[VerbosityLevel, bool]:
    """Compute (verbosity, prompts_user) for an exchange.

    ``oversight_active`` reflects the folder's policy. When False (user
    disabled oversight) the decision falls back to a sensible default:

    - AGENTIC: max verbosity, no prompt (FULL_PLUS_TRACE). Captures the
      most evidence since we can't ask the user mid-flow.
    - INTERACTIVE: NONE (silent — user disabled prompts; capturing without
      asking would be data hoarding).
    """
    if not oversight_active:
        if mode == IngestMode.AGENTIC:
            return VerbosityLevel.FULL_PLUS_TRACE, False
        return VerbosityLevel.NONE, False

    if mode == IngestMode.AGENTIC:
        return _AGENTIC_MATRIX[oversight], False
    # Interactive
    return _INTERACTIVE_MATRIX[oversight]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def capture_llm_exchange(
    exchange: LLMExchange,
    *,
    mode: IngestMode,
    oversight: OversightLevel | str | int,
    folder_context: str | Path,
    log_root: str | Path | None = None,
    user_decision_callback: Callable[[LLMExchange, OversightLevel, VerbosityLevel], bool] | None = None,
    actor: str = "agent",
) -> CaptureResult:
    """Capture an LLM exchange into the folder's memory.

    The single entry point cloud-LLM-calling code goes through. Returns a
    :class:`CaptureResult` describing what was stored at what verbosity.

    Args:
        exchange: the LLM exchange to capture.
        mode: INTERACTIVE (user chat) or AGENTIC (workflow audit-floor).
        oversight: oversight level (enum, name, or int).
        folder_context: the folder this exchange is scoped to.
        log_root: override the default log root (tests).
        user_decision_callback: in INTERACTIVE mode where the level requires
            a prompt, called with (exchange, level, proposed_verbosity).
            Returns True to capture, False to skip. If None, defaults to
            "capture" — appropriate for non-interactive hosts (CLI tools that
            assume user already said yes by running the command).
        actor: who's performing the capture (recorded in the audit log).
    """
    oversight_level = _coerce_oversight(oversight)
    policy = effective_policy(folder_context, log_root=log_root)
    oversight_active = policy.oversight_is_active

    verbosity, will_prompt = decide_verbosity(
        mode, oversight_level, oversight_active=oversight_active,
    )

    # Audit-log foundation: even when verbosity is NONE we still write a
    # system-event so an audit replay can see the call was made.
    mem = WorkspaceMemory(folder_context, log_root=log_root, actor=actor)
    audit_id = str(uuid.uuid4())

    # Interactive mode at a prompt-requiring level → ask the user.
    prompted = False
    if mode == IngestMode.INTERACTIVE and will_prompt:
        prompted = True
        if user_decision_callback is not None:
            user_says_yes = bool(user_decision_callback(exchange, oversight_level, verbosity))
        else:
            user_says_yes = True
        if not user_says_yes:
            return CaptureResult(
                captured=False,
                pair_id=None,
                verbosity=VerbosityLevel.NONE,
                prompted_user=True,
                audit_id=audit_id,
                mode=mode,
                skipped_reason="user_declined",
                oversight_bypassed=not oversight_active,
            )

    if verbosity == VerbosityLevel.NONE:
        return CaptureResult(
            captured=False,
            pair_id=None,
            verbosity=VerbosityLevel.NONE,
            prompted_user=prompted,
            audit_id=audit_id,
            mode=mode,
            skipped_reason=("low_oversight_interactive"
                            if mode == IngestMode.INTERACTIVE
                            else "verbosity_none"),
            oversight_bypassed=not oversight_active,
        )

    pair = _project_pair(exchange, verbosity, folder_context=str(folder_context))
    pair_id = mem.remember(
        pair,
        channel="llm_answer",
        source_hash=_hash(exchange.prompt_context),
    )

    return CaptureResult(
        captured=True,
        pair_id=pair_id,
        verbosity=verbosity,
        prompted_user=prompted,
        audit_id=audit_id,
        mode=mode,
        skipped_reason="",
        oversight_bypassed=not oversight_active,
    )


def _is_spend_pair(problem: dict[str, Any]) -> bool:
    """True if a memory pair's problem is a captured LLM/web exchange — i.e. a
    pair that can carry a ``cost_estimate_cents`` facet. The single definition
    of "what counts as spend", shared by the read (capture_read) and the
    enforcement (operate's cost-cap guard) so the two can never drift."""
    scope = problem.get("scope") or ""
    typ = problem.get("type") or ""
    return scope in ("llm", "web", "websearch") or typ.endswith(("exchange", "search"))


def _pair_cost(problem: dict[str, Any]) -> float:
    """The spend contribution (cents) of one pair — validated, never negative.

    Only a finite, non-negative number counts. A malformed facet (None, a
    string, ``bool``, NaN/inf, or a negative value) contributes 0 — crucially
    it never *subtracts*, so a single poisoned facet can't pull the total below
    the cap and silently disable enforcement (fail-safe). The single
    cost-extraction rule, shared by capture_read and folder_spend_cents."""
    cost = (problem.get("facets") or {}).get("cost_estimate_cents")
    # bool is an int subclass — exclude it so True doesn't count as 1.0.
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return 0.0
    c = float(cost)
    if not math.isfinite(c) or c < 0:
        return 0.0
    return c


def folder_spend_cents(
    folder_context: str | Path,
    *,
    log_root: str | Path | None = None,
    actor: str = "agent",
) -> float:
    """Total recorded spend (cents) for a folder, summed over the capture
    ledger. Reads only what the verbosity policy already stored (no bypass);
    a pair with no valid ``cost_estimate_cents`` facet contributes 0. Read-only.

    NOTE (declared limitation): this sees only ledger entries the memory layer
    serves. A SEALED workspace serves no pairs, so its spend reads 0 — enforcement,
    like the readable spend, reflects only accessible entries. A future hardening
    can refuse the read when the workspace is sealed."""
    mem = WorkspaceMemory(folder_context, log_root=log_root, actor=actor)
    spend = 0.0
    for p in mem.all_pairs():
        prob = p.get("problem") or {}
        if _is_spend_pair(prob):
            spend += _pair_cost(prob)
    return round(spend, 4)
