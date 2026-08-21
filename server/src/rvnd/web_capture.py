# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Web-search capture — Phase B3.

Mirrors the two-mode design of :mod:`llm_capture`: INTERACTIVE (user-chat
opt-in) and AGENTIC (workflow audit-floor, mandatory). Same verbosity matrix
mapped to web-specific fields.

Web searches sit on a different sensitivity profile than LLM exchanges:

- **URLs themselves can leak intent** ("divorce lawyer near me"). The METADATA
  verbosity captures the query *hash*, not the query string. At PREVIEW the
  query string + URL list become visible. Don't fall into "URLs are public so
  capture them at the lowest level" — query intent is what's at risk.

- **Snippets vs full content** is the cost frontier (storage + later
  re-retrieval). PREVIEW captures snippets; FULL pulls the full body where
  the caller had it.

- **Per-result tracing** (rank, freshness, dedupe path) is FULL_PLUS_TRACE
  only — useful for debugging the search pipeline, not for routine capture.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .memory import WorkspaceMemory
from .llm_capture import (
    CaptureResult,
    IngestMode,
    OversightLevel,
    VerbosityLevel,
    _coerce_oversight,
    decide_verbosity,
)
from .policy import load_policy
from .policy import effective_policy


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class WebSearchResult:
    """One result row from a web search."""

    url: str
    title: str = ""
    snippet: str = ""
    full_text: str = ""
    rank: int = 0


