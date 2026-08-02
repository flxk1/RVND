# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Aggregate caps on standing approvals — stress-test case 6.

An uncapped standing approval is an unbounded amplifier: 101 payments of
€99 pass a "< €100" per-instance predicate while the aggregate is exactly
what the rule meant to forbid. These tests pin the cap semantics:

- caps only ever REMOVE a GO (fall back to sign-off), never grant one;
- a capped approval never covers an unmeasured instance (conservative);
- usage counters are a replay of verdict history, never hand state.
"""

from datetime import date

import pytest

from workspaces.action_gate import (
    ActionRequest, ApprovalUsage, StandingApproval, Verdict, gate,
    usage_from_history)


AS_OF = date(2026, 6, 5)


def _req(magnitude=None, **kw):
    base = dict(agent="bot", action_class="pay-invoice",
                autonomy_grade="L3", footprint=("financial",),
                magnitude=magnitude)
    base.update(kw)
    return ActionRequest(**base)


def _approval(**kw):
    base = dict(agent="bot", action_class="pay-invoice",
                obligation_pair="pair:invoices-under-100")
    base.update(kw)
    return StandingApproval(**base)


def test_uncapped_approval_behaves_as_before():
    d = gate(_req(magnitude=99.0), standing_approvals=[_approval()],
             as_of=AS_OF)
    assert d.verdict is Verdict.GO
    assert d.reason == "covered by standing approval"


def test_uses_cap_exhausts_to_conditional():
    appr = _approval(max_uses=2)
    usage = {appr.obligation_pair: ApprovalUsage(uses=2, total=0.0)}
    d = gate(_req(magnitude=10.0), standing_approvals=[appr],
             approval_usage=usage, as_of=AS_OF)
    assert d.verdict is Verdict.CONDITIONAL
    assert "cap exhausted" in d.reason
    assert d.audit_triple["cap_exhausted"]


def test_uses_cap_under_limit_still_covers():
    appr = _approval(max_uses=2)
    usage = {appr.obligation_pair: ApprovalUsage(uses=1, total=0.0)}
    d = gate(_req(magnitude=10.0), standing_approvals=[appr],
             approval_usage=usage, as_of=AS_OF)
    assert d.verdict is Verdict.GO


def test_total_cap_structuring_attack_blocked():
    appr = _approval(max_total=10_000.0)
    usage = {appr.obligation_pair: ApprovalUsage(uses=100, total=9_900.0)}
    d = gate(_req(magnitude=99.0), standing_approvals=[appr],
             approval_usage=usage, as_of=AS_OF)
    # 9900 + 99 = 9999 ≤ 10000 → still covered…
    assert d.verdict is Verdict.GO
    usage2 = {appr.obligation_pair: ApprovalUsage(uses=101, total=9_999.0)}
    d2 = gate(_req(magnitude=99.0), standing_approvals=[appr],
              approval_usage=usage2, as_of=AS_OF)
    # …but the 102nd crosses the aggregate and falls to sign-off.
    assert d2.verdict is Verdict.CONDITIONAL
    assert "total cap" in d2.reason


def test_unmeasured_instance_never_consumes_counted_budget():
    appr = _approval(max_total=10_000.0)
    d = gate(_req(magnitude=None), standing_approvals=[appr],
             as_of=AS_OF)
    assert d.verdict is Verdict.CONDITIONAL
    assert "unmeasured" in d.reason


def test_caps_never_grant_no_go_relief():
    # Under-grade agent: cap logic must not change the NO-GO outcome.
    appr = _approval(max_uses=1)
    usage = {appr.obligation_pair: ApprovalUsage(uses=1)}
    d = gate(_req(magnitude=5.0, autonomy_grade="L1"),
             standing_approvals=[appr], approval_usage=usage, as_of=AS_OF)
    assert d.verdict is Verdict.NO_GO


def test_second_uncapped_approval_still_covers():
    capped = _approval(max_uses=1)
    other = _approval(obligation_pair="pair:general-payments")
    usage = {capped.obligation_pair: ApprovalUsage(uses=1)}
    d = gate(_req(magnitude=5.0), standing_approvals=[capped, other],
             approval_usage=usage, as_of=AS_OF)
    assert d.verdict is Verdict.GO
    assert d.obligation_pairs == ["pair:general-payments"]


def test_usage_from_history_replays_go_citations_only():
    history = [
        {"predicate": "GO", "reason": "standing-approval",
         "obligation_pairs": ["pair:a"], "magnitude": 50.0},
        {"predicate": "GO", "reason": "standing-approval",
         "obligation_pairs": ["pair:a", "pair:b"], "magnitude": 25.0},
        {"predicate": "GO", "reason": "benign",
         "obligation_pairs": [], "magnitude": 999.0},
        {"predicate": "NO-GO", "reason": "prohibited",
         "obligation_pairs": ["pair:a"], "magnitude": 10.0},
        {"predicate": "GO", "reason": "standing-approval",
         "obligation_pairs": ["pair:a"], "magnitude": None},
    ]
    usage = usage_from_history(history)
    assert usage["pair:a"].uses == 3
    assert usage["pair:a"].total == 75.0
    assert usage["pair:b"].uses == 1
    assert usage["pair:b"].total == 25.0


def test_replayed_usage_drives_the_gate_end_to_end():
    appr = _approval(max_uses=3)
    history = []
    verdicts = []
    for _ in range(5):
        usage = usage_from_history(history)
        d = gate(_req(magnitude=10.0), standing_approvals=[appr],
                 approval_usage=usage, as_of=AS_OF)
        verdicts.append(d.verdict)
        history.append(d.audit_triple)
    assert verdicts == [Verdict.GO, Verdict.GO, Verdict.GO,
                        Verdict.CONDITIONAL, Verdict.CONDITIONAL]


def test_magnitude_recorded_in_audit_triple():
    d = gate(_req(magnitude=42.5), standing_approvals=[_approval()],
             as_of=AS_OF)
    assert d.audit_triple["magnitude"] == 42.5


def test_cap_validation_at_write():
    with pytest.raises(ValueError):
        _approval(max_uses=0)
    with pytest.raises(ValueError):
        _approval(max_total=0)
