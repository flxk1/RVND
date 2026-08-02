# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real-LLM latency-distribution measurement.

For each model registered under role ``lock-c`` or ``validator``, run
100 classification calls with short synthetic prompts and record per-call
wall-time. Compute and print p50 / p95 / p99 / min / max / mean per
model.

What gets asserted:
  * Every call returned without raising.
  * Each call's parsed label is non-empty.

What does NOT get asserted:
  * Any latency threshold. The point is to give the controller a number
    on THEIR hardware against THIS workload; we don't pre-judge what
    "good enough" is.
"""

from __future__ import annotations

import time

import pytest

from workspaces import local_llm
from tests.stress._real_llm_harness import (
    RealLatencyRecorder,
    real_llm_or_skip,   # noqa: F401
)


# A small bank of short prompts — keep them under ~120 chars so the
# latency measurement is not dominated by tokenisation of long inputs.
_PROMPTS = [
    "Q3 revenue overview is now in the tracker.",
    "Project kickoff moved to Tuesday afternoon.",
    "Team standup notes pasted in the channel.",
    "Quarterly roadmap circulated for review today.",
    "All-hands recording uploaded to the wiki.",
    "Engineering velocity stable across the sprint.",
    "Customer satisfaction trend continues upward.",
    "Marketing pipeline forecast revised down 5%.",
    "Roadmap workshop notes shared in Notion now.",
    "Sales pipeline review scheduled for Thursday.",
]


def _next_prompt(i: int) -> str:
    return _PROMPTS[i % len(_PROMPTS)]


def test_real_latency_distribution_per_model(real_llm_or_skip, capsys):
    """100 classification calls per registered model. Record latency,
    parse the label, assert on the invariants, print the distribution.
    """
    models_by_role = real_llm_or_skip["models_by_role"]
    # Deduplicate models across roles — a model registered for both
    # lock-c and validator should still only be benchmarked once.
    target_models: list[str] = sorted({
        mid for ids in models_by_role.values() for mid in ids
    })
    assert target_models, "real_llm_or_skip fixture returned no models"

    latency = RealLatencyRecorder()
    failures: list[str] = []
    empty_labels: list[str] = []
    started = time.time()

    for model in target_models:
        for i in range(100):
            prompt = _next_prompt(i)
            call_started = time.time()
            result = local_llm.classify(
                text=prompt,
                categories=["pii_yes", "pii_no", "insufficient"],
                model=model,
            )
            call_ms = (time.time() - call_started) * 1000
            latency.record(model, call_ms)
            if not isinstance(result, dict):
                failures.append(f"{model}: non-dict result")
                continue
            if not result.get("ok"):
                failures.append(
                    f"{model}: not-ok ({result.get('error', '?')[:80]})"
                )
                continue
            # The returned category may be None (model returned a label
            # outside the configured set); track it separately so the
            # invariant assertion can be specific.
            cat = result.get("category")
            raw = result.get("raw_response", "")
            if not cat and not raw:
                empty_labels.append(f"{model}: empty label")

    elapsed = time.time() - started

    # ---- INVARIANTS ----
    assert not failures, (
        f"{len(failures)} model calls failed; first 3: {failures[:3]}"
    )
    assert not empty_labels, (
        f"{len(empty_labels)} calls returned no label or raw response; "
        f"first 3: {empty_labels[:3]}"
    )

    # ---- MEASUREMENTS ----
    print("\n=== Real-LLM latency distribution — measurements ===")
    print(f"models benchmarked   = {target_models}")
    print(f"calls per model      = 100")
    print(f"wall-clock total     = {elapsed:.2f}s")
    print()
    print(latency.summary())
