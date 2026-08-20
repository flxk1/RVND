# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Drift → Breaker (L2 evaluator) + dispatch-record writer."""

import tempfile
from pathlib import Path

from workspaces.drift_monitor import DriftReport
from workspaces.oversight_drift import (
    drift_tripwire, raise_floor, evaluate as evaluate_drift)
from workspaces.breaker import Breaker, Lease, BreakerState
from workspaces.oversight_dispatch import dispatch, record_decision_return
from workspaces.mutation_log import MutationLog


# ── drift → signal ───────────────────────────────────────────────────────────

def _report(structural=None, behavioural=None, too_thin=False,
            no_baseline=False, window_n=50):
    return DriftReport(folder="/f", as_of=1.0,
                       structural=structural or [], behavioural=behavioural or [],
                       too_thin=too_thin, no_baseline=no_baseline,
                       window_n=window_n)


def test_structural_drift_arms_breaker():
    sig = evaluate_drift(_report(structural=[{"metric": "catalogue"}]))
    assert sig.structural is True
    assert sig.metrics["drift_structural"] is True
    assert "stale" in sig.reason


def test_behavioural_drift_raises_floor_not_freeze():
    sig = evaluate_drift(_report(behavioural=[{"metric": "channel:web"}]))
    assert sig.structural is False
    assert sig.recommend_floor == "REVIEW"
    assert sig.metrics["drift_structural"] is False


def test_no_drift_clean():
    sig = evaluate_drift(_report())
    assert not sig.structural and not sig.recommend_floor
    assert sig.reason == "no drift"


def test_thin_window_is_gap_not_breach():
    sig = evaluate_drift(_report(too_thin=True, window_n=3))
    assert sig.needs_rebaseline is True
    assert sig.structural is False


def test_no_baseline_needs_rebaseline():
    sig = evaluate_drift(_report(no_baseline=True))
    assert sig.needs_rebaseline is True


def test_structural_signal_quarantines_breaker():
    # the wire: drift signal metrics → breaker tripwire → quarantine
    b = Breaker(Lease("bot", "L3", expires_at=1000.0),
                tripwires=[drift_tripwire()])
    sig = evaluate_drift(_report(structural=[{"metric": "policy"}]))
    s = b.status(metrics=sig.metrics, now=990.0)
    assert s.state is BreakerState.QUARANTINED
    assert s.effective_grade == "L0"


def test_clean_drift_does_not_quarantine():
    b = Breaker(Lease("bot", "L3", expires_at=1000.0),
                tripwires=[drift_tripwire()])
    sig = evaluate_drift(_report())
    s = b.status(metrics=sig.metrics, now=990.0)
    assert s.state is BreakerState.RUNNING


def test_raise_floor_joins_stricter():
    assert raise_floor("NOTIFY", "REVIEW") == "REVIEW"
    assert raise_floor("APPROVE", "REVIEW") == "APPROVE"
    assert raise_floor("", "REVIEW") == "REVIEW"
    assert raise_floor("NOTIFY", "") == "NOTIFY"


# ── dispatch record ──────────────────────────────────────────────────────────

def _ratify_payload():
    return {"render": "ratify", "action": "pay", "agent": "bot",
            "link": "workspace://s/1", "grounds": []}


def _residual_payload(n_opts=2, link="workspace://s/2"):
    return {"render": "options", "action": "erase", "agent": "bot",
            "link": link, "options": [{"id": str(i)} for i in range(n_opts)]}


def test_dispatch_ratify_writes_record():
    with tempfile.TemporaryDirectory() as d:
        res = dispatch(_ratify_payload(), folder=d, channel="jira",
                       recipient="alice", log_root=d)
        assert res.ok and res.dispatch_id.startswith("dispatch:")
        log = MutationLog(Path(d), log_root=Path(d))
        assert any(e.pair_id == res.dispatch_id for e in log.replay())


def test_dispatch_residual_needs_two_options():
    with tempfile.TemporaryDirectory() as d:
        res = dispatch(_residual_payload(n_opts=1), folder=d, channel="jira",
                       log_root=d)
        assert not res.ok
        assert "≥2 options" in res.error


def test_dispatch_residual_needs_link():
    with tempfile.TemporaryDirectory() as d:
        res = dispatch(_residual_payload(link=""), folder=d, channel="email",
                       log_root=d)
        assert not res.ok
        assert "decision surface" in res.error


def test_dispatch_residual_ok_with_options_and_link():
    with tempfile.TemporaryDirectory() as d:
        res = dispatch(_residual_payload(), folder=d, channel="webhook",
                       log_root=d)
        assert res.ok


def test_decision_return_requires_surface_reference():
    with tempfile.TemporaryDirectory() as d:
        bad = record_decision_return(folder=d, dispatch_id="dispatch:x",
                                     surface_audit_id="", actor="alice",
                                     log_root=d)
        assert "error" in bad
        ok = record_decision_return(folder=d, dispatch_id="dispatch:x",
                                    surface_audit_id="srf:123",
                                    chosen_option_id="a", actor="alice",
                                    log_root=d)
        assert "audit_id" in ok


def test_decision_return_requires_actor():
    with tempfile.TemporaryDirectory() as d:
        bad = record_decision_return(folder=d, dispatch_id="d",
                                     surface_audit_id="srf:1", actor="",
                                     log_root=d)
        assert "error" in bad
