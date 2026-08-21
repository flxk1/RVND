# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""M1 — unknown/unrecognised input must fail toward the MOST restrictive, not
the old fail-OPEN defaults (Oversight + Lock/Shield panels)."""
from __future__ import annotations

import pytest

from rvnd import verdict as V
from rvnd import policy_matrix as pm
from rvnd.breaker import cap_grade


# ── verdict mappers: absent → PERMIT (no constraint); unrecognised → DENY ────
@pytest.mark.parametrize("s,expect", [
    (None, V.Verdict.PERMIT), ("", V.Verdict.PERMIT),     # absent = no constraint
    ("GO", V.Verdict.PERMIT), ("NO-GO", V.Verdict.DENY),
    ("CONDITIONAL", V.Verdict.HOLD),
    ("garbage", V.Verdict.DENY), ("permitt", V.Verdict.DENY),  # unrecognised → deny
])
def test_from_gate_failsafe(s, expect):
    assert V.from_gate(s) is expect


@pytest.mark.parametrize("s,expect", [
    (None, V.Verdict.PERMIT), ("go", V.Verdict.PERMIT), ("block", V.Verdict.DENY),
    ("ask", V.Verdict.HOLD), ("nonsense", V.Verdict.DENY),
])
def test_from_light_failsafe(s, expect):
    assert V.from_light(s) is expect


# ── policy_matrix: unrecognised gate verdict tightens to block, not go ──────
def test_effective_light_unknown_gate_verdict_blocks():
    # Pick a cell that is itself "go" (L1×approve) so the outcome is attributable
    # to the gate default, not the matrix.
    m = pm.recommended_default()
    absent = pm.effective_light(m, grade="L1", oversight="approve", gate_verdict=None)
    assert absent["light"] == "go"                  # absent gate adds no constraint
    unknown = pm.effective_light(m, grade="L1", oversight="approve", gate_verdict="weird")
    assert unknown["light"] == "block"              # unrecognised gate → fail-safe block


# ── breaker cap_grade: unrecognised ceiling → L0; absent → no cap ───────────
@pytest.mark.parametrize("req,ceil,expect", [
    ("L4", "garbage", "L0"),      # unrecognised ceiling → most restrictive
    ("L4", "", "L4"),             # absent ceiling → no cap
    ("L2", "L1", "L1"),           # normal meet
    ("L4", "L4", "L4"),
    ("L3", "L0", "L0"),
])
def test_cap_grade_failsafe(req, ceil, expect):
    assert cap_grade(req, ceil) == expect


# ── breaker.cap_grade: unrecognised REQUESTED also fails safe (symmetry) ─────
def test_cap_grade_unrecognised_requested_is_L0():
    assert cap_grade("garbage", "L4") == "L0"


# ── oversight_compose.binds_grade: same fail-safe as cap_grade ──────────────
def test_binds_grade_failsafe():
    from rvnd.oversight_compose import binds_grade, ComposedOversight
    assert binds_grade(ComposedOversight(grade_ceiling="garbage"), "L4") == "L0"  # unrecognised → L0
    assert binds_grade(ComposedOversight(grade_ceiling=""), "L4") == "L4"          # absent → no cap
    assert binds_grade(ComposedOversight(grade_ceiling="L1"), "L4") == "L1"        # normal meet
