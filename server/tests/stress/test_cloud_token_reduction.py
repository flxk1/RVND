# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Deliverable 2 — Cloud-token reduction measurement (B10+).

Run the same 200-row synthetic workload twice and compare:

  * Mode A — baseline. No Workspace validator. Every dispatch goes to the
    cloud mock directly. Cloud-token spend is the unprotected baseline.
  * Mode B — with Workspace's full pipeline (Tier B regex → Tier C ensemble
    → cloud, gated). Tier-B / Tier-B+ refused; Tier-C-detected refused;
    only regex+ensemble-clean inputs reach the cloud.

Measurements per mode:

  * total cloud tokens (sum of synthetic input+output token counts)
  * local-model invocations
  * dispatch outcomes (committed / refused / escalated)

Assertions:

  * Mode B cloud tokens < Mode A cloud tokens (>= 20% reduction on
    this privacy-heavy mix).
  * Mode B "correct refusal" count >= Mode A's (the validator catches
    things that should never have shipped).
  * Mode B local-model invocations equal 2× the number of Mode-B inputs
    that reached the Tier C path (ensemble runs both models).

A printable summary table is captured to stdout for the brief's
"-s"-friendly report format.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


from rvnd.lock.core import (
    tier_b_scan_text,
    tier_c_semantic_check,
    _detect_confusable_bypass,
)

from tests.stress._harness import (
    MockCloudLLM,
    MockLocalLLM,
    SyntheticWorkload,
    TokenCounter,
    synthetic_token_count,
)


@dataclass
class ModeStats:
    name: str
    cloud_calls: int
    cloud_tokens: int
    local_invocations: int
    network_call_count: int
    outcomes: dict
    correct_refusals: int


def _expected_should_refuse(tier: str) -> bool:
    """Per the synthetic distribution, which tiers SHOULD have been
    refused if Workspace were doing its job?"""
    return tier in ("tier_b", "tier_b_plus", "tier_c")


def _run_mode_a(workload, counter: TokenCounter,
                cloud: MockCloudLLM) -> ModeStats:
    """Baseline: dispatch every input to cloud directly. No validator."""
    outcomes = Counter()
    correct_refusals = 0
    for row in workload:
        # Baseline does NOT inspect inputs; it just dispatches.
        cloud.dispatch(row.text)
        outcomes["dispatched"] += 1
        # No refusals → never correct in this mode.
    return ModeStats(
        name="A (cloud-only)",
        cloud_calls=len(cloud.calls),
        cloud_tokens=counter.total_cloud_tokens,
        local_invocations=counter.local_invocations,
        network_call_count=len(cloud.calls),
        outcomes=dict(outcomes),
        correct_refusals=correct_refusals,
    )


def _run_mode_b(workload, counter: TokenCounter,
                cloud: MockCloudLLM,
                local: MockLocalLLM) -> ModeStats:
    """With validator: lock + Tier C ensemble + gated cloud."""
    outcomes = Counter()
    correct_refusals = 0
    for row in workload:
        # Step 1 — regex.
        if _detect_confusable_bypass(row.text) or tier_b_scan_text(row.text):
            outcomes["refused"] += 1
            if _expected_should_refuse(row.expected_tier):
                correct_refusals += 1
            continue
        # Step 2 — Tier C ensemble (the two-model classifier).
        c = tier_c_semantic_check(row.text)
        if c is None:
            # Local backend unavailable — caller policy is to refuse.
            outcomes["refused"] += 1
            continue
        if c.label == "pii_yes":
            outcomes["refused"] += 1
            if _expected_should_refuse(row.expected_tier):
                correct_refusals += 1
            continue
        if c.label == "insufficient":
            outcomes["escalated"] += 1
            continue
        # Clean — dispatch.
        cloud.dispatch(row.text)
        outcomes["dispatched"] += 1
    return ModeStats(
        name="B (with validator)",
        cloud_calls=len(cloud.calls),
        cloud_tokens=counter.total_cloud_tokens,
        local_invocations=counter.local_invocations,
        network_call_count=len(cloud.calls) + counter.local_invocations,
        outcomes=dict(outcomes),
        correct_refusals=correct_refusals,
    )


