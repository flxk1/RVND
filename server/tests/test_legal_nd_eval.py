# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Legal-ND eval: measure deontic-operator extraction on a small gold set, and
prove the confidence→oversight escalation (your steer) — low-confidence
extractions route through the oversight policy instead of being silently emitted.

    python -m pytest runtime/tests/test_legal_nd_eval.py     # validate + guard
    python runtime/tests/test_legal_nd_eval.py               # print the baseline
"""
from __future__ import annotations

from rvnd.deontic_facets import extract_formulae, obligation_is_grounded
from rvnd import governance as gov

# (sentence, expected operator | None).  O=obligation P=permission F=prohibition.
# There is no "R": a right is CONSTRUCTED as P (a privilege/liberty) or the
# correlative O (a claim), carried by the Hohfeld incident — never a primitive.
# None = no deontic norm → the extractor must NOT fabricate one.
GOLD = [
    ("The provider shall establish a risk management system.", "O"),
    ("Member States shall ensure that providers comply.", "O"),
    ("The deployer may use the system for its intended purpose.", "P"),
    ("A provider shall not place a prohibited AI system on the market.", "F"),
    ("The controller must not retain data longer than necessary.", "F"),
    ("The data subject has the right to obtain erasure of personal data.", "P"),
    ("Providers are required to draw up technical documentation.", "O"),
    ("The user is entitled to lodge a complaint.", "P"),
    ("Operators should consider the residual risks.", "O"),
    ("This Regulation applies to providers placing systems on the market.", None),
]


def _score():
    rows = []
    for text, exp in GOLD:
        ops = [f.operator for f in extract_formulae(text, gated_by_fingerprint=False)]
        if exp is None:
            ok = (len(ops) == 0)                  # must not fabricate a norm
        else:
            ok = exp in ops
        rows.append((text, exp, ops, ok))
    correct = sum(1 for r in rows if r[3])
    return rows, correct, len(rows)


def test_clean_catalogued_modals_are_correct():
    # the unambiguous modal reads must be right — these anchor the baseline
    clean = {
        "The provider shall establish a risk management system.": "O",
        "The deployer may use the system for its intended purpose.": "P",
        "A provider shall not place a prohibited AI system on the market.": "F",
        "The data subject has the right to obtain erasure of personal data.": "P",
    }
    for text, exp in clean.items():
        ops = [f.operator for f in extract_formulae(text, gated_by_fingerprint=False)]
        assert exp in ops, f"{text!r} → {ops}, expected {exp}"


def test_no_fabrication_on_non_normative_text():
    ops = [f.operator for f in extract_formulae(GOLD[-1][0], gated_by_fingerprint=False)]
    assert ops == [], f"fabricated a norm from applicability text: {ops}"


def test_baseline_accuracy_guard():
    _rows, correct, total = _score()
    acc = correct / total
    assert acc >= 0.6, f"legal-ND operator accuracy regressed below baseline: {acc:.0%}"


# --- span-grounding: no span, no obligation (a fuller metric than operator-only) ---

def _grounding_rate():
    norm = [(t, e) for t, e in GOLD if e is not None]
    grounded = 0
    for text, _ in norm:
        fs = extract_formulae(text, gated_by_fingerprint=False)
        if fs and obligation_is_grounded(fs[0]):
            grounded += 1
    return grounded, len(norm)


def test_full_obligation_grounding_rate_guard():
    g, n = _grounding_rate()
    assert g / n >= 0.6, f"grounded-obligation rate regressed: {g}/{n}"


def test_span_grounding_routes_through_oversight(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    f = extract_formulae("The provider shall establish a risk management system.",
                         gated_by_fingerprint=False)[0]
    assert obligation_is_grounded(f) is True
    ok = gov.decide_output(folder, grounded=True, oversight_level="approve",
                           action_class="obligation", log_root=tmp_path / "log")
    assert ok["verdict"] == "permit"
    f.bearer = "(unspecified)"                     # strip the bearer → no longer grounded
    assert obligation_is_grounded(f) is False
    held = gov.decide_output(folder, grounded=False, oversight_level="approve",
                             action_class="obligation", log_root=tmp_path / "log")
    assert held["verdict"] == "hold"               # no span/bearer → escalate, don't emit


# --- the steer: confidence + escalation routed by oversight policy ---

def test_low_confidence_extraction_escalates_per_oversight(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    # the deterministic extractor emits ~0.7 confidence; default floor 0.85
    d_approve = gov.decide_confidence(folder, confidence=0.7, oversight_level="approve",
                                      action_class="legal-extract", log_root=tmp_path / "log")
    assert d_approve["verdict"] == "hold"          # HITL: agent stops, you decide
    d_notify = gov.decide_confidence(folder, confidence=0.7, oversight_level="notify",
                                     action_class="legal-extract", log_root=tmp_path / "log")
    assert d_notify["verdict"] == "permit" and d_notify["flagged"] is True   # HOTL: flagged, runs
    d_high = gov.decide_confidence(folder, confidence=0.95, oversight_level="approve",
                                   action_class="legal-extract", log_root=tmp_path / "log")
    assert d_high["verdict"] == "permit" and d_high["flagged"] is False
    assert d_approve["audit_id"]                   # on the signed chain


if __name__ == "__main__":
    rows, correct, total = _score()
    print(f"\n{'sentence':<60} {'exp':<4} {'got':<10} ok")
    print("-" * 84)
    for text, exp, ops, ok in rows:
        print(f"{text[:58]:<60} {str(exp):<4} {str(ops):<10} {'Y' if ok else 'N'}")
    print("-" * 84)
    print(f"deontic-operator accuracy (baseline): {correct}/{total} = {correct/total:.0%}")
    g, n = _grounding_rate()
    print(f"grounded-obligation rate (span+bearer+action): {g}/{n} = {g/n:.0%}")
    print("note: deterministic reads emit ~0.7 confidence < default floor 0.85, so under "
          "oversight=approve every extraction escalates to the human (decide_confidence → hold). "
          "Calibrating per-workspace floor vs confidence is the next lever.")
