# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Step contracts — resolving risk and hardening into a governed step.

A step contract sits on a node and decides, in advance, how much the agent may
do alone (the granted autonomy grade L0-L4), what it must satisfy first (the
requirement debt), and what happens if a human does not respond inside the
override window (proceed or halt). It is DERIVED from the fingerprint of
earlier approved similar steps: a long, low-disagreement track record hardens
the process and earns autonomy; a new or contested step stays cautious.

The dial has two opposing forces, with hard invariants:
  * risk up   -> autonomy down, requirement debt up;
  * hardening up -> autonomy up;
  * a risk FLOOR: critical risk caps autonomy at a human-gated grade however
    hardened (you cannot auto-publish a critical step);
  * auditability is INVARIANT (True at every grade) — autonomy buys silence
    from humans, never from the audit chain.

Composes with the control-form algebra and the autonomy grades; this is where
risk, hardening, the timed override and the debt resolve into one step.
Deterministic; no model in the loop.

NOTE: this is the canonical Loomground module. If it moves into
loomground_core, this file should become a thin compatibility shim.
"""
from __future__ import annotations

from typing import Any

#: risk levels, ascending. Index is the risk rank.
RISK_LEVELS = ("low", "medium", "high", "critical")
_RANK = {r: i for i, r in enumerate(RISK_LEVELS)}

#: the highest autonomy grade (L0..L4) each risk may ever reach — the floor.
_RISK_CAP = {"low": 4, "medium": 4, "high": 2, "critical": 1}


def risk_grade_cap(risk: str) -> int:
    """The autonomy-grade ceiling (0..4) for a risk level — the SINGLE SOURCE the
    UI renders for the M8 ceiling fader. The client must consume this, never
    recompute it (M8/E3: the server composes the ceiling, the client renders it).
    An unknown risk caps to 0 (most restrictive, fail-safe)."""
    return _RISK_CAP.get(risk, 0)

#: requirement debt the agent must satisfy, ascending with risk (cumulative).
_REQUIREMENTS = {
    "low":      ["audit-log"],
    "medium":   ["audit-log", "single-approver"],
    "high":     ["audit-log", "four-eyes", "evidence-pack"],
    "critical": ["audit-log", "four-eyes", "expert-review", "reserved-act"],
}


def _hardening(prior_approvals: int, disagreement_rate: float) -> float:
    """0..1 process-hardening score from precedent: approvals build it,
    disagreement (calibration decay) erodes it."""
    approvals = max(0, int(prior_approvals))
    dis = min(1.0, max(0.0, float(disagreement_rate)))
    base = min(1.0, 0.1 * approvals)          # ~10 clean approvals → fully built
    return round(base * (1.0 - dis), 4)


def derive_contract(
    risk: str,
    *,
    prior_approvals: int = 0,
    disagreement_rate: float = 0.0,
    override_window_seconds: int = 0,
) -> dict[str, Any]:
    """Derive a step contract from a risk level and the fingerprint's
    precedent (prior approvals + disagreement). Returns the granted grade,
    the requirement debt, the timed-override behaviour, and the (invariant)
    auditability."""
    if risk not in _RANK:
        raise ValueError(f"unknown risk {risk!r}; valid: {list(RISK_LEVELS)}")

    hard = _hardening(prior_approvals, disagreement_rate)
    rank = _RANK[risk]
    cap = _RISK_CAP[risk]

    # autonomy earned by hardening (0..4), reduced by risk, floored by the cap.
    base_grade = round(hard * 4)
    grade = max(0, min(base_grade - rank, cap))

    requirements = list(_REQUIREMENTS[risk])
    on_timeout = "proceed" if risk == "low" else "halt"

    return {
        "risk": risk,
        "hardening": hard,
        "grade": grade,                  # granted autonomy L0..L4
        "requirements": requirements,    # the agent's debt
        "debt": len(requirements),
        "on_timeout": on_timeout,        # proceed (fail-open) | halt (fail-closed)
        "override_window_seconds": max(0, int(override_window_seconds)),
        "auditable": True,               # invariant — never bought down
    }
