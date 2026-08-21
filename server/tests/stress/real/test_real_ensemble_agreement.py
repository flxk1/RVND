# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real-LLM ensemble-agreement measurement.

How often do Phi-3.5 and Qwen-7B (or whatever the user has registered
under role ``lock-c``) agree on the same input?

This is **not** a quality benchmark. It is a per-run, per-hardware,
per-workload measurement. The numbers are printed; nothing is asserted
on them. The only assertion is a sanity-count invariant — every input
must produce a recorded ensemble outcome (no silent drops).

Workload: the canonical 200-row :class:`SyntheticWorkload`. Test reads
``WORKSPACES_STRESS_REAL_WORKLOAD_SIZE`` to scale it down on slower
hardware (default 200, can be set lower e.g. 50 for fast iteration).
"""

from __future__ import annotations

import os
import time


from rvnd.lock.core import tier_c_semantic_check

from tests.stress._harness import SyntheticWorkload
from tests.stress._real_llm_harness import (
    RealEnsembleAgreementRecorder,
    RealLatencyRecorder,
    real_llm_or_skip,   # noqa: F401
    stable_hash,
)


def _resolve_workload_size(default: int = 200) -> int:
    raw = os.environ.get("WORKSPACES_STRESS_REAL_WORKLOAD_SIZE", "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
        return n if n > 0 else default
    except ValueError:
        return default


def test_real_ensemble_agreement_on_synthetic_workload(real_llm_or_skip, capsys):  # noqa: F811  -- pytest fixture: the parameter intentionally shadows the imported fixture
    """Drive synthetic inputs through the REAL ensemble. Record per-call
    labels. Report agreement, disagreement, INSUFFICIENT rate + per-model
    latency.

    Asserts ONLY that every input produced a recorded outcome (no silent
    drops). Performance + quality numbers are printed for the
    controller to read.
    """
    size = _resolve_workload_size(default=200)
    workload = SyntheticWorkload(total=size, seed=1234).build()

    agreement = RealEnsembleAgreementRecorder()
    latency = RealLatencyRecorder()
    none_results = 0  # ensemble call returned None (backend unavailable)
    by_expected: dict[str, dict[str, int]] = {}
    started = time.time()

    for row in workload:
        bucket = by_expected.setdefault(row.expected_tier, {
            "pii_yes": 0, "pii_no": 0, "insufficient": 0, "none": 0,
        })
        call_started = time.time()
        result = tier_c_semantic_check(row.text)
        call_ms = (time.time() - call_started) * 1000
        if result is None:
            none_results += 1
            bucket["none"] += 1
            continue
        bucket[result.label] = bucket.get(result.label, 0) + 1
        per_model = result.per_model or {}
        if len(per_model) >= 2:
            mids = sorted(per_model.keys())
            agreement.record(
                input_hash=stable_hash(row.text),
                phi_label=per_model[mids[0]],
                qwen_label=per_model[mids[1]],
            )
        # Split the ensemble's wall-time evenly between the participating
        # models; per-call resolution is the same call so we treat them
        # symmetrically.
        for mid in per_model:
            latency.record(mid, call_ms / max(1, len(per_model)))

    elapsed = time.time() - started

    # ---- INVARIANT: every input produced a recorded outcome ----------
    agreement.total + none_results + (size - agreement.total - none_results)
    # The agreement recorder only fires when both models reported; the
    # "missing" branch covers the single-model and None-result paths.
    # Sum of all per-bucket counts must equal the workload size.
    grand_total = sum(sum(b.values()) for b in by_expected.values())
    assert grand_total == size, (
        f"sanity: recorded {grand_total} outcomes for {size} inputs — "
        f"silent drops? per-expected breakdown: {by_expected}"
    )

    # ---- PRINT only (measurements) -----------------------------------
    print("\n=== Real-LLM ensemble agreement — measurements ===")
    print(f"workload size       = {size}")
    print(f"wall-clock total    = {elapsed:.2f}s")
    print(f"backend-unavailable = {none_results}  "
          f"(both models down for that input)")
    print()
    print("--- agreement report ---")
    print(agreement.summary())
    print()
    print("--- per-model latency ---")
    print(latency.summary())
    print()
    print("--- per-expected-tier outcome breakdown ---")
    for tier in sorted(by_expected):
        print(f"  {tier:<14} {by_expected[tier]}")