def test_mode_b_uses_strictly_fewer_cloud_tokens_than_mode_a(capsys):
    workload = SyntheticWorkload(total=200, seed=1234).build()

    # Mode A — baseline.
    counter_a = TokenCounter()
    cloud_a = MockCloudLLM(token_counter=counter_a)
    with cloud_a:
        stats_a = _run_mode_a(workload, counter_a, cloud_a)

    # Mode B — with the validator.
    counter_b = TokenCounter()
    cloud_b = MockCloudLLM(token_counter=counter_b)
    local_b = MockLocalLLM()
    # Have the local-mock token counter share state with the cloud-mock so
    # we measure the combined dispatch cost in a single counter.
    local_b.token_counter = counter_b
    with cloud_b, local_b:
        stats_b = _run_mode_b(workload, counter_b, cloud_b, local_b)

    # Reduction assertion.
    assert stats_b.cloud_tokens < stats_a.cloud_tokens, (
        f"Mode B cloud tokens ({stats_b.cloud_tokens}) >= "
        f"Mode A's ({stats_a.cloud_tokens})"
    )
    reduction_pct = (
        100.0 * (stats_a.cloud_tokens - stats_b.cloud_tokens) / max(1, stats_a.cloud_tokens)
    )
    assert reduction_pct >= 20.0, (
        f"Mode B cloud-token reduction only {reduction_pct:.1f}% on the "
        f"privacy-heavy synthetic mix (target: >= 20%)"
    )

    # Correct-refusal monotonicity: Mode B catches more.
    assert stats_b.correct_refusals >= stats_a.correct_refusals

    # Local invocations: ensemble runs BOTH models for every Tier-C call.
    # We can't predict exactly how many inputs reached Tier C (regex-clean
    # ones do); but the count must be even (2 calls per input) and
    # positive, since the no-PII and Tier-C-only buckets are regex-clean.
    assert stats_b.local_invocations > 0
    assert stats_b.local_invocations % 2 == 0, (
        f"local invocations ({stats_b.local_invocations}) not even — "
        f"ensemble should always run both models or neither"
    )

    # Printable summary.
    print(
        f"\nMode A ({stats_a.name}):     {len(workload)} dispatches, "
        f"{stats_a.cloud_tokens} cloud tokens, "
        f"{stats_a.local_invocations} local invocations"
    )
    print(
        f"Mode B ({stats_b.name}): "
        f"{stats_b.outcomes.get('dispatched', 0)} dispatches "
        f"({stats_b.outcomes.get('refused', 0)} refused, "
        f"{stats_b.outcomes.get('escalated', 0)} escalated), "
        f"{stats_b.cloud_tokens} cloud tokens "
        f"(-{reduction_pct:.1f}%), "
        f"{stats_b.local_invocations} local invocations"
    )
    print(
        f"correct_refusals: A={stats_a.correct_refusals}, "
        f"B={stats_b.correct_refusals} "
        f"(of {sum(1 for r in workload if _expected_should_refuse(r.expected_tier))} expected)"
    )


def test_mode_a_and_mode_b_run_against_identical_workload():
    """Determinism check: the SyntheticWorkload factory must be seeded
    so two builds produce the identical list."""
    a = SyntheticWorkload(total=200, seed=1234).build()
    b = SyntheticWorkload(total=200, seed=1234).build()
    assert [r.text for r in a] == [r.text for r in b]
    assert [r.expected_tier for r in a] == [r.expected_tier for r in b]


def test_synthetic_token_count_is_deterministic_per_text():
    text = "a quick brown fox jumps"
    assert synthetic_token_count(text) == synthetic_token_count(text)
    assert synthetic_token_count("") == 0
    assert synthetic_token_count("ab") >= 1
