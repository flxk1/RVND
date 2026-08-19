# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""NT-13 telemetry monotonicity — observables raise verdicts, never lower.

Property under test (the conformity-runtime design, C0 item 1): for
every (request, observables) combination, the post-telemetry verdict is at
least as severe as the pre-telemetry verdict, NO-GO is never produced by
telemetry alone, and absent observables reproduce the legacy behaviour
exactly.
"""

import itertools
import random

import rvnd.action_gate as ag
from rvnd.action_gate import (ActionRequest, Observables, StandingApproval,
                               Verdict, check_telemetry_monotonicity)

SEV = {Verdict.GO: 0, Verdict.CONDITIONAL: 1, Verdict.NO_GO: 2}


# ── backwards compatibility ───────────────────────────────────────────────────

def test_no_observables_is_legacy_behaviour():
    req = ActionRequest("a", "read_folder", "L2")
    assert ag.gate(req).to_dict() == ag.gate(req, observables=None).to_dict()


def test_empty_observables_do_not_escalate():
    req = ActionRequest("a", "read_folder", "L2")
    d = ag.gate(req, observables=Observables())
    assert d.verdict is Verdict.GO
    assert d.audit_triple["telemetry"]["escalated"] is False


# ── single triggers ───────────────────────────────────────────────────────────

def test_low_confidence_floors_benign_go_to_conditional():
    d = ag.gate(ActionRequest("a", "read", "L2"),
                observables=Observables(confidence=0.5))
    assert d.verdict is Verdict.CONDITIONAL
    assert "NT-13" in d.reason
    assert d.audit_triple["telemetry"]["verdict_before"] == "GO"


def test_low_confidence_overrides_standing_approval():
    """An approval covers the class; telemetry speaks about the instance."""
    sa = StandingApproval("a", "export", "pair-1", until=None)
    req = ActionRequest("a", "export", "L3", footprint=("personal-data",))
    base = ag.gate(req, standing_approvals=[sa])
    assert base.verdict is Verdict.GO  # covered
    d = ag.gate(req, standing_approvals=[sa],
                observables=Observables(confidence=0.4))
    assert d.verdict is Verdict.CONDITIONAL
    assert d.obligation_pairs == ["pair-1"]  # provenance survives escalation


def test_confidence_at_floor_does_not_escalate():
    d = ag.gate(ActionRequest("a", "read", "L2"),
                observables=Observables(confidence=0.85))
    assert d.verdict is Verdict.GO


def test_weak_authority_on_flagged_action_escalates():
    sa = StandingApproval("a", "publish", "pair-2")
    req = ActionRequest("a", "publish", "L3", footprint=("external-publish",),
                        affected_parties=("recipient",))
    d = ag.gate(req, standing_approvals=[sa],
                observables=Observables(authority_tier=4))
    assert d.verdict is Verdict.CONDITIONAL


def test_weak_authority_on_benign_action_is_ignored():
    d = ag.gate(ActionRequest("a", "read", "L2"),
                observables=Observables(authority_tier=5))
    assert d.verdict is Verdict.GO


def test_first_occurrence_of_flagged_action_escalates():
    sa = StandingApproval("a", "wire", "pair-3")
    req = ActionRequest("a", "wire", "L3", footprint=("financial",))
    d = ag.gate(req, standing_approvals=[sa], observables=Observables(novelty=0))
    assert d.verdict is Verdict.CONDITIONAL
    d2 = ag.gate(req, standing_approvals=[sa], observables=Observables(novelty=7))
    assert d2.verdict is Verdict.GO


def test_many_affected_parties_escalates():
    sa = StandingApproval("a", "mailshot", "pair-4")
    req = ActionRequest("a", "mailshot", "L3", footprint=("external-publish",),
                        affected_parties=("recipient",))
    d = ag.gate(req, standing_approvals=[sa],
                observables=Observables(affected_party_count=10))
    assert d.verdict is Verdict.CONDITIONAL
    d2 = ag.gate(req, standing_approvals=[sa],
                 observables=Observables(affected_party_count=3))
    assert d2.verdict is Verdict.GO


# ── structure of the escalated decision ──────────────────────────────────────

def test_escalation_is_reconstructible():
    d = ag.gate(ActionRequest("a", "read", "L2"),
                observables=Observables(confidence=0.1))
    t = d.audit_triple["telemetry"]
    assert t["escalated"] is True
    assert t["verdict_before"] == "GO"
    assert t["triggers"]
    assert t["observables"]["confidence"] == 0.1


def test_no_go_is_never_changed_by_telemetry():
    req = ActionRequest("a", "export", "L1", footprint=("personal-data",))
    base = ag.gate(req)
    assert base.verdict is Verdict.NO_GO
    d = ag.gate(req, observables=Observables(confidence=0.99, novelty=50))
    assert d.verdict is Verdict.NO_GO
    assert d.reason == base.reason  # untouched, not rewritten


def test_conditional_is_not_double_escalated():
    req = ActionRequest("a", "export", "L2", footprint=("personal-data",))
    d = ag.gate(req, observables=Observables(confidence=0.1))
    assert d.verdict is Verdict.CONDITIONAL
    assert "NT-13" not in d.reason  # already conditional; telemetry adds nothing


# ── the property: monotone, never NO-GO from telemetry alone ─────────────────

def _random_observables(rng):
    return Observables(
        confidence=rng.choice([None, rng.random()]),
        authority_tier=rng.choice([None, 1, 2, 3, 4, 5]),
        novelty=rng.choice([None, 0, 1, 12]),
        affected_party_count=rng.choice([None, 0, 1, 9, 10, 500]),
    )


def test_property_monotone_over_random_space():
    rng = random.Random(2604)
    grades = ["L0", "L1", "L2", "L3", "L4"]
    tags = [(), ("personal-data",), ("financial",), ("external-publish",),
            ("irreversible", "personal-data"), ("not-a-risk-tag",)]
    postures = ["restrictive", "balanced", "permissive"]
    sa = StandingApproval("a", "act", "pair-x")
    for _ in range(500):
        req = ActionRequest("a", "act", rng.choice(grades),
                            footprint=rng.choice(tags))
        kw = dict(standing_approvals=rng.choice([(), (sa,)]),
                  posture=rng.choice(postures))
        base = ag.gate(req, **kw)
        after = ag.gate(req, observables=_random_observables(rng), **kw)
        assert check_telemetry_monotonicity(base, after), (base, after)
        assert SEV[after.verdict] >= SEV[base.verdict]
        # Telemetry alone never manufactures NO-GO.
        if base.verdict is not Verdict.NO_GO:
            assert after.verdict is not Verdict.NO_GO


def test_property_exhaustive_trigger_combinations_stop_at_conditional():
    """All 2^4 trigger combinations on a GO action land on GO or CONDITIONAL."""
    sa = StandingApproval("a", "act", "p")
    req = ActionRequest("a", "act", "L3", footprint=("external-publish",),
                        affected_parties=("recipient",))
    vals = {
        "confidence": [None, 0.2],
        "authority_tier": [None, 5],
        "novelty": [None, 0],
        "affected_party_count": [None, 99],
    }
    for combo in itertools.product(*vals.values()):
        obs = Observables(**dict(zip(vals.keys(), combo)))
        d = ag.gate(req, standing_approvals=[sa], observables=obs)
        assert d.verdict in (Verdict.GO, Verdict.CONDITIONAL)
        any_trigger = any(v is not None for v in combo)
        if not any_trigger:
            assert d.verdict is Verdict.GO
