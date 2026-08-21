# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Oversight orchestrator — the embedded-engine entry point.

Proves the three layers compose into one verdict, and that the grade the gate
sees is capped by the Breaker and the ceiling, never trusted from the request.
"""

from rvnd.oversight import assess
from rvnd.action_gate import ActionRequest
from rvnd.breaker import Breaker, Lease, Tripwire


def _req(grade="L3", footprint=("financial",), action="pay-invoice", **kw):
    return ActionRequest(agent="bot", action_class=action,
                         autonomy_grade=grade, footprint=footprint, **kw)


def test_benign_action_goes_through():
    o = assess(_req(footprint=()))
    assert o.proceed and o.verdict == "GO"
    assert o.bundle is None


def test_flagged_action_needs_human_with_bundle():
    o = assess(_req())                       # financial, no standing approval
    assert o.needs_human and o.verdict == "CONDITIONAL"
    assert o.bundle is not None
    assert o.bundle.reversibility == "window"


def test_breaker_decay_caps_grade_to_l0_then_no_go():
    # L3 request, but the lease has lapsed → effective L0 → financial NO-GO.
    b = Breaker(Lease("bot", "L3", expires_at=1000.0), tripwires=[])
    o = assess(_req(), breaker=b, now=1001.0)
    assert o.effective_grade == "L0"
    assert o.breaker_state == "DECAYED"
    assert o.blocked and o.verdict == "NO-GO"
    assert "capped L3→L0" in o.reason


def test_breaker_live_lease_permits_normal_path():
    b = Breaker(Lease("bot", "L3", expires_at=1000.0), tripwires=[])
    o = assess(_req(), breaker=b, now=990.0)
    assert o.effective_grade == "L3"
    assert o.breaker_state == "RUNNING"
    assert o.needs_human               # financial still needs sign-off at L3


def test_tripwire_quarantine_forces_no_go():
    b = Breaker(Lease("bot", "L3", expires_at=1000.0),
                tripwires=[Tripwire("err", "error_rate", 0.25, "max")])
    o = assess(_req(), breaker=b, metrics={"error_rate": 0.9}, now=990.0)
    assert o.breaker_state == "QUARANTINED"
    assert o.effective_grade == "L0"
    assert o.blocked


def test_grade_ceiling_caps_below_request():
    # Request L4, ceiling L2 (from an OversightFacet) → gate sees L2.
    o = assess(_req(grade="L4", footprint=("personal-data",)),
               grade_ceiling="L2")
    assert o.effective_grade == "L2"


def test_standing_approval_makes_it_go():
    from rvnd.action_gate import StandingApproval
    appr = StandingApproval("bot", "pay-invoice", "pair:x")
    o = assess(_req(magnitude=50.0), standing_approvals=[appr])
    assert o.proceed
    assert o.decision.obligation_pairs == ["pair:x"]


def test_high_risk_bundle_carries_dossier():
    o = assess(_req(footprint=("personal-data",), action="loan-eligibility"),
               scope="credit-scoring",
               grounds=[{"id": "p", "authority_tier": 5, "verified": False}],
               dossier_material={"confidences": [0.55], "ood_percentile": 97.0})
    assert o.needs_human
    assert o.bundle.dossier is not None
    assert o.bundle.connector_payload()["doubt"]["ood_percentile"] == 97.0


def test_residual_options_make_origination_payload():
    opts = [{"id": "a", "label": "retain"}, {"id": "b", "label": "erase"}]
    o = assess(_req(footprint=("personal-data", "irreversible"),
                    action="erase-record"),
               options=opts, link_target="workspace://surface/7")
    assert o.bundle.requires_origination
    assert o.bundle.connector_payload()["render"] == "options"


def test_outcome_serialises():
    o = assess(_req())
    d = o.to_dict()
    assert d["verdict"] == "CONDITIONAL"
    assert "bundle" in d and "decision" in d


def test_prohibited_action_blocked_regardless():
    o = assess(_req(action="delete-prod-db"),
               prohibited_actions=["delete-prod-db"])
    assert o.blocked


def test_quarantine_overrides_standing_approval():
    # Regression (oversight_demo): a quarantined agent must NOT ride a standing
    # approval to GO. Frozen means frozen.
    from rvnd.action_gate import StandingApproval
    b = Breaker(Lease("bot", "L3", expires_at=1000.0),
                tripwires=[Tripwire("err", "error_rate", 0.25, "max")])
    appr = StandingApproval("bot", "pay-invoice", "pair:x")
    o = assess(_req(magnitude=50.0), breaker=b, standing_approvals=[appr],
               metrics={"error_rate": 0.9}, now=990.0)
    assert o.breaker_state == "QUARANTINED"
    assert o.effective_grade == "L0"
    assert o.blocked and o.verdict == "NO-GO"


def test_decayed_lease_overrides_standing_approval():
    from rvnd.action_gate import StandingApproval
    b = Breaker(Lease("bot", "L3", expires_at=1000.0), tripwires=[])
    appr = StandingApproval("bot", "pay-invoice", "pair:x")
    o = assess(_req(magnitude=50.0), breaker=b, standing_approvals=[appr],
               now=1001.0)
    assert o.breaker_state == "DECAYED"
    assert o.blocked


def test_breaker_cap_does_not_relax_a_low_request():
    # A live L4 lease must not RAISE an L1 request — cap is a meet, not a set.
    b = Breaker(Lease("bot", "L4", expires_at=1000.0), tripwires=[])
    o = assess(_req(grade="L1"), breaker=b, now=990.0)
    assert o.effective_grade == "L1"
