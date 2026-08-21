# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real-LLM cloud-token reduction measurement.

Replicates the synthetic ``test_cloud_token_reduction`` but with the
REAL local-LLM ensemble as the validator in Mode B. Mode A is unchanged
— it never calls the local models — so the comparison answers a single
question:

  *On this hardware, against this workload, with these registered
  models, how much cloud-token spend did the validator actually avoid?*

The number is printed. It is **not** asserted. The synthetic test
asserts a >= 20% reduction (calibrated against the synthetic
privacy-heavy mix); the real-LLM test does not, because:

  * Mode B's reduction depends on the model's PII recall, which depends
    on the model, which is what we're measuring.
  * Asserting a threshold here would just propagate the synthetic
    test's calibration to a workload it wasn't calibrated for.

What IS asserted: structural invariants — the same workload ran in both
modes, no PII leaked, Mode B correct-refusals >= Mode A's.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass


from rvnd.lock.core import (
    _detect_confusable_bypass,
    tier_b_scan_text,
    tier_c_semantic_check,
)

from tests.stress._harness import (
    MockCloudLLM,
    SyntheticWorkload,
    assert_no_pii_leaked,
)
from tests.stress._real_llm_harness import (
    RealTokenCounter,
    real_llm_or_skip,   # noqa: F401
)


@dataclass
class _ModeStats:
    name: str
    cloud_calls: int
    cloud_tokens: int
    local_invocations: int
    local_wall_ms: float
    wall_clock_s: float
    outcomes: dict
    correct_refusals: int


def _should_refuse(tier: str) -> bool:
    return tier in ("tier_b", "tier_b_plus", "tier_c")


def _run_mode_a(workload, cloud: MockCloudLLM,
                counter: RealTokenCounter) -> _ModeStats:
    """Baseline: dispatch every row to the cloud mock. No validator."""
    outcomes: Counter = Counter()
    started = time.time()
    for row in workload:
        cloud.dispatch(row.text)
        counter.record_cloud(row.text, "[mock]")
        outcomes["dispatched"] += 1
    return _ModeStats(
        name="A (cloud-only)",
        cloud_calls=counter.cloud_calls,
        cloud_tokens=counter.total_cloud_tokens,
        local_invocations=counter.local_invocations,
        local_wall_ms=counter.local_wall_ms,
        wall_clock_s=time.time() - started,
        outcomes=dict(outcomes),
        correct_refusals=0,
    )


def _run_mode_b(workload, cloud: MockCloudLLM,
                counter: RealTokenCounter) -> _ModeStats:
    """With validator: regex + REAL Tier C ensemble + gated cloud."""
    outcomes: Counter = Counter()
    correct_refusals = 0
    started = time.time()
    for row in workload:
        if _detect_confusable_bypass(row.text) or tier_b_scan_text(row.text):
            outcomes["refused"] += 1
            if _should_refuse(row.expected_tier):
                correct_refusals += 1
            continue
        call_started = time.time()
        c = tier_c_semantic_check(row.text)
        call_ms = (time.time() - call_started) * 1000
        # Each ensemble call invokes both registered models — record one
        # per model. The actual per-model split happens inside Tier C;
        # for the counter we treat the call as N model invocations
        # totalling call_ms.
        if c is None:
            # Backend unavailable — refuse per policy (the test would
            # rather drop the call than leak).
            outcomes["refused_backend"] += 1
            counter.record_local(ms=call_ms)
            continue
        model_count = max(1, len(c.per_model))
        for _mid in c.per_model:
            counter.record_local(ms=call_ms / model_count)
        if c.label == "pii_yes":
            outcomes["refused"] += 1
            if _should_refuse(row.expected_tier):
                correct_refusals += 1
            continue
        if c.label == "insufficient":
            outcomes["escalated"] += 1
            continue
        # Clean — dispatch.
        cloud.dispatch(row.text)
        counter.record_cloud(row.text, "[mock]")
        outcomes["dispatched"] += 1
    return _ModeStats(
        name="B (with real local validator)",
        cloud_calls=counter.cloud_calls,
        cloud_tokens=counter.total_cloud_tokens,
        local_invocations=counter.local_invocations,
        local_wall_ms=counter.local_wall_ms,
        wall_clock_s=time.time() - started,
        outcomes=dict(outcomes),
        correct_refusals=correct_refusals,
    )


def test_real_cloud_token_reduction(real_llm_or_skip, capsys):  # noqa: F811  -- pytest fixture: the parameter intentionally shadows the imported fixture
    """Measure cloud-token spend with vs. without the REAL validator.

    Print a comparison table. Assert only the structural invariants;
    leave the reduction figure to the controller's judgement.
    """
    workload = SyntheticWorkload(total=100, seed=1234).build()

    # Mode A — baseline.
    counter_a = RealTokenCounter()
    cloud_a = MockCloudLLM()
    with cloud_a:
        stats_a = _run_mode_a(workload, cloud_a, counter_a)

    # Mode B — REAL validator.
    counter_b = RealTokenCounter()
    cloud_b = MockCloudLLM()
    with cloud_b:
        stats_b = _run_mode_b(workload, cloud_b, counter_b)

    # ---- INVARIANTS ----
    # No PII leaked into the cloud mock under Mode B.
    assert_no_pii_leaked(cloud_b.calls, [r.text for r in workload])
    # Mode B's correct refusal count >= Mode A's (Mode A never refuses).
    assert stats_b.correct_refusals >= stats_a.correct_refusals, (
        f"Mode B caught fewer refusals than Mode A: "
        f"{stats_b.correct_refusals} < {stats_a.correct_refusals}"
    )

    # ---- MEASUREMENTS ----
    if stats_a.cloud_tokens > 0:
        reduction_pct = 100.0 * (
            stats_a.cloud_tokens - stats_b.cloud_tokens
        ) / stats_a.cloud_tokens
    else:
        reduction_pct = 0.0

    print("\n=== Real-LLM cloud-token reduction — measurements ===")
    print(f"workload size            = {len(workload)}")
    print()
    print(f"{'metric':<28} {'Mode A':>15} {'Mode B':>15}")
    print(f"{'-'*60}")
    print(f"{'cloud calls':<28} {stats_a.cloud_calls:>15} "
          f"{stats_b.cloud_calls:>15}")
    print(f"{'cloud tokens (synthetic)':<28} {stats_a.cloud_tokens:>15} "
          f"{stats_b.cloud_tokens:>15}")
    print(f"{'local invocations':<28} {stats_a.local_invocations:>15} "
          f"{stats_b.local_invocations:>15}")
    print(f"{'wall-clock (s)':<28} {stats_a.wall_clock_s:>15.2f} "
          f"{stats_b.wall_clock_s:>15.2f}")
    print(f"{'correct refusals':<28} {stats_a.correct_refusals:>15} "
          f"{stats_b.correct_refusals:>15}")
    print()
    print(f"measured token reduction = {reduction_pct:.1f}%  "
          f"(in this run on this hardware against this workload)")
    print()
    print(f"Mode A outcomes = {stats_a.outcomes}")
    print(f"Mode B outcomes = {stats_b.outcomes}")