@dataclass
class WebSearchExchange:
    """One round-trip with a search engine."""

    query: str
    engine: str
    results: list[WebSearchResult] = field(default_factory=list)
    cost_estimate_cents: float | None = None
    timestamp: float = field(default_factory=time.time)
    request_id: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    """Optional per-step trace (retries, cache hits, dedupe path).
    Stored only at FULL_PLUS_TRACE verbosity."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _short(text: str, n: int = 200) -> str:
    return " ".join(text.split())[:n]


# ---------------------------------------------------------------------------
# Pair projection — per verbosity
# ---------------------------------------------------------------------------


def _project_pair(
    exchange: WebSearchExchange,
    verbosity: VerbosityLevel,
    *,
    folder_context: str,
) -> dict[str, Any]:
    """Build one pair per query (not per result). Verbosity controls how much
    of the result list lands in the body."""
    query_hash = _hash(exchange.query)
    pid = "sha256:web-" + hashlib.sha256(
        (folder_context + "\x1f" + exchange.engine + "\x1f" + query_hash).encode("utf-8")
    ).hexdigest()[:32]
    problem_id = "sha256:web-problem-" + query_hash

    # Facets — always populated with metadata.
    facets: dict[str, Any] = {
        "engine": exchange.engine,
        "query_hash": query_hash,
        "result_count": len(exchange.results),
        "verbosity_level": verbosity.value,
    }
    if exchange.cost_estimate_cents is not None:
        facets["cost_estimate_cents"] = exchange.cost_estimate_cents
    if exchange.request_id:
        facets["request_id"] = exchange.request_id

    # The query string itself is only revealed at PREVIEW+ verbosity. At
    # METADATA only the hash is stored — query intent is the risk surface.
    if verbosity != VerbosityLevel.METADATA:
        facets["query"] = exchange.query

    # Build the body + cited_sources per verbosity.
    if verbosity == VerbosityLevel.METADATA:
        body = ""
        cited: list[str] = []
    elif verbosity == VerbosityLevel.PREVIEW:
        # URL list + titles, no snippets.
        lines = [
            f"[{r.rank}] {r.title} — {r.url}".strip()
            for r in exchange.results
        ]
        body = "\n".join(lines)
        cited = [r.url for r in exchange.results if r.url]
    elif verbosity == VerbosityLevel.PREVIEW_PLUS_CITATIONS:
        # + snippets.
        chunks: list[str] = []
        for r in exchange.results:
            head = f"[{r.rank}] {r.title} — {r.url}".strip()
            snippet = _short(r.snippet, 300)
            chunks.append(head + ("\n  " + snippet if snippet else ""))
        body = "\n".join(chunks)
        cited = [r.url for r in exchange.results if r.url]
    elif verbosity in (VerbosityLevel.FULL, VerbosityLevel.FULL_PLUS_TRACE):
        # + full text where retrieved.
        chunks = []
        for r in exchange.results:
            head = f"[{r.rank}] {r.title} — {r.url}".strip()
            full = r.full_text or r.snippet
            chunks.append(head + ("\n" + full if full else ""))
        body = "\n\n".join(chunks)
        cited = [r.url for r in exchange.results if r.url]
    else:
        body = ""
        cited = []

    summary = (
        f"web search: {_short(exchange.query, 100)}"
        if verbosity != VerbosityLevel.METADATA
        else f"web search (hash={query_hash}) → {len(exchange.results)} results"
    )

    # Redact secrets/PII before persistence (same invariant as the LLM path):
    # the query, the assembled result body (titles/URLs/snippets/full_text),
    # the cited URLs, and the trace all reach the ledger + signed chain. The
    # identity hashes above are over the RAW query (one-way, non-leaking).
    from .lock import redact_for_capture
    summary = redact_for_capture(summary)
    body = redact_for_capture(body)
    cited = [redact_for_capture(u) for u in cited]
    if "query" in facets:
        facets["query"] = redact_for_capture(facets["query"])

    problem = {
        "id": problem_id,
        "scope": "web",
        "type": "websearch",
        "summary": summary,
        "facets": facets,
    }

    solution: dict[str, Any] = {
        "id": pid,
        "problem_id": problem_id,
        "body": body,
        "body_format": "metadata" if not body else "prose",
        "authority_tier": 5,        # web, unverified
        "confidence": 0.5,
        "cited_sources": cited,
        "extractor_chain": [f"web_capture:{exchange.engine}"],
        "extractor_version": "0.1.0",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                    time.gmtime(exchange.timestamp)),
    }

    if verbosity == VerbosityLevel.FULL_PLUS_TRACE and exchange.trace:
        from .llm_capture import _redact_obj
        solution["trace"] = _redact_obj(list(exchange.trace), redact_for_capture)

    return {
        "id": pid,
        "problem": problem,
        "solution": solution,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def capture_web_search(
    exchange: WebSearchExchange,
    *,
    mode: IngestMode,
    oversight: OversightLevel | str | int,
    folder_context: str | Path,
    log_root: str | Path | None = None,
    user_decision_callback: Callable[[WebSearchExchange, OversightLevel, VerbosityLevel], bool] | None = None,
    actor: str = "agent",
) -> CaptureResult:
    """Capture a web search exchange into the folder's memory.

    Same contract as :func:`capture_llm_exchange`:

    - Agentic mode: mandatory capture; verbosity scales with oversight.
    - Interactive mode: opt-in; the user-decision callback gates capture at
      REVIEW+; no capture at AUTONOMOUS/NOTIFY.
    - Folder policy with oversight disabled:
        * AGENTIC → max verbosity (FULL_PLUS_TRACE).
        * INTERACTIVE → skipped (user wants silence).

    Returns a :class:`CaptureResult` reporting verbosity + pair_id + audit_id.
    """
    oversight_level = _coerce_oversight(oversight)
    policy = effective_policy(folder_context, log_root=log_root)
    oversight_active = policy.oversight_is_active

    verbosity, will_prompt = decide_verbosity(
        mode, oversight_level, oversight_active=oversight_active,
    )

    mem = WorkspaceMemory(folder_context, log_root=log_root, actor=actor)
    audit_id = str(uuid.uuid4())

    prompted = False
    if mode == IngestMode.INTERACTIVE and will_prompt:
        prompted = True
        if user_decision_callback is not None:
            user_says_yes = bool(
                user_decision_callback(exchange, oversight_level, verbosity)
            )
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
        channel="websearch",
        source_hash=_hash(exchange.query),
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
