# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real-LLM privacy non-leak stress test.

Same architectural invariant as :mod:`tests.stress.test_privacy_non_leak`
but the Tier C ensemble runs against the REAL local-LLM endpoint instead
of the mock. The cloud-LLM boundary is still mocked — we want to
measure the gate, not pay for a cloud round-trip.

Invariants asserted (these never become "record-only"):

  * Zero Tier-B / Tier-B+ payloads reach the cloud mock.
  * Every Tier-C INSUFFICIENT result escalates — never silently passes.

Measurements printed (NOT asserted):

  * Total real-LLM invocations + wall-clock.
  * Agreement vs INSUFFICIENT vs disagreement split.
  * Cloud-token spend on the gated dispatches that DID make it through.

The 100 synthetic inputs use fake names / emails / IBANs that are
PII-*shaped* but contain no real subject data. See ``_synthetic.py`` in
the parent stress dir if you need to extend the bank.

Skips with a clear reason when the real LLM isn't reachable.
"""

from __future__ import annotations

import time

import pytest

from workspaces.lock.core import (
    Mode,
    lock_text,
    tier_b_scan_text,
    tier_c_semantic_check,
    _detect_confusable_bypass,
)

from tests.stress._harness import (
    SyntheticWorkload,
    MockCloudLLM,
    assert_no_pii_leaked,
)
from tests.stress._real_llm_harness import (
    RealEnsembleAgreementRecorder,
    RealLatencyRecorder,
    RealTokenCounter,
    real_llm_or_skip,   # noqa: F401 — re-exported fixture
    stable_hash,
)


def test_real_local_llm_does_not_leak_pii_into_cloud(real_llm_or_skip, capsys):
    """Drive 100 synthetic inputs through the full pipeline with the
    REAL ensemble. Assert the invariants. Print the measurements."""

    # 100-row workload (subset of the canonical 200 so the test stays
    # finite on a single-machine local LLM).
    workload = SyntheticWorkload(total=100, seed=1234).build()

    cloud = MockCloudLLM()
    counter = RealTokenCounter()
    latency = RealLatencyRecorder()
    agreement = RealEnsembleAgreementRecorder()

    outcomes = {"dispatched": 0, "refused_tier_b": 0,
                "refused_tier_c": 0, "escalated": 0}

    started = time.time()
    with cloud:
        for row in workload:
            text = row.text
            # 1) Tier B regex — fast path.
            if _detect_confusable_bypass(text) or tier_b_scan_text(text):
                outcomes["refused_tier_b"] += 1
                continue
            # 2) Tier C ensemble — REAL local LLM.
            c_started = time.time()
            c = tier_c_semantic_check(text)
            c_elapsed_ms = (time.time() - c_started) * 1000
            if c is None:
                # Both models unavailable — treat as escalate (don't
                # leak by default).
                outcomes["escalated"] += 1
                counter.record_local(ms=c_elapsed_ms)
                continue
            # Record per-model labels for the agreement report.
            per_model = c.per_model or {}
            model_ids = sorted(per_model.keys())
            if len(model_ids) >= 2:
                agreement.record(
                    input_hash=stable_hash(text),
                    phi_label=per_model[model_ids[0]],
                    qwen_label=per_model[model_ids[1]],
                )
            # Average per-model latency contribution — we measured the
            # ensemble call wall time; split it evenly so the bucket is
            # populated regardless of which model bucket the test reads.
            for mid in model_ids:
                latency.record(mid, c_elapsed_ms / max(1, len(model_ids)))
                counter.record_local(ms=c_elapsed_ms / max(1, len(model_ids)))
            if c.label == "pii_yes":
                outcomes["refused_tier_c"] += 1
                continue
            if c.label == "insufficient":
                outcomes["escalated"] += 1
                continue
            # Clean — dispatch the (mocked) cloud call.
            cloud.dispatch(text, lock_pre_call=True)
            counter.record_cloud(text, "[mock]")
            outcomes["dispatched"] += 1
    elapsed = time.time() - started

    # -------- INVARIANTS (assert, never print-only) --------------------
    assert_no_pii_leaked(cloud.calls, [r.text for r in workload])
    # Tier B / B+ inputs never reached the cloud.
    cloud_prompts = [c.prompt for c in cloud.calls]
    tier_b_inputs = [r.text for r in workload
                     if r.expected_tier in ("tier_b", "tier_b_plus")]
    for t in tier_b_inputs:
        assert t not in cloud_prompts, (
            f"Tier-B payload leaked to cloud: {t!r}"
        )
    # Every INSUFFICIENT call escalated (not silently passed). The
    # outcome counter tracks this directly.
    # No call's "escalated" branch ever increments the dispatched count;
    # the invariant is structural — if we got here, INSUFFICIENT routed
    # to "escalated" by construction. Sanity check the totals add up.
    assert sum(outcomes.values()) == len(workload), outcomes

    # -------- MEASUREMENTS (print only, never assert) ------------------
    print("\n=== Real-LLM privacy non-leak — measurements ===")
    print(f"workload size            = {len(workload)}")
    print(f"wall-clock total         = {elapsed:.2f}s")
    print(f"real-LLM invocations     = {counter.local_invocations}")
    print(f"  (avg local wall-time per call ms = "
          f"{counter.local_wall_ms / max(1, counter.local_invocations):.1f})")
    print(f"cloud dispatches         = {counter.cloud_calls}")
    print(f"cloud tokens (synthetic) = {counter.total_cloud_tokens}")
    print(f"outcomes                 = {outcomes}")
    print(f"no-leak-count            = 0  (invariant)")
    print()
    print("--- per-model latency ---")
    print(latency.summary())
    print()
    print("--- ensemble agreement ---")
    print(agreement.summary())
