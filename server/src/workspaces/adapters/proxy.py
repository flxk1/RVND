# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND adapter seam over loomground-proxy — declared substitutions checked
against two readings; internal by design.

RVND attests an enforcement **posture** (which controls are in force) and
separately **measures** complete mediation by reconciling the authorisation
ledger against the effect ledger. The posture is routinely read as if it stood
for mediation — "the controls are on, therefore effects were gated". That
reading is a substitution, and nothing checked it.

The plane checks it. Given the movement of the metric and the movement of the
thing it is declared to stand for, it reports one of four kinds:

  * ``gamed`` — posture hardened while mediation got worse. The controls went
    on and unauthorised effects went up: whatever the posture is tracking, it
    is not mediation.
  * ``misleading`` — posture weakened while mediation improved; the
    substitution itself is what is in question, not the readings.
  * ``unchecked`` — one side was not read, so the other is no evidence about it.
  * ``tracking`` — they moved together.

This module owns the translation and takes plain values rather than RVND types,
so the seam stays thin and acyclic. The declaration itself lives here because it
is the claim a reviewer should argue with — not the readings.
"""
from __future__ import annotations

from typing import Optional, Sequence

from loomground_proxy import Movement, Proxy, Substitution, check_proxies

__all__ = [
    "Movement", "Proxy", "Substitution", "check_proxies",
    "POSTURE_STANDS_FOR_MEDIATION",
    "movement_from_change", "movement_from_unauthorised_rate",
    "check_posture_substitution",
]

#: The substitution RVND actually makes. `ref` points at where it is decided,
#: because that is the arguable part.
POSTURE_STANDS_FOR_MEDIATION = Proxy(
    metric="enforcement_posture",
    stands_for="complete_mediation",
    ref="workspaces.enforcement_posture_binding.effective_posture",
)


def movement_from_change(change: Optional[str]) -> Movement:
    """Map an ``enforcement_posture.Change`` onto a reading.

    ``INCOMPARABLE`` becomes UNMEASURED, not UNCHANGED: two postures with a
    polarity conflict have no ordering, so there is no direction to report, and
    calling that "held still" would invent a reading nobody took.
    """
    if change is None:
        return Movement.UNMEASURED
    value = str(getattr(change, "value", change)).lower()
    if value == "hardened":
        return Movement.IMPROVED
    if value == "weakened":
        return Movement.WORSENED
    if value == "unchanged":
        return Movement.UNCHANGED
    return Movement.UNMEASURED


def movement_from_unauthorised_rate(before: Optional[float],
                                    after: Optional[float]) -> Movement:
    """Map two mediation readings onto a movement.

    The rate is a *gap* measure — share of observed effects with no
    authorisation behind them — so a falling rate is an improvement. Either
    side absent (the window was never reconciled, or the package was missing
    and the block degraded to ``unavailable``) is UNMEASURED: an unreconciled
    window is not a clean one.
    """
    if before is None or after is None:
        return Movement.UNMEASURED
    if after < before:
        return Movement.IMPROVED
    if after > before:
        return Movement.WORSENED
    return Movement.UNCHANGED


def check_posture_substitution(
    *,
    posture_change: Optional[str],
    unauthorised_before: Optional[float],
    unauthorised_after: Optional[float],
) -> Sequence[Substitution]:
    """Does the attested posture still stand for measured mediation?"""
    readings = {
        "enforcement_posture": movement_from_change(posture_change),
        "complete_mediation": movement_from_unauthorised_rate(
            unauthorised_before, unauthorised_after),
    }
    return check_proxies([POSTURE_STANDS_FOR_MEDIATION], readings)
