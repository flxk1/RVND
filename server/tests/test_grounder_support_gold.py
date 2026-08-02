# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Gold-set + eval-harness stress tests for the Grounder support gate.

Two layers: (1) the gold-set itself is structurally sound and hard in the
right ways; (2) the harness scores correctly — a perfect oracle passes, an
inverting model is caught fatally, an always-escalating model cannot pass by
escaping, and a sloppy-but-safe model lands where the bar says it should.
The live local-LLM run only fires when WORKSPACE_LOCAL_LLM_URL is configured.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from workspaces.grounder_eval import (
    BAR,
    LABELS,
    load_gold,
    validate_gold,
)
from workspaces.grounder_eval import evaluate as _evaluate

# The corpus is conformance data owned by the language kit and ships in its
# packaged artifact tree; an installed loomground-governance too old to carry it
# skips with the remedy.
from workspaces.loomground_assets import grounder_gold_path

GOLD_CORPUS = grounder_gold_path()
if not GOLD_CORPUS.is_file():
    pytest.skip("gold corpus absent from the installed loomground-governance "
                "artifacts — update the language kit", allow_module_level=True)


def evaluate(classify_fn, **kw):
    """Score against the repository corpus rather than the harness default."""
    return _evaluate(classify_fn, gold_path=GOLD_CORPUS, **kw)

GOLD = {r["id"]: r for r in load_gold(GOLD_CORPUS)}
ORACLE = {r["id"]: r["label"] for r in load_gold(GOLD_CORPUS)}


def _by_pair() -> dict:
    """(claim, quote) -> gold label, for building mock models."""
    return {(r["claim"], r["quote"]): r["label"] for r in load_gold(GOLD_CORPUS)}


# ── the gold-set itself ───────────────────────────────────────────────────────

def test_gold_meets_minimum_size_and_coverage():
    v = validate_gold(GOLD_CORPUS)
    assert v["rows"] >= 32
    assert v["meets_minimum"] is True
    assert set(v["by_label"]) == set(LABELS)


def test_gold_distribution_is_panel_shaped():
    v = validate_gold(GOLD_CORPUS)
    assert v["by_label"]["supports"] >= 16
    assert v["by_label"]["does_not_support"] >= 8
    assert v["by_label"]["insufficient"] >= 8


def test_gold_has_hard_cases_and_domain_spread():
    rows = load_gold(GOLD_CORPUS)
    assert sum(r.get("difficulty") == "hard" for r in rows) >= 8
    assert len({r["domain"] for r in rows}) >= 8
    # cross-lingual cases present (German quotes, English claims)
    assert any("Urheber" in r["quote"] or "GVL" in r["quote"] for r in rows)


def test_gold_quotes_never_equal_claims():
    """A gold pair where quote == claim would test string matching, not
    entailment."""
    for r in load_gold(GOLD_CORPUS):
        assert r["quote"].strip().lower() != r["claim"].strip().lower(), r["id"]


def test_gold_contains_shared_quote_with_opposite_labels():
    """The same quote must appear with different gold labels somewhere —
    models keying on the quote alone (ignoring the claim) cannot pass."""
    by_quote: dict[str, set] = {}
    for r in load_gold(GOLD_CORPUS):
        by_quote.setdefault(r["quote"], set()).add(r["label"])
    assert any(len(v) > 1 for v in by_quote.values())


def test_gold_is_marked_pending_expert_review():
    text = GOLD_CORPUS.read_text(encoding="utf-8")
    assert "pending expert review" in text


# ── harness mechanics ─────────────────────────────────────────────────────────

def test_perfect_oracle_passes():
    table = _by_pair()
    res = evaluate(lambda c, q: table[(c, q)])
    assert res["passed"] is True
    assert res["accuracy_decided"] == 1.0
    assert res["fatal_inversions"] == 0
    assert res["escalations"] == sum(
        1 for l in ORACLE.values() if l == "insufficient")


def test_inverting_model_fails_fatally():
    table = _by_pair()
    flip = {"supports": "does_not_support",
            "does_not_support": "supports",
            "insufficient": "insufficient"}
    res = evaluate(lambda c, q: flip[table[(c, q)]])
    assert res["passed"] is False
    assert res["fatal_inversions"] >= 8          # every contradiction admitted
    assert res["accuracy_decided"] == 0.0


