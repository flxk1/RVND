# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for oversight_drift — the L2 evaluator that turns a DriftReport into a
DriftSignal (Breaker metrics / recommended floor). Grounding: Conant & Ashby
1970 (a regulator that is no longer a model of its system must be re-gated)."""

from __future__ import annotations

from workspaces.drift_monitor import DriftReport
from workspaces.oversight_drift import evaluate, raise_floor, drift_tripwire


def _report(**kw) -> DriftReport:
    base = dict(folder="/x", as_of=0.0)
    base.update(kw)
    return DriftReport(**base)


def test_no_baseline_asks_for_rebaseline_never_trips():
    sig = evaluate(_report(no_baseline=True))
    assert sig.needs_rebaseline is True
    assert sig.structural is False               # a gap is not a breach
    assert sig.recommend_floor == ""


def test_thin_window_asks_for_rebaseline_never_trips():
    sig = evaluate(_report(too_thin=True, window_n=3))
    assert sig.needs_rebaseline is True
    assert sig.structural is False


def test_structural_drift_arms_the_breaker():
    sig = evaluate(_report(structural=[{"change": "tool added"}]))
    assert sig.structural is True
    assert sig.metrics == {"drift_structural": True}
    assert "quarantine" in sig.reason
    # the metric pairs with the tripwire the Breaker arms
    tw = drift_tripwire()
    assert tw.trips(sig.metrics["drift_structural"]) is True


def test_behavioural_drift_raises_floor_not_freeze():
    sig = evaluate(_report(behavioural=[{"shift": 0.4}]),
                   behavioural_floor="REVIEW")
    assert sig.structural is False               # not a freeze
    assert sig.recommend_floor == "REVIEW"
    assert sig.metrics["drift_structural"] is False


def test_structural_beats_behavioural():
    sig = evaluate(_report(structural=[{"c": 1}], behavioural=[{"s": 1}]))
    assert sig.structural is True
    assert sig.recommend_floor == ""             # structural path, no soft floor


def test_clean_report_no_drift():
    sig = evaluate(_report())
    assert sig.structural is False
    assert sig.recommend_floor == ""
    assert sig.reason == "no drift"


def test_drift_tripwire_does_not_trip_on_false():
    assert drift_tripwire().trips(False) is False


# --- raise_floor: stricter (higher-order) wins ---

def test_raise_floor_takes_the_stricter():
    assert raise_floor("NOTIFY", "REVIEW") == "REVIEW"     # recommended is stricter
    assert raise_floor("APPROVE", "REVIEW") == "APPROVE"   # current already stricter
    assert raise_floor("", "REVIEW") == "REVIEW"           # no current
    assert raise_floor("APPROVE", "") == "APPROVE"         # no recommendation
