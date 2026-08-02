# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The Breaker (USP 3) — leases, tripwires, quarantine.

Pins the inversion: stopping is default, running needs a live lease.
- a lapsed lease decays to L0 with no human action (dead-man's switch);
- a refused renewal does not extend; a successful one does;
- a tripped tripwire quarantines (L0) and a renewal cannot lift it;
- only a named human with a rationale can clear a quarantine;
- the effective grade is the single coupling to the action gate.
"""

import pytest

from workspaces.breaker import (
    Breaker, BreakerState, Lease, Tripwire, cap_grade, default_tripwires)


def _lease(grade="L3", expires=1000.0, ttl=60.0, granted=940.0):
    return Lease(agent="bot", granted_grade=grade, expires_at=expires,
                 ttl_seconds=ttl, granted_at=granted)


# ── Lease decay ──────────────────────────────────────────────────────────────

def test_live_lease_grants_its_grade():
    lease = _lease()
    assert lease.effective_grade(now=990.0) == "L3"


def test_lapsed_lease_decays_to_l0_no_action():
    lease = _lease(expires=1000.0)
    assert lease.effective_grade(now=1001.0) == "L0"


def test_refused_renewal_does_not_extend():
    lease = _lease(expires=1000.0)
    r = lease.renew(ok=False, reason="budget exceeded", now=990.0)
    assert r.renewed is False
    assert lease.expires_at == 1000.0
    assert lease.effective_grade(now=1001.0) == "L0"


def test_successful_renewal_extends():
    lease = _lease(expires=1000.0, ttl=60.0)
    r = lease.renew(ok=True, now=990.0)
    assert r.renewed is True
    assert lease.expires_at == 1050.0
    assert lease.effective_grade(now=1040.0) == "L3"


def test_bad_grade_and_ttl_rejected_at_write():
    with pytest.raises(ValueError):
        Lease("bot", "L9", 1.0)
    with pytest.raises(ValueError):
        Lease("bot", "L3", 1.0, ttl_seconds=0)


# ── Tripwires ────────────────────────────────────────────────────────────────

def test_tripwire_max_min_flag():
    assert Tripwire("e", "error_rate", 0.2, "max").trips(0.3)
    assert not Tripwire("e", "error_rate", 0.2, "max").trips(0.1)
    assert Tripwire("h", "queue_health", 0.5, "min").trips(0.4)
    assert Tripwire("a", "attestation_failed", 0.0, "flag").trips(True)


def test_unmeasured_metric_does_not_trip():
    assert Tripwire("e", "error_rate", 0.2, "max").trips(None) is False


# ── Breaker composition ──────────────────────────────────────────────────────

def test_running_when_lease_live_and_clean():
    b = Breaker(_lease(), tripwires=[])
    s = b.status(now=990.0)
    assert s.state is BreakerState.RUNNING
    assert s.effective_grade == "L3"
    assert s.running


def test_decayed_when_lease_lapsed():
    b = Breaker(_lease(expires=1000.0), tripwires=[])
    s = b.status(now=1001.0)
    assert s.state is BreakerState.DECAYED
    assert s.effective_grade == "L0"


def test_tripwire_quarantines_even_with_live_lease():
    tw = [Tripwire("error_rate", "error_rate", 0.25, "max")]
    b = Breaker(_lease(), tripwires=tw)
    s = b.status(metrics={"error_rate": 0.4}, now=990.0)
    assert s.state is BreakerState.QUARANTINED
    assert s.effective_grade == "L0"
    assert s.tripped


def test_quarantine_is_sticky_until_cleared():
    tw = [Tripwire("attestation", "attestation_failed", 0.0, "flag")]
    b = Breaker(_lease(), tripwires=tw)
    b.status(metrics={"attestation_failed": True}, now=990.0)
    # metric back to clean — still quarantined
    s2 = b.status(metrics={"attestation_failed": False}, now=991.0)
    assert s2.state is BreakerState.QUARANTINED


def test_renewal_cannot_lift_quarantine():
    tw = [Tripwire("chain", "chain_invalid", 0.0, "flag")]
    b = Breaker(_lease(), tripwires=tw)
    b.status(metrics={"chain_invalid": True}, now=990.0)
    r = b.renew(ok=True, now=991.0)
    assert r.renewed is False
    assert "quarantine" in r.reason.lower()


def test_clear_requires_actor_and_rationale():
    tw = [Tripwire("chain", "chain_invalid", 0.0, "flag")]
    b = Breaker(_lease(), tripwires=tw)
    b.status(metrics={"chain_invalid": True}, now=990.0)
    assert "error" in b.clear(by="", rationale="x")
    assert "error" in b.clear(by="alice", rationale="")
    ok = b.clear(by="alice", rationale="root cause fixed, hash rebuilt")
    assert ok["cleared"] is True
    # after clear + renew, runs again
    b.renew(ok=True, now=991.0)
    s = b.status(metrics={"chain_invalid": False}, now=992.0)
    assert s.state is BreakerState.RUNNING


def test_effective_grade_is_the_gate_coupling():
    b = Breaker(_lease(grade="L4", expires=1000.0), tripwires=[])
    assert b.effective_grade(now=990.0) == "L4"
    assert b.effective_grade(now=1001.0) == "L0"


def test_dead_mans_switch_sequence():
    # Agent runs at L3, misses two renewals, decays; a clean renewal restores.
    b = Breaker(_lease(grade="L3", expires=1000.0, ttl=60.0), tripwires=[])
    assert b.status(now=990.0).effective_grade == "L3"     # live
    assert b.status(now=1001.0).effective_grade == "L0"    # lapsed → stop
    b.renew(ok=True, now=1001.0)                            # human/system back
    assert b.status(now=1010.0).effective_grade == "L3"    # restored


# ── grade lattice meet ───────────────────────────────────────────────────────

def test_cap_grade_meets_lower():
    assert cap_grade("L4", "L2") == "L2"
    assert cap_grade("L1", "L3") == "L1"
    assert cap_grade("L3", "L3") == "L3"


def test_default_tripwires_cover_integrity():
    metrics = {tw.metric for tw in default_tripwires()}
    assert "attestation_failed" in metrics
    assert "chain_invalid" in metrics