def test_always_escalating_model_cannot_pass_by_escaping():
    res = evaluate(lambda c, q: "insufficient")
    assert res["passed"] is False
    assert res["decided_fraction"] == 0.0
    assert res["fatal_inversions"] == 0          # safe, but useless


def test_always_supports_model_fails():
    res = evaluate(lambda c, q: "supports")
    assert res["passed"] is False
    assert res["fatal_inversions"] >= 8          # admits every refuted claim


def test_safe_escalation_is_not_an_error_but_floors_still_bind():
    """A supports-only model that escalates everything else makes zero
    errors — escalation is never wrong — yet cannot pass: 17/36 decided is
    below the 0.50 floor. Both halves of the posture in one case."""
    table = _by_pair()

    def cautious(c, q):
        label = table[(c, q)]
        return label if label == "supports" else "insufficient"

    res = evaluate(cautious)
    assert res["accuracy_decided"] == 1.0        # no errors…
    assert res["fatal_inversions"] == 0
    assert res["failures"] == []
    assert res["decided_fraction"] < 0.5         # …but too timid
    assert res["passed"] is False

    def cautious_both_ways(c, q):                # decides both decided labels
        label = table[(c, q)]
        return label if label != "insufficient" else "insufficient"

    res2 = evaluate(cautious_both_ways)
    assert res2["accuracy_decided"] == 1.0
    assert res2["decided_fraction"] > 0.7
    assert res2["passed"] is True


def test_overconfidence_on_insufficient_counts_against():
    """Deciding a gold-insufficient case is an error — the gate must not
    manufacture certainty the evidence doesn't carry."""
    table = _by_pair()

    def overconfident(c, q):
        label = table[(c, q)]
        return "supports" if label == "insufficient" else label

    res = evaluate(overconfident)
    assert res["accuracy_decided"] < 0.9
    assert res["passed"] is False
    assert all(f["gold"] == "insufficient" for f in res["failures"])


def test_one_fatal_inversion_is_too_many():
    table = _by_pair()
    poisoned_once = {"done": False}

    def nearly_perfect(c, q):
        label = table[(c, q)]
        if label == "does_not_support" and not poisoned_once["done"]:
            poisoned_once["done"] = True
            return "supports"                    # a single admitted refutation
        return label

    res = evaluate(nearly_perfect)
    assert res["fatal_inversions"] == 1
    assert res["passed"] is False                # despite accuracy ≈ 0.97


def test_noisy_model_scored_deterministically():
    table = _by_pair()
    rng = random.Random(42)

    def noisy(c, q):
        return table[(c, q)] if rng.random() < 0.8 else rng.choice(list(LABELS))

    res1 = evaluate(noisy)
    rng.seed(42)
    res2 = evaluate(noisy)
    assert res1["accuracy_decided"] == res2["accuracy_decided"]
    assert res1["cases"] == res2["cases"]


def test_garbage_predictions_treated_as_escalation():
    res = evaluate(lambda c, q: "definitely!!")
    assert res["decided"] == 0
    assert res["fatal_inversions"] == 0


def test_bar_matches_gold_meta():
    import json
    first = GOLD_CORPUS.read_text(encoding="utf-8").splitlines()[0]
    meta = json.loads(first)["_meta"]["bar"]
    assert meta["accuracy_decided_min"] == BAR["accuracy_decided_min"]
    assert meta["fatal_inversions_max"] == BAR["fatal_inversions_max"]
    assert meta["decided_fraction_min"] == BAR["decided_fraction_min"]


# ── live run (only when an endpoint is configured) ────────────────────────────

@pytest.mark.skipif(not os.environ.get("WORKSPACE_LOCAL_LLM_URL"),
                    reason="no local-LLM endpoint configured")
def test_live_local_llm_against_gold():
    from workspaces.grounder_eval import evaluate_local_llm
    res = evaluate_local_llm()
    # report either way; the assertion is the production gate
    print(f"\nlive gold run: acc={res['accuracy_decided']} "
          f"decided={res['decided_fraction']} fatal={res['fatal_inversions']}")
    assert res["fatal_inversions"] == 0
    assert res["passed"], res["failures"]
