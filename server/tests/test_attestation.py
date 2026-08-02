# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Behavioral attestation — producer of the Breaker's integrity flag.

Pins the §4.5 rule and the coupling to the Breaker:
- behaviour matching the baseline passes;
- behaviour changed with NO admitted-learning event = unlogged learning, fails;
- behaviour changed WITH admitted events = governed drift, does not fail;
- a probe with no observation is a coverage gap, not a failure;
- a failed attestation, fed through breaker_metrics, actually quarantines;
- green_checks refuses lease renewal when attestation/chain/budget is not green.
"""

import pytest

from workspaces.attestation.core import (
    EXPLAINED_DRIFT,
    PASS,
    UNLOGGED_LEARNING,
    Probe,
    attest,
    breaker_metrics,
    green_checks,
    signature,
)
from workspaces.breaker import Breaker, BreakerState, Lease, default_tripwires


def _gold():
    return [Probe("p1", "sigA"), Probe("p2", "sigB"), Probe("p3", "sigC")]


def _lease(grade="L3", expires=1000.0, ttl=60.0, granted=940.0):
    return Lease(agent="bot", granted_grade=grade, expires_at=expires,
                 ttl_seconds=ttl, granted_at=granted)


# ── signature helper ─────────────────────────────────────────────────────────

def test_signature_is_stable_and_normalising():
    assert signature("Hello   World") == signature("hello world")
    assert signature("a") != signature("b")


# ── attest verdicts ──────────────────────────────────────────────────────────

def test_unchanged_behaviour_passes():
    observed = {"p1": "sigA", "p2": "sigB", "p3": "sigC"}
    r = attest(observed, _gold(), admitted_learning_events=0)
    assert r.verdict == PASS
    assert r.attestation_failed is False
    assert r.diverged == []


def test_change_without_admitted_event_is_unlogged_learning():
    observed = {"p1": "sigA", "p2": "MUTATED", "p3": "sigC"}
    r = attest(observed, _gold(), admitted_learning_events=0)
    assert r.verdict == UNLOGGED_LEARNING
    assert r.attestation_failed is True
    assert r.diverged == ["p2"]


def test_change_with_admitted_event_is_explained_drift():
    observed = {"p1": "sigA", "p2": "MUTATED", "p3": "sigC"}
    r = attest(observed, _gold(), admitted_learning_events=1)
    assert r.verdict == EXPLAINED_DRIFT
    assert r.attestation_failed is False
    assert "review" in r.reason.lower()


def test_unobserved_probe_is_a_gap_not_a_failure():
    observed = {"p1": "sigA", "p3": "sigC"}  # p2 missing
    r = attest(observed, _gold(), admitted_learning_events=0)
    assert r.verdict == PASS
    assert r.attestation_failed is False
    assert r.unobserved == ["p2"]


def test_tolerance_allows_n_diverged():
    observed = {"p1": "X", "p2": "sigB", "p3": "sigC"}
    assert attest(observed, _gold(), admitted_learning_events=0,
                  tolerance=1).verdict == PASS
    observed2 = {"p1": "X", "p2": "Y", "p3": "sigC"}
    assert attest(observed2, _gold(), admitted_learning_events=0,
                  tolerance=1).attestation_failed is True


def test_negative_inputs_rejected():
    with pytest.raises(ValueError):
        attest({}, _gold(), admitted_learning_events=-1)
    with pytest.raises(ValueError):
        attest({}, _gold(), admitted_learning_events=0, tolerance=-1)


# ── coupling to the Breaker (the part that isolates a subverted agent) ────────

def test_failed_attestation_quarantines_via_breaker():
    observed = {"p1": "sigA", "p2": "MUTATED", "p3": "sigC"}
    r = attest(observed, _gold(), admitted_learning_events=0)
    metrics = breaker_metrics(attestation=r, chain_valid=True)
    b = Breaker(_lease(), tripwires=default_tripwires())
    s = b.status(metrics={**metrics, "usd_spent_iteration": 0.0,
                          "error_rate": 0.0}, now=990.0)
    assert s.state is BreakerState.QUARANTINED
    assert s.effective_grade == "L0"


def test_clean_attestation_keeps_breaker_running():
    observed = {"p1": "sigA", "p2": "sigB", "p3": "sigC"}
    r = attest(observed, _gold(), admitted_learning_events=0)
    metrics = breaker_metrics(attestation=r, chain_valid=True,
                              usd_spent_iteration=0.0, error_rate=0.0)
    b = Breaker(_lease(), tripwires=default_tripwires())
    s = b.status(metrics=metrics, now=990.0)
    assert s.state is BreakerState.RUNNING
    assert s.effective_grade == "L3"


def test_chain_invalid_propagates_to_breaker():
    r = attest({"p1": "sigA", "p2": "sigB", "p3": "sigC"}, _gold(),
               admitted_learning_events=0)
    metrics = breaker_metrics(attestation=r, chain_valid=False,
                              usd_spent_iteration=0.0, error_rate=0.0)
    b = Breaker(_lease(), tripwires=default_tripwires())
    s = b.status(metrics=metrics, now=990.0)
    assert s.state is BreakerState.QUARANTINED


# ── green checks gate lease renewal (lease-renewal-on-green) ──────────────────

def test_green_checks_pass_allows_renewal():
    r = attest({"p1": "sigA", "p2": "sigB", "p3": "sigC"}, _gold(),
               admitted_learning_events=0)
    g = green_checks(attestation=r, chain_valid=True, budget_ok=True)
    assert g.ok is True
    lease = _lease(expires=1000.0)
    res = lease.renew(ok=g.ok, reason=g.reason, now=990.0)
    assert res.renewed is True


def test_failed_attestation_refuses_renewal_and_lets_lease_lapse():
    observed = {"p1": "sigA", "p2": "MUTATED", "p3": "sigC"}
    r = attest(observed, _gold(), admitted_learning_events=0)
    g = green_checks(attestation=r, chain_valid=True)
    assert g.ok is False
    lease = _lease(expires=1000.0)
    res = lease.renew(ok=g.ok, reason=g.reason, now=990.0)
    assert res.renewed is False
    assert lease.effective_grade(now=1001.0) == "L0"  # decays, dead-man's switch


def test_budget_or_chain_failure_blocks_green():
    r = attest({"p1": "sigA", "p2": "sigB", "p3": "sigC"}, _gold(),
               admitted_learning_events=0)
    assert green_checks(attestation=r, chain_valid=False).ok is False
    assert green_checks(attestation=r, chain_valid=True, budget_ok=False).ok is False
