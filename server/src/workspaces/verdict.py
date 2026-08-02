# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The one three-state decision vocabulary every Workspace surface speaks.

Permit / Hold / Deny — "act · pause for a human · forbid" — defined once here,
not re-spelled in each surface. The gate, the policy matrix, and the Lens keep
their own public words (``GO/CONDITIONAL/NO-GO``, ``go/ask/block``,
``admit/hold/reject``); this module is the canonical type they all map to, plus
the single ``strictest()`` compose rule (default-deny, strictest-wins).

Separation of concerns — this module changes none of it:
  * **Policy declares** what is allowed: the policy matrix (autonomy × oversight).
  * **Oversight workflows follow** the policy: the action gate, the workflow
    runner, the Lens admission. They read policy and enforce it — they never
    redefine it.
This is only the shared vocabulary + compose both layers use. It moves no policy
into the workflow and no workflow into the policy.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable


class Verdict(str, Enum):
    PERMIT = "permit"   # act
    HOLD = "hold"       # pause for a human
    DENY = "deny"       # forbid


# severity order — higher = stricter. The only ordering in the system.
_SEV = {Verdict.PERMIT: 0, Verdict.HOLD: 1, Verdict.DENY: 2}

# Each surface's public strings ↔ the canonical tri-state. Surfaces keep their
# own words; these are the only translation tables.
GATE = {"GO": Verdict.PERMIT, "CONDITIONAL": Verdict.HOLD, "NO-GO": Verdict.DENY}
LIGHT = {"go": Verdict.PERMIT, "ask": Verdict.HOLD, "block": Verdict.DENY}
ADMISSION = {"admit": Verdict.PERMIT, "hold": Verdict.HOLD, "reject": Verdict.DENY}
# Workspace Lock keeps its own 4-value egress vocabulary; this is the only place it
# maps onto the canonical tri-state (so a lock decision can ride the signed chain
# in the same words as everything else). "minimise" = acted-with-redaction = permit.
LOCK = {"allow": Verdict.PERMIT, "minimise": Verdict.PERMIT,
        "ask_user": Verdict.HOLD, "refuse": Verdict.DENY}

_TO_GATE = {v: k for k, v in GATE.items()}
_TO_LIGHT = {v: k for k, v in LIGHT.items()}
_TO_ADMISSION = {v: k for k, v in ADMISSION.items()}


def severity(v: Verdict) -> int:
    return _SEV[v]


def strictest(*verdicts: Verdict) -> Verdict:
    """The strictest of the given verdicts (default-deny composition). Empty →
    PERMIT (nothing constrains)."""
    vs = [v for v in verdicts if v is not None]
    if not vs:
        return Verdict.PERMIT
    return max(vs, key=lambda v: _SEV[v])


def strictest_of(verdicts: Iterable[Verdict]) -> Verdict:
    return strictest(*list(verdicts))


# ── boundary mappers (surface word → canonical, and back) ───────────────────
def from_gate(s: str | None) -> Verdict:
    # Absent verdict = no constraint (PERMIT, composes harmlessly via strictest).
    # An UNRECOGNISED verdict string is anomalous → fail-safe to DENY, never the
    # old fail-OPEN default of PERMIT (M1, Oversight + Lock panels).
    if not s:
        return Verdict.PERMIT
    return GATE.get(s.upper(), Verdict.DENY)


def from_light(s: str | None) -> Verdict:
    if not s:
        return Verdict.PERMIT
    return LIGHT.get(s.lower(), Verdict.DENY)


def from_admission(s: str | None) -> Verdict:
    return ADMISSION.get((s or "").lower(), Verdict.HOLD)   # admission default-deny → hold


def from_lock(s: str | None) -> Verdict:
    return LOCK.get((s or "").lower(), Verdict.HOLD)        # unknown lock action → hold


# A federated THIRD-PARTY governance tool reports a neutral risk-tier (the bus, B′).
# This is the only translation of an external tool's word into the canonical tri-state;
# the BINDING (which tier holds/denies) is policy the registry authors — Rvnd asserts
# none of it. Default-DENY on an unrecognised tier (fail-safe), like from_gate.
RISK_TIER = {
    "pass": Verdict.PERMIT, "ok": Verdict.PERMIT, "clear": Verdict.PERMIT, "low": Verdict.PERMIT,
    "review": Verdict.HOLD, "medium": Verdict.HOLD, "high": Verdict.HOLD, "flag": Verdict.HOLD,
    "fail": Verdict.DENY, "critical": Verdict.DENY, "block": Verdict.DENY, "reject": Verdict.DENY,
}


def from_risk_tier(s: str | None) -> Verdict:
    if not s:
        return Verdict.PERMIT                              # no finding = no constraint
    return RISK_TIER.get(s.strip().lower(), Verdict.DENY)  # unknown tier → fail-safe DENY


def coerce(s: str | None, *, default: Verdict = Verdict.DENY) -> Verdict:
    """Parse a CANONICAL verdict string (permit/hold/deny) read back from the signed
    log or a caller. A corrupt/tampered/unknown value never crashes and never
    fail-opens — it becomes ``default`` (DENY, fail-safe). Use this for any Verdict
    reconstructed from stored or external data, never the bare ``Verdict(...)``."""
    try:
        return Verdict((s or "").strip().lower())
    except (ValueError, AttributeError):
        return default


def to_gate(v: Verdict) -> str:
    return _TO_GATE[v]


def to_light(v: Verdict) -> str:
    return _TO_LIGHT[v]


def to_admission(v: Verdict) -> str:
    return _TO_ADMISSION[v]
