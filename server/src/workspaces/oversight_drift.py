# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Drift monitor → Breaker: drift as the L2 evaluator.

The variety-conservation law (L2, algebra §4) holds only at the instant it is
checked. `drift_monitor` already detects when the regulator's model has gone
stale — the agent acquiring behaviour the footprint ontology no longer covers
(Conant & Ashby: a regulator that is no longer a model of its system). This
module is the missing wire: it turns a `DriftReport` into Breaker metrics, so a
drift finding *acts* — arming quarantine or raising oversight — instead of only
being recorded.

Two coupling strengths (the response is graduated, not binary):

  * **Structural drift** (tool catalogue / policy bindings changed) → the model
    no longer matches the system at all: arm the breaker (``drift_structural``
    flag) → QUARANTINE until re-baselined. This is the substantial-modification
    candidate (Art. 3(23)); it must not keep running on a stale model.
  * **Behavioural drift** (traffic-share shifts past threshold) → the model is
    drifting but not broken: raise the effective oversight floor and surface the
    finding, rather than freeze. Returned as a recommended floor, not a trip.

``too_thin`` / ``no_baseline`` never trip — an unmeasured window is a gap, not a
breach (same discipline as `Tripwire.trips(None)`), and is surfaced for a
re-baseline decision instead.

Pure stdlib; consumes a `DriftReport`, emits a `DriftSignal`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .drift_monitor import DriftReport
from .breaker import Tripwire
from .oversight_extractor import OVERSIGHT_LEVELS

_LEVEL_ORDER = {lvl: i for i, lvl in enumerate(OVERSIGHT_LEVELS)}


@dataclass
class DriftSignal:
    """The actionable read of a drift report for the Breaker / orchestrator."""
    structural: bool = False                # arm the breaker (quarantine)
    recommend_floor: str = ""               # raised oversight floor (behavioural)
    metrics: dict[str, Any] = field(default_factory=dict)  # → Breaker.status
    findings: list[dict] = field(default_factory=list)
    needs_rebaseline: bool = False          # thin window / no baseline
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"structural": self.structural,
                "recommend_floor": self.recommend_floor,
                "metrics": self.metrics, "findings": self.findings,
                "needs_rebaseline": self.needs_rebaseline, "reason": self.reason}


def drift_tripwire() -> Tripwire:
    """The tripwire the Breaker arms for structural drift. Pair it with the
    ``metrics`` from :func:`evaluate` (``{"drift_structural": True/False}``)."""
    return Tripwire("drift", "drift_structural", 0.0, "flag")


def evaluate(report: DriftReport,
             *,
             behavioural_floor: str = "REVIEW") -> DriftSignal:
    """Convert a DriftReport into a DriftSignal.

    ``behavioural_floor`` is the oversight floor recommended when behavioural
    (but not structural) drift is present — default REVIEW (surface, inspect),
    deliberately not a freeze."""
    if report.no_baseline:
        return DriftSignal(needs_rebaseline=True,
                           reason="no baseline — establish one before judging drift")
    if report.too_thin:
        return DriftSignal(needs_rebaseline=True,
                           reason=f"window too thin ({report.window_n} events) "
                                  "— gap, not breach; re-baseline or wait")

    structural = bool(report.structural)
    behavioural = bool(report.behavioural)
    sig = DriftSignal(
        structural=structural,
        metrics={"drift_structural": structural},
        findings=report.findings,
    )
    if structural:
        sig.reason = (f"structural drift ({len(report.structural)} change(s)) — "
                      "regulator model stale; breaker armed for quarantine")
    elif behavioural:
        sig.recommend_floor = behavioural_floor
        sig.reason = (f"behavioural drift ({len(report.behavioural)} share-shift(s)) "
                      f"— oversight floor raised to {behavioural_floor}")
    else:
        sig.reason = "no drift"
    return sig


def raise_floor(current: str, recommended: str) -> str:
    """Join two oversight floors — the stricter (higher-order) wins. Used to
    let a behavioural-drift signal lift an action's effective floor."""
    if not recommended:
        return current
    if not current:
        return recommended
    return (recommended if _LEVEL_ORDER[recommended] > _LEVEL_ORDER[current]
            else current)
