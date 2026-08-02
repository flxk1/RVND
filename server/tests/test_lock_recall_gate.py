# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-04 / RV-15: the Privacy Lock recall bench is a CI-enforced ratchet.

The bench (server/eval/lock_recall/) put the first real number behind the
Lock's headline "it catches PII" claim. This gate freezes that number:
per-category Tier B recall and Tier B+ confusable recall must stay at or
above the committed floors in docs/evidence/lock-recall-baseline.json, and
false alarms on clean prose must stay under the ceiling. A detection
regression — a refactor that quietly stops matching IBANs, say — fails here
instead of shipping silent.

Deterministic tiers only (regex + confusable folding); Tier C semantic recall
depends on a real model and is measured elsewhere. When a fix improves recall,
raise the floor so the gain is protected (bidirectional ratchet).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.security  # privacy-detection integrity

_REPO = Path(__file__).resolve().parents[2]
_BASELINE = _REPO / "docs" / "evidence" / "lock-recall-baseline.json"
sys.path.insert(0, str(_REPO / "server" / "eval" / "lock_recall"))

import measure_recall  # noqa: E402  (path inserted above)


@pytest.fixture(scope="module")
def measured() -> dict:
    return measure_recall.measure()


@pytest.fixture(scope="module")
def baseline() -> dict:
    return json.loads(_BASELINE.read_text())


def test_overall_tier_b_recall_meets_floor(measured, baseline):
    floor = baseline["tier_b"]["overall_recall_floor"]
    got = measured["tier_b"]["overall_recall"]
    assert got >= floor, (
        f"Tier B overall recall {got:.3f} fell below floor {floor:.3f} — "
        f"misses: {measured['tier_b']['misses']}")


def test_no_category_recall_regressed(measured, baseline):
    floors = baseline["tier_b"]["per_category_floor"]
    measured_cat = measured["tier_b"]["per_category"]
    regressions = []
    for cat, floor in floors.items():
        got = measured_cat.get(cat, {}).get("recall")
        if got is None:
            regressions.append(f"{cat}: no longer measured (dropped from corpus)")
        elif got < floor:
            regressions.append(f"{cat}: {got:.2f} < floor {floor:.2f}")
    assert not regressions, "Tier B recall regressed:\n  " + "\n  ".join(regressions)


def test_corpus_still_covers_every_floored_category(measured, baseline):
    """A category silently dropped from the corpus is itself a regression —
    the floor would pass vacuously otherwise."""
    floored = set(baseline["tier_b"]["per_category_floor"])
    covered = set(measured["covered_categories"])
    missing = floored - covered
    assert not missing, f"corpus no longer covers floored categories: {sorted(missing)}"


def test_confusable_recall_meets_floor(measured, baseline):
    floor = baseline["tier_b_plus_confusable"]["recall_floor"]
    got = measured["tier_b_plus_confusable"]["recall"]
    assert got >= floor, (
        f"Tier B+ confusable recall {got:.3f} fell below floor {floor:.3f}")


def test_false_positive_rate_under_ceiling(measured, baseline):
    ceiling = baseline["precision"]["fp_rate_ceiling"]
    got = measured["precision"]["fp_rate"]
    assert got <= ceiling, (
        f"false-positive rate {got:.3f} exceeded ceiling {ceiling:.3f} — "
        f"clean prose now flagged: {measured['precision']['fp_rows']}")


def test_floor_is_not_stale_relative_to_measured(measured, baseline):
    """Gentle nudge: if measured recall for a category sits a full 0.25 above
    its floor, the floor is stale — raise it so the gain is protected. This
    keeps the ratchet tightening instead of rotting."""
    floors = baseline["tier_b"]["per_category_floor"]
    measured_cat = measured["tier_b"]["per_category"]
    stale = [
        f"{cat}: measured {measured_cat[cat]['recall']:.2f} >> floor {floor:.2f}"
        for cat, floor in floors.items()
        if cat in measured_cat and measured_cat[cat]["recall"] - floor > 0.25
    ]
    assert not stale, (
        "recall improved well above these floors — raise them in "
        "lock-recall-baseline.json to lock the gain:\n  " + "\n  ".join(stale))
