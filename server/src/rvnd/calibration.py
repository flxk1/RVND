# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Calibration sampling ledger — the safeguard against the two harshest
objections to scaled oversight.

Objection 1 (calibration regress): if reuse is validated by the same
automated confidence that chose to reuse, the human is trusting a machine's
judgment about when to trust machine judgment. Answer: validate reuse with an
INDEPENDENT signal — a human RE-JUDGING a sampled reuse. That judgment is
ground truth, not self-report.

Objection 2 (gameable metric): a sampling RATE measures activity, not quality;
a reviewer can sample and rubber-stamp. Answer: the integrity number is the
DISAGREEMENT rate on sampled reuses. A rising disagreement trend is oversight
catching decay (Bainbridge), and a rubber stamp cannot lower it without
recording a false agreement on the signed chain.

Objection 8 (leverage only 'potential'): reuse events are logged here, so
reuse is realised and measurable.

All events ride the signed append-only chain (same pattern as case_index /
parties); the report is a pure replay projection. Human-only ground truth:
an agent or non-active party cannot supply a sample judgment (consistent with
the approvals doctrine).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog


def _append(folder_context: str, actor: str, log_root, extra: dict) -> str:
    log = MutationLog(folder_context, log_root=log_root)
    return log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"calib:{extra.get('kind', '')}",
        channel="system",
        actor=actor,
        extra=extra,
    ))


def log_reuse(
    folder_context: str,
    *,
    fingerprint: dict[str, Any],
    solver: str,
    source_receipt: str = "",
    actor: str = "system",
    log_root: Optional[str] = None,
) -> str:
    """Record that a prior human-closed solution was REUSED for a new instance
    of its problem shape — realised leverage, and the population the sampler
    draws from."""
    return _append(folder_context, actor, log_root, {
        "kind": "ReuseLogged",
        "fingerprint": dict(fingerprint or {}),
        "solver": solver,
        "source_receipt": source_receipt,
    })


def judge_sample(
    folder_context: str,
    *,
    reuse_id: str,
    actor: str,
    agreed: bool,
    rationale: str = "",
    log_root: Optional[str] = None,
) -> str:
    """A human re-judges a sampled reuse: did they AGREE with the reused
    decision? Independent ground truth. Fail-closed — needs a named actor; a
    disagreement needs a rationale; an agent or non-active party cannot supply
    ground truth (its 'judgment' would just echo the automation)."""
    if not (actor or "").strip():
        raise ValueError("a sample judgment needs a named human actor")
    if not agreed and not (rationale or "").strip():
        raise ValueError("a disagreement needs a written rationale")
    from .parties import list_parties
    known = {p["party_id"]: p for p in
             list_parties(folder_context, log_root=log_root)["parties"]}
    party = known.get(actor)
    if party is not None:
        if party.get("party_kind") == "agent":
            raise ValueError(
                f"actor '{actor}' is an AGENT — it cannot supply the "
                "independent ground truth a sample judgment requires")
        if party.get("status") != "active":
            raise ValueError(f"actor '{actor}' is {party.get('status')} — "
                             "only an active human can judge a sample")
    return _append(folder_context, actor, log_root, {
        "kind": "SampleJudged",
        "reuse_id": reuse_id,
        "agreed": bool(agreed),
        "rationale": (rationale or "")[:500],
    })


def calibration_report(
    folder_context: str,
    *,
    sampling_floor: float = 0.05,
    decay_threshold: float = 0.2,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Replay projection over reuse + sample-judgment events. Reports realised
    reuse, the sampling rate, and — the load-bearing number — the DISAGREEMENT
    rate on sampled reuses. Flags under-sampling (can't tell) or decay (humans
    disagree with reused decisions). Responsible only when adequately sampled
    AND not decaying."""
    log = MutationLog(folder_context, log_root=log_root)
    reuse_ids: set[str] = set()
    judged: dict[str, bool] = {}        # reuse_id -> agreed (latest wins)
    for evt in log.replay():
        extra = evt.extra or {}
        k = extra.get("kind")
        if k == "ReuseLogged":
            reuse_ids.add(evt.audit_id)
        elif k == "SampleJudged":
            rid = extra.get("reuse_id", "")
            if rid:
                judged[rid] = bool(extra.get("agreed", True))

    reuse_count = len(reuse_ids)
    sampled = len([r for r in judged if r in reuse_ids])
    disagreements = len([1 for r, ok in judged.items()
                         if r in reuse_ids and not ok])
    sampling_rate = round(sampled / reuse_count, 4) if reuse_count else 1.0
    disagreement_rate = round(disagreements / sampled, 4) if sampled else 0.0

    flag = None
    if reuse_count and sampling_rate < sampling_floor:
        flag = "under-sampled"
    elif disagreement_rate >= decay_threshold:
        flag = "calibration-decay"

    return {
        "reuse_count": reuse_count,
        "sampled": sampled,
        "sampling_rate": sampling_rate,
        "disagreement_rate": disagreement_rate,
        "flag": flag,
        "responsible": flag is None,
        "basis": "realised reuse + independent human ground truth on samples "
                 "(disagreement = quality, not activity)",
    }
