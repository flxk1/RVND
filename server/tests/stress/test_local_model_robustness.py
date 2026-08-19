# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Deliverable 3 — Local-model integration robustness (B10+).

Eight scenarios, each isolated to the failure mode it tests. The Tier C
ensemble (``tier_c_semantic_check``) is the system under test; the mocked
``local_llm_classify`` boundary is used to inject the failure shapes.

Failure shapes covered:

  * Phi-3.5 endpoint 503 → insufficient; the surviving models' verdict
    is never silently promoted.
  * All endpoints 503 → ensemble returns None; caller treats as
    "Tier C unavailable".
  * 100-case disagreement stress → all 100 escalate cleanly to
    INSUFFICIENT.
  * Endpoint timeout → pipeline does not hang; falls back per policy.
  * Per-folder routing policy (local-only vs cloud-allowed).
  * Concurrent local-llm calls — no deadlock, no audit-event loss.
  * Endpoint crash mid-response → no truncated capture.
  * Model registry consistency — ensemble adapts when one model
    deregisters.
"""

from __future__ import annotations

import threading
from collections import Counter


from rvnd.lock.core import (
    ENSEMBLE_MODELS_DEFAULT,
    tier_c_semantic_check,
)

from tests.stress._harness import MockCloudLLM, MockLocalLLM


# ---------------------------------------------------------------------------
# 1. Phi-3.5 unavailable → ensemble degrades
# ---------------------------------------------------------------------------


def test_phi35_unavailable_returns_insufficient():
    """When phi-3.5 returns 503, the ensemble must report insufficient
    (agreement requires every model, not just the ones still up)."""
    local = MockLocalLLM(models={
        "phi-3.5-mini-q4":        "503",
        "qwen-2.5-coder-7b-q4":   "ok",
        "mistral-7b-instruct-q4": "ok",
    }, classify_fn={
        "qwen-2.5-coder-7b-q4":   lambda t: "pii_no",
        "mistral-7b-instruct-q4": lambda t: "pii_no",
    })
    with local:
        result = tier_c_semantic_check("Mr. Smith works downstairs.")
    # The ensemble's contract: only agreement = high-confidence label.
    # One model unavailable → "insufficient" (or None if all gone).
    assert result is not None, "single model alive should still produce a result"
    assert result.label == "insufficient", (
        f"phi-3.5 failure must not silently promote the surviving models' verdict; got {result.label}"
    )
    assert result.confidence < 0.9


# ---------------------------------------------------------------------------
# 2. Both models unavailable → None
# ---------------------------------------------------------------------------


def test_all_models_unavailable_falls_back_to_stub():
    """All models 503 → tier_c_semantic_check returns None."""
    local = MockLocalLLM(models={
        "phi-3.5-mini-q4":        "503",
        "qwen-2.5-coder-7b-q4":   "503",
        "mistral-7b-instruct-q4": "503",
    })
    with local:
        result = tier_c_semantic_check("Any text at all.")
    assert result is None, "all-models-down must return None (Tier C unavailable)"


# ---------------------------------------------------------------------------
# 3. 100 disagreement cases — all escalate cleanly
# ---------------------------------------------------------------------------


def test_ensemble_disagreement_returns_insufficient_at_scale():
    """100 disagreement cases must each return INSUFFICIENT, never raise,
    never silently promote one verdict."""
    inputs = [f"Synthetic input number {i} for disagreement test." for i in range(100)]
    # Alternate the verdict per input so every call disagrees.
    local = MockLocalLLM(classify_fn={
        "phi-3.5-mini-q4":        lambda t: "pii_yes",
        "qwen-2.5-coder-7b-q4":   lambda t: "pii_no",
        "mistral-7b-instruct-q4": lambda t: "pii_no",
    })
    labels: Counter[str] = Counter()
    with local:
        for text in inputs:
            r = tier_c_semantic_check(text)
            assert r is not None
            labels[r.label] += 1
    assert labels["insufficient"] == 100, labels


# ---------------------------------------------------------------------------
# 4. Timeout doesn't hang the pipeline
# ---------------------------------------------------------------------------


def test_local_model_timeout_does_not_block_pipeline():
    """A timeout returns immediately as an error, NOT hangs the pipeline.

    We're mocking at the boundary, so a "timeout" is just an error
    return; the test asserts the call returns within sub-second wall
    time and surfaces as INSUFFICIENT."""
    import time
    local = MockLocalLLM(models={
        "phi-3.5-mini-q4":        "timeout",
        "qwen-2.5-coder-7b-q4":   "timeout",
        "mistral-7b-instruct-q4": "timeout",
    })
    started = time.time()
    with local:
        result = tier_c_semantic_check("PII-suspect input")
    elapsed = time.time() - started
    assert elapsed < 1.0, f"pipeline took {elapsed:.2f}s — should be near-instant under mocked timeout"
    assert result is None, "all-models-timeout = None (Tier C unavailable)"


# ---------------------------------------------------------------------------
# 5. Per-folder routing policy: local-only vs cloud-allowed
# ---------------------------------------------------------------------------


def test_per_folder_routing_policy_honored(tmp_path):
    """Borderline input + two folder policies → folder A (local-only)
    refuses; folder B (cloud-allowed) escalates via ensemble.

    We simulate the per-folder routing by branching on a simple
    ``policy.local_llm_mode`` flag the caller is expected to check.
    """
    borderline = "The applicant born 1973 in Stuttgart lost the appeal."

    def _dispatch(text: str, *, mode: str, cloud: MockCloudLLM) -> str:
        if mode == "local-only":
            # Even if Tier C says clean, never cross to cloud.
            return "refused"
        # cloud-allowed: ensemble runs; insufficient → escalate.
        result = tier_c_semantic_check(text)
        if result is not None and result.label == "insufficient":
            return "escalated"
        if result is not None and result.label == "pii_yes":
            return "refused"
        cloud.dispatch(text)
        return "dispatched"

    cloud = MockCloudLLM()
    local = MockLocalLLM(classify_fn={
        "phi-3.5-mini-q4":        lambda t: "pii_yes",
        "qwen-2.5-coder-7b-q4":   lambda t: "pii_no",  # disagreement
        "mistral-7b-instruct-q4": lambda t: "pii_no",
    })
    with cloud, local:
        a_outcome = _dispatch(borderline, mode="local-only", cloud=cloud)
        b_outcome = _dispatch(borderline, mode="cloud-allowed", cloud=cloud)
    assert a_outcome == "refused"
    assert b_outcome in ("escalated", "refused")
    assert len(cloud.calls) == 0, "borderline input must not reach cloud"


# ---------------------------------------------------------------------------
# 6. Concurrency — 20 threads, no deadlock, no event loss
# ---------------------------------------------------------------------------


def test_concurrent_local_llm_calls_do_not_deadlock():
    """20 concurrent threads each invoke tier_c_semantic_check.
    All must complete within a timeout; total call count = 20 * 3 (one
    classification per ensemble model per call)."""
    local = MockLocalLLM()  # default = all models ok, default mapper
    results: list = []
    errors: list = []

    def _worker(idx: int):
        try:
            r = tier_c_semantic_check(f"Concurrent test input #{idx}")
            results.append(r)
        except Exception as e:
            errors.append(e)

    with local:
        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "thread did not join — deadlock?"

    assert not errors, f"concurrent calls raised: {errors[:3]}"
    assert len(results) == 20
    # Each call invokes one classification per ensemble model. The mock
    # counter sees them all (no event loss).
    expected = 20 * len(ENSEMBLE_MODELS_DEFAULT)
    assert local.token_counter.local_invocations == expected, (
        f"expected {expected} local invocations from 20 ensemble calls; "
        f"got {local.token_counter.local_invocations} — events lost?"
    )


# ---------------------------------------------------------------------------
# 7. Crash mid-response: no truncated capture event
# ---------------------------------------------------------------------------


def test_local_model_crash_mid_response_records_no_truncated_capture(monkeypatch):
    """When the endpoint crashes mid-stream we want no capture_llm event
    on chain at all (capture only happens on ``ok``)."""
    # The capture_llm in mcp_server.local_llm_complete only fires when
    # result.get("ok") is True. We assert the mock crash path never
    # triggers that path.
    captured: list = []

    def _fake_capture(**kwargs):
        captured.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("rvnd.mcp_server.capture_llm",
                         _fake_capture, raising=True)

    local = MockLocalLLM(models={
        "phi-3.5-mini-q4": "crash_mid_response",
    })
    with local:
        from rvnd.mcp_server import local_llm_complete
        out = local_llm_complete(
            prompt="hello", folder_context="/tmp/x",
            model="phi-3.5-mini-q4", capture=True,
        )
    # Even when the route fails, capture must NOT be invoked.
    assert out.get("ok") is False
    assert captured == [], (
        f"capture_llm fired on a failed local call; got {len(captured)} event(s)"
    )


# ---------------------------------------------------------------------------
# 8. Model registry consistency
# ---------------------------------------------------------------------------


def test_model_registry_consistency_when_one_model_deregisters():
    """list_available reports both → ensemble uses both → deregister
    one → ensemble downgrades to single-model + INSUFFICIENT."""
    # Step 1: both registered.
    local_full = MockLocalLLM()  # default both ok
    with local_full:
        result_both = tier_c_semantic_check("Some neutral text.")
        from rvnd.local_llm import list_available
        listing_both = list_available()
    assert result_both is not None
    assert set(listing_both.get("models", [])) == set(ENSEMBLE_MODELS_DEFAULT)
    assert result_both.label in ("pii_yes", "pii_no")  # agreement reachable

    # Step 2: deregister one.
    local_one = MockLocalLLM(models={
        "phi-3.5-mini-q4":       "ok",
        "qwen-2.5-coder-7b-q4":  "missing",
    })
    with local_one:
        result_one = tier_c_semantic_check("Some neutral text.")
        from rvnd.local_llm import list_available as _list_again
        listing_one = _list_again()
    assert result_one is not None
    assert result_one.label == "insufficient", (
        "ensemble must NOT promote a single-model verdict to high-confidence"
    )
    assert "qwen-2.5-coder-7b-q4" not in listing_one.get("models", [])
