#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Measure Privacy Lock recall + precision against a labelled corpus (RV-04).

The Lock is RVND's headline claim — "fail-closed: when in doubt it stops
rather than leaks" — but its detection had no number behind it: the tests
were pass/fail on hand-picked payloads. This measures the DETERMINISTIC tiers
(Tier B regex + Tier B+ confusable) against a labelled corpus and reports:

  * per-category Tier B recall  (found / total, over verified valid PII);
  * overall Tier B recall;
  * Tier B+ confusable recall   (homoglyph-hidden PII caught after folding);
  * false-positive rate on clean / adversarial text.

Tier C (semantic ensemble) is deliberately OUT OF SCOPE here: its recall
depends on a real local model, so it cannot carry a stable committed floor —
measuring it under the deterministic mock backend would measure the mock, not
the product. Tier C recall needs a separate model-eval harness.

Reproduce (deterministic; no randomness, no network, no LLM):

    PYTHONPATH=src python3 eval/lock_recall/measure_recall.py
    PYTHONPATH=src python3 eval/lock_recall/measure_recall.py --json   # machine-readable

The pytest gate (server/tests/test_lock_recall_gate.py) imports ``measure``
and asserts the numbers against docs/evidence/lock-recall-baseline.json.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from workspaces.lock.core import (  # noqa: E402
    _detect_confusable_bypass,
    tier_b_scan_text,
)
import corpus  # noqa: E402  (sibling module; path inserted above)


def _categories(text: str) -> set[str]:
    """The set of Tier B category labels the scan reports for ``text``."""
    out: set[str] = set()
    for f in tier_b_scan_text(text):
        marker = "regex matched pattern: "
        if marker in f.detail:
            out.add(f.detail.split(marker, 1)[1])
    return out


def measure() -> dict:
    """Run the full bench and return a structured result (no printing)."""
    # --- Tier B per-category recall ---
    per_cat_total: dict[str, int] = defaultdict(int)
    per_cat_hit: dict[str, int] = defaultdict(int)
    recall_misses: list[dict] = []
    for text, expected in corpus.RECALL_PROBES:
        per_cat_total[expected] += 1
        found = _categories(text)
        if expected in found:
            per_cat_hit[expected] += 1
        else:
            recall_misses.append({"category": expected, "text": text,
                                  "found": sorted(found)})

    per_category = {
        cat: {"hit": per_cat_hit[cat], "total": per_cat_total[cat],
              "recall": round(per_cat_hit[cat] / per_cat_total[cat], 4)}
        for cat in sorted(per_cat_total)
    }
    total_probes = sum(per_cat_total.values())
    total_hits = sum(per_cat_hit.values())
    overall_recall = round(total_hits / total_probes, 4) if total_probes else 0.0

    # --- Tier B+ confusable recall ---
    conf_total = len(corpus.CONFUSABLE_PROBES)
    conf_hit = sum(1 for t in corpus.CONFUSABLE_PROBES if _detect_confusable_bypass(t))
    confusable_recall = round(conf_hit / conf_total, 4) if conf_total else 0.0

    # --- false-positive rate on the negative set ---
    neg_total = len(corpus.NEGATIVE_PROBES)
    fp_rows = [{"text": t, "flagged": sorted(_categories(t))}
               for t in corpus.NEGATIVE_PROBES if _categories(t)]
    fp_rate = round(len(fp_rows) / neg_total, 4) if neg_total else 0.0

    return {
        "schema": "lock-recall-1",
        "tier_b": {
            "overall_recall": overall_recall,
            "total_probes": total_probes,
            "total_hits": total_hits,
            "per_category": per_category,
            "misses": recall_misses,
        },
        "tier_b_plus_confusable": {
            "recall": confusable_recall, "hit": conf_hit, "total": conf_total,
        },
        "precision": {
            "negative_total": neg_total,
            "false_positives": len(fp_rows),
            "fp_rate": fp_rate,
            "fp_rows": fp_rows,
        },
        "covered_categories": corpus.COVERED_CATEGORIES,
    }


def _print_report(r: dict) -> None:
    tb = r["tier_b"]
    print("=== Privacy Lock recall/precision bench (RV-04) ===")
    print(f"Tier B overall recall: {tb['overall_recall']:.3f} "
          f"({tb['total_hits']}/{tb['total_probes']} probes)")
    print("\nper-category recall:")
    for cat, s in tb["per_category"].items():
        flag = "" if s["recall"] == 1.0 else "  <-- gap"
        print(f"  {cat:24} {s['hit']}/{s['total']}  {s['recall']:.2f}{flag}")
    if tb["misses"]:
        print("\nrecall misses (valid PII the Tier B scan did not flag):")
        for m in tb["misses"]:
            print(f"  [{m['category']}] {m['text']!r} -> found {m['found']}")
    cp = r["tier_b_plus_confusable"]
    print(f"\nTier B+ confusable recall: {cp['recall']:.3f} "
          f"({cp['hit']}/{cp['total']})")
    pr = r["precision"]
    print(f"\nfalse-positive rate on clean/adversarial: {pr['fp_rate']:.3f} "
          f"({pr['false_positives']}/{pr['negative_total']})")
    for row in pr["fp_rows"]:
        print(f"  FP {row['flagged']} <- {row['text']!r}")


def main() -> int:
    r = measure()
    if "--json" in sys.argv[1:]:
        print(json.dumps(r, indent=1))
    else:
        _print_report(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
