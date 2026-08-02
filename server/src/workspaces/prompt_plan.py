# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fingerprint-driven prompt planning — an LLM-usage optimiser.

The problem-solution fingerprint already carries everything a prompt router
needs, so model spend can be decided BEFORE a single token is sent:

  * **recall** — if the case memory holds a verified solution for this
    problem shape, reuse it: zero model tokens (the largest saving).
  * **method** — the issue type fixes the reasoning method; a clean
    subsumption issue with grounded norms can run on a cheap LOCAL model,
    an open-balancing issue needs a HOSTED one.
  * **rooms** — the prompt carries only the token's own norm anchors, never
    the whole corpus, and only the per-phase briefs (already model-sized),
    never the teaching curriculum.

This is the planner the LLM-prompting skill calls: deterministic, auditable,
no model in the loop. It returns a per-token plan plus a budget estimate
against the naive baseline (one monolithic prompt carrying the whole corpus).
Token counts are estimates (``CHARS_PER_TOKEN``), labelled as such.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from . import reasoning_phases as rp
from .reasoning_contract import PROFILES

CHARS_PER_TOKEN = 4          # rough industry heuristic; estimate, not a meter
ROOM_CHARS = 220             # budgeted chars for one cited norm room's text
REUSE_MIN_EVIDENCE = 1       # human-closed cases needed to trust a reuse

#: Methods whose work is open judgment (balancing, proportionality) — a cheap
#: local model should not be trusted to close them; route hosted.
_JUDGMENT_PROFILES = {"generic"}


def _est_tokens(chars: int) -> int:
    return max(0, round(chars / CHARS_PER_TOKEN))


def _phase_brief_chars(profile: str) -> int:
    prof = profile if profile in PROFILES else "generic"
    return sum(len(rp.brief(ph, profile=prof)) for ph in rp.PHASE_ORDER)


def _route(token, tiers: dict) -> tuple[str, Optional[str]]:
    """Pick a tier from the fingerprint. Returns (action, fallback_note).
    Judgment methods → hosted. Otherwise local when available, else hosted."""
    method = token.method()
    rooms = [a for a in token.norm_anchors if a]
    wants_local = method not in _JUDGMENT_PROFILES and bool(rooms)
    if not wants_local:
        return "hosted", None
    if tiers.get("local"):
        return "local", None
    return "hosted", "local→hosted"        # honest: no local tier configured


def plan_prompts(
    tokens: list,
    *,
    recall_fn: Callable[[Any], list],
    tiers: dict,
    corpus_rooms: Optional[list] = None,
) -> dict[str, Any]:
    """Plan model usage for a set of issue tokens.

    ``recall_fn(token) -> [hit, ...]`` is injected (wrap
    ``case_index.recall_for_token`` bound to a folder, or a stub). ``tiers``
    is e.g. ``{"local": True, "hosted": True}``. ``corpus_rooms`` is the full
    norm set the naive baseline would have dumped into one prompt — used only
    to size the saving.
    """
    entries: list[dict[str, Any]] = []
    planned_tokens = 0
    counts = {"reuse": 0, "local": 0, "hosted": 0}

    for tok in tokens:
        hits = recall_fn(tok) or []
        best = hits[0] if hits else None
        if best and best.get("evidence", 0) >= REUSE_MIN_EVIDENCE:
            entries.append({
                "issue_id": tok.issue_id, "issue_type": tok.issue_type,
                "action": "reuse", "est_tokens": 0,
                "reuse_solver": best["solver"],
                "reuse_evidence": best["evidence"],
                "prompt": None, "fallback": None})
            counts["reuse"] += 1
            continue

        action, fallback = _route(tok, tiers)
        rooms = sorted({a for a in tok.norm_anchors if a})
        method = tok.method()
        chars = _phase_brief_chars(method) + ROOM_CHARS * len(rooms)
        est = _est_tokens(chars)
        planned_tokens += est
        counts[action] += 1
        entries.append({
            "issue_id": tok.issue_id, "issue_type": tok.issue_type,
            "action": action, "est_tokens": est, "fallback": fallback,
            "reuse_solver": None,
            "prompt": {"profile": method, "phases": list(rp.PHASE_ORDER),
                       "rooms": rooms}})

    # naive baseline: one monolithic HOSTED prompt carrying every phase brief
    # AND the whole corpus, once per token (no reuse, no scoping).
    corpus_chars = ROOM_CHARS * len(corpus_rooms or [])
    naive_per_token = _phase_brief_chars("generic") + corpus_chars
    naive_tokens = _est_tokens(naive_per_token) * max(1, len(tokens))
    saving = naive_tokens - planned_tokens
    saving_pct = round(100 * saving / naive_tokens, 1) if naive_tokens else 0.0

    return {
        "entries": entries,
        "summary": counts,
        "planned_tokens": planned_tokens,
        "naive_tokens": naive_tokens,
        "saving_tokens": saving,
        "saving_pct": saving_pct,
        "estimate_basis": f"{CHARS_PER_TOKEN} chars/token (estimate)",
    }
