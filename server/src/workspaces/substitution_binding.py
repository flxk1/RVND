# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Is the attested posture still standing for measured mediation?

``enforcement_posture_binding`` attests which controls are in force.
``reconciliation_binding`` measures, from two ledgers that must agree, how many
observed effects had no authorisation behind them. A governance report that
shows a hardening posture and calls that improved mediation has substituted the
first for the second without checking.

This projects both over two consecutive windows and hands the movements to
``loomground-proxy`` through :mod:`workspaces.adapters.proxy`. The consequential
finding is ``gamed``: the posture hardened while the measured mediation gap
grew — controls went on and unauthorised effects went up, so whatever the
posture tracks, it is not mediation.

Pure projection — reads chain events only, no clock, no environment; window
bounds arrive as parameters, mirroring both sibling bindings. Consume-not-regrow:
the substitution logic lives upstream in ``loomground_proxy``; this module is
reader + adapter only.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .reconciliation_binding import reconcile_projection

__all__ = ["substitution_projection"]


def _rate(block: dict[str, Any]) -> Optional[float]:
    """The measured mediation gap, or None when the window was never read.

    ``unavailable`` (package absent) and ``UNRECONCILED`` (nobody looked) both
    mean no reading was taken. Returning 0.0 for either would be the exact
    error the plane exists to catch — an unchecked proxy recorded as a healthy
    one.
    """
    if block.get("status") in (None, "unavailable", "UNRECONCILED", "unreconciled"):
        return None
    rate = block.get("unauthorised_rate")
    return float(rate) if isinstance(rate, (int, float)) else None


def substitution_projection(
    events: Iterable,
    *,
    prior_since_ts: float,
    prior_until_ts: float,
    since_ts: float,
    until_ts: float,
    posture_change: Optional[str] = None,
) -> dict[str, Any]:
    """Check the posture→mediation substitution across two windows.

    ``posture_change`` is the value of an ``enforcement_posture.Change`` for the
    same span (``compare(before, after)``). Omitted, it reads UNMEASURED and the
    substitution reports ``unchecked`` — which is the honest answer when nobody
    compared the postures, not a pass.
    """
    try:
        from .adapters.proxy import check_posture_substitution
    except Exception:                                   # noqa: BLE001
        return {"status": "unavailable",
                "detail": "loomground-proxy not installed"}

    events = list(events)
    prior = reconcile_projection(events, since_ts=prior_since_ts, until_ts=prior_until_ts)
    current = reconcile_projection(events, since_ts=since_ts, until_ts=until_ts)

    substitutions = check_posture_substitution(
        posture_change=posture_change,
        unauthorised_before=_rate(prior),
        unauthorised_after=_rate(current),
    )
    return {
        "status": "checked",
        "substitutions": [s.to_dict() for s in substitutions],
        "kinds": [s.kind for s in substitutions],
        "readings": {
            "unauthorised_rate_before": _rate(prior),
            "unauthorised_rate_after": _rate(current),
            "posture_change": posture_change,
        },
    }
