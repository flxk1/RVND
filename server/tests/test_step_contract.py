# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Step contracts: each node governed by a contract that resolves
risk + hardening into a granted autonomy, a requirement debt, and a timed
override — derived from the fingerprint of earlier approved steps.

Claims under test (written BEFORE the logic):
  S1  monotone in risk: at fixed hardening, MORE risk never RAISES autonomy
  S2  monotone in hardening: at fixed risk, MORE hardening never LOWERS it
  S3  risk floor: critical risk caps autonomy at a human-gated grade (<=1)
      no matter how hardened
  S4  requirement debt scales with risk (more risk → more to satisfy)
  S5  timed override: low risk → on_timeout 'proceed' (fail-open); higher
      risk → 'halt' (fail-closed)
  S6  auditability is invariant — True at every grade
  S7  derive_contract reads precedent: more prior approvals + low disagreement
      → more hardening → higher granted autonomy
  S8  deterministic
"""
from __future__ import annotations

import pytest

from workspaces.step_contract import derive_contract, RISK_LEVELS


def test_more_risk_never_raises_autonomy():                       # S1
    grades = [derive_contract(r, prior_approvals=10)["grade"]
              for r in RISK_LEVELS]
    assert grades == sorted(grades, reverse=True)   # non-increasing in risk


def test_more_hardening_never_lowers_autonomy():                  # S2
    g_low = derive_contract("medium", prior_approvals=0)["grade"]
    g_high = derive_contract("medium", prior_approvals=20)["grade"]
    assert g_high >= g_low


def test_critical_risk_is_floored_human_gated():                  # S3
    c = derive_contract("critical", prior_approvals=1000,
                        disagreement_rate=0.0)
    assert c["grade"] <= 1                           # never auto, however hardened
    assert c["auditable"] is True


def test_requirement_debt_scales_with_risk():                     # S4
    debts = [len(derive_contract(r, prior_approvals=5)["requirements"])
             for r in RISK_LEVELS]
    assert debts == sorted(debts)                   # non-decreasing in risk
    assert "reserved-act" in derive_contract("critical")["requirements"]


def test_timed_override_direction_by_risk():                      # S5
    assert derive_contract("low")["on_timeout"] == "proceed"
    assert derive_contract("high")["on_timeout"] == "halt"
    assert derive_contract("critical")["on_timeout"] == "halt"
    # the window is carried through
    assert derive_contract("low", override_window_seconds=90)[
        "override_window_seconds"] == 90


def test_auditable_is_invariant():                                # S6
    for r in RISK_LEVELS:
        for appr in (0, 5, 50):
            assert derive_contract(r, prior_approvals=appr)["auditable"] is True


def test_precedent_raises_hardening_and_grade():                  # S7
    new = derive_contract("low", prior_approvals=0)
    proven = derive_contract("low", prior_approvals=20, disagreement_rate=0.0)
    assert proven["hardening"] > new["hardening"]
    assert proven["grade"] >= new["grade"]
    # but decay (high disagreement) pulls hardening back down
    decayed = derive_contract("low", prior_approvals=20, disagreement_rate=0.8)
    assert decayed["hardening"] < proven["hardening"]


def test_deterministic():                                         # S8
    a = derive_contract("high", prior_approvals=7, disagreement_rate=0.1)
    b = derive_contract("high", prior_approvals=7, disagreement_rate=0.1)
    assert a == b


def test_unknown_risk_rejected():
    with pytest.raises(ValueError):
        derive_contract("apocalyptic")
