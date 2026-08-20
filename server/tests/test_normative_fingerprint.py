# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Precision/recall evaluation of the normative fingerprint against the
labeled corpora.

The corpora live in :mod:`tests.fixtures.normative_corpus` (tuning set —
patterns were iterated against this) and :mod:`tests.fixtures.normative_holdout`
(held-out set — patterns never saw these during tuning).

Targets baked into CI:

- TUNING:  F1 ≥ 0.97, P ≥ 0.95, R ≥ 0.95
- HOLDOUT: F1 ≥ 0.92, P ≥ 0.92, R ≥ 0.85

The holdout thresholds are deliberately tighter on precision than on recall
because for legal/regulatory ND work a false-positive (claiming non-rules
as rules) is more damaging than a false-negative (missing a rule). Precision
≥ 0.92 means: at most 1 in 13 fragments the fingerprint claims is normative
is actually noise. Recall ≥ 0.85 means: at most 1 in 7 actual rules slips
through.

If the fingerprint regresses past these thresholds, this test fails and
forces a re-tune. The score breakdown is printed even on success so a
regression has a clear signal in CI output.
"""

from __future__ import annotations

from typing import Iterable


from workspaces import RuleFacet, extract_rules
from workspaces.nd_routing import (
    DefaultClassifier,
    NORMATIVE_THRESHOLD,
    score_normative,
)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fixtures.normative_corpus import CORPUS
from fixtures.normative_holdout import HOLDOUT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _confusion(data: Iterable[tuple[str, str, str]]):
    c = DefaultClassifier()
    tp = fp = fn = tn = 0
    errors: list[tuple[str, str, str, float]] = []
    for label, text, note in data:
        cl = c.classify(text)
        pred = "normative" if cl.primary_type == "normative" else "non-normative"
        score, _ = score_normative(text)
        if label == pred:
            if label == "normative":
                tp += 1
            else:
                tn += 1
        else:
            errors.append((label, note, cl.primary_type, score))
            if label == "normative":
                fn += 1
            else:
                fp += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return tp, fp, tn, fn, precision, recall, f1, errors


# ---------------------------------------------------------------------------
# Tuning-set evaluation
# ---------------------------------------------------------------------------


def test_normative_fingerprint_tuning_set():
    """Patterns were tuned against this set — F1 must stay ≥ 0.97."""
    tp, fp, tn, fn, p, r, f1, errors = _confusion(CORPUS)
    print(f"\nTUNING:  F1={f1:.3f}  P={p:.3f}  R={r:.3f}  "
          f"TP={tp} FP={fp} TN={tn} FN={fn}")
    for label, note, t, s in errors:
        print(f"  miss [{s:.2f}] {label}: {note} → {t}")
    assert p >= 0.95, f"tuning precision {p:.3f} regressed below 0.95"
    assert r >= 0.95, f"tuning recall {r:.3f} regressed below 0.95"
    assert f1 >= 0.97, f"tuning F1 {f1:.3f} regressed below 0.97"


# ---------------------------------------------------------------------------
# Held-out validation set — the real generalisation signal
# ---------------------------------------------------------------------------


def test_normative_fingerprint_holdout_set():
    """Patterns never saw this set during tuning — F1 must stay ≥ 0.92."""
    tp, fp, tn, fn, p, r, f1, errors = _confusion(HOLDOUT)
    print(f"\nHOLDOUT: F1={f1:.3f}  P={p:.3f}  R={r:.3f}  "
          f"TP={tp} FP={fp} TN={tn} FN={fn}")
    for label, note, t, s in errors:
        print(f"  miss [{s:.2f}] {label}: {note} → {t}")
    # Tighter on precision: false-positives in normative classification are
    # more damaging than false-negatives for legal work.
    assert p >= 0.92, f"holdout precision {p:.3f} regressed below 0.92"
    assert r >= 0.85, f"holdout recall {r:.3f} regressed below 0.85"
    assert f1 >= 0.92, f"holdout F1 {f1:.3f} regressed below 0.92"


# ---------------------------------------------------------------------------
# Score-distribution sanity checks
# ---------------------------------------------------------------------------


def test_threshold_separates_distributions():
    """Across both corpora, positives should average well above threshold and
    negatives well below. Wide separation = robust fingerprint.
    """
    pos_scores = []
    neg_scores = []
    for label, text, _ in list(CORPUS) + list(HOLDOUT):
        s, _ = score_normative(text)
        (pos_scores if label == "normative" else neg_scores).append(s)
    pos_avg = sum(pos_scores) / len(pos_scores)
    neg_avg = sum(neg_scores) / len(neg_scores)
    pos_min = min(pos_scores)
    neg_max = max(neg_scores)
    print(f"\nSCORE DIST: pos_avg={pos_avg:.2f} (min={pos_min:.2f})  "
          f"neg_avg={neg_avg:.2f} (max={neg_max:.2f})  "
          f"threshold={NORMATIVE_THRESHOLD}")
    # The minimum positive must clear the threshold (zero FNs on combined data).
    assert pos_min >= NORMATIVE_THRESHOLD, (
        f"a positive scored {pos_min:.2f}, below threshold {NORMATIVE_THRESHOLD}"
    )
    # The maximum negative must stay below the threshold.
    assert neg_max < NORMATIVE_THRESHOLD, (
        f"a negative scored {neg_max:.2f}, at or above threshold {NORMATIVE_THRESHOLD}"
    )
    # Average separation: positives should average ≥ 0.5 points higher than
    # negatives. Tighter than just "threshold-separable" — proves the fingerprint
    # produces clear signal, not just signal-at-the-edge.
    assert (pos_avg - neg_avg) >= 0.5, (
        f"positive/negative averages too close: {pos_avg:.2f} vs {neg_avg:.2f}"
    )


# ---------------------------------------------------------------------------
# Rule extractor — precision (no spurious extraction on non-normative)
# ---------------------------------------------------------------------------


def test_rule_extractor_no_spurious_on_negatives():
    """The extractor must produce NO rules from non-normative fragments.

    Gated-by-fingerprint default ensures content the classifier rejected never
    reaches the extractor; the pronoun stoplist is belt-and-braces against
    casual prose ("he shall return") sneaking through.
    """
    leakage: list[tuple[str, list[RuleFacet]]] = []
    for label, text, note in list(CORPUS) + list(HOLDOUT):
        if label != "non-normative":
            continue
        rules = extract_rules(text)
        if rules:
            leakage.append((note, rules))
    assert leakage == [], f"spurious rule extraction on negatives: {leakage}"


def test_rule_extractor_extracts_from_positives():
    """A solid majority of normative positives must yield ≥ 1 RuleFacet.

    Coverage on the harder structures (Wer-pattern, statutory definitions,
    indirect operative-verb forms) is a future extractor target. The regex floor
    should cover at least 55% of the combined positive corpus.
    """
    extracted = total = 0
    no_rule: list[str] = []
    for label, text, note in list(CORPUS) + list(HOLDOUT):
        if label != "normative":
            continue
        total += 1
        rules = extract_rules(text)
        if rules:
            extracted += 1
        else:
            no_rule.append(note)
    rate = extracted / total if total else 0
    print(f"\nEXTRACTOR: {extracted}/{total} = {rate:.1%}")
    print("  positives without extraction (future extractor targets):")
    for n in no_rule:
        print(f"    - {n}")
    assert rate >= 0.55, (
        f"extractor coverage {rate:.1%} below target 55% "
        f"(missing: {no_rule})"
    )


# ---------------------------------------------------------------------------
# Modal classification sanity
# ---------------------------------------------------------------------------


def test_rule_extractor_classifies_modals():
    """Modal classes (obligation / prohibition / right / permission) are
    correctly assigned for canonical examples.
    """
    cases = [
        ("The controller shall ensure compliance.", "obligation"),
        ("The processor shall not engage another processor without consent.", "prohibition"),
        ("The data subject shall have the right to obtain confirmation.", "right"),
        ("The Commission may, by means of implementing acts, adopt technical specifications.", "permission"),
        ("Der Anbieter muss sicherstellen, dass.", "obligation"),
    ]
    for text, expected in cases:
        rules = extract_rules(text, gated_by_fingerprint=False)
        assert rules, f"no extraction for: {text!r}"
        assert rules[0].modal == expected, (
            f"text={text!r}: got modal={rules[0].modal!r}, expected {expected!r}"
        )
