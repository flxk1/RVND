# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Detection completeness: the bigger problem than Ashby — the
solver acts only on what it DETECTS, and the completeness of detection is
unknown (open-world). This module does not pretend to close the open world;
it MEASURES and DECLARES the residual, and uses the signal to make human
oversight TARGETED instead of blanket.

Three signals:
  * negative space — a type model says what issue types a document of this
    class usually carries; expected-but-absent are KNOWN-unknowns (the
    biggest lever: unknown-unknowns become a checklist gap);
  * dark fraction — the share of the surface no detector could classify (a
    proxy for unknown-unknowns: not what was missed, but that something was);
  * honest floor — completeness is NEVER certified 'high' without a type
    model to check against (open-world: absence of a model ≠ presence of
    nothing).

Claims under test (written BEFORE the logic):
  E1  expected-but-absent issue types are reported (negative space)
  E2  the dark fraction is computed from covered vs total surface
  E3  low completeness → escalate=True (targeted oversight); high → False
  E4  a complete doc (all expected present, low dark) → band 'high'
  E5  OPEN-WORLD FLOOR: an unknown doc type (no model) is never 'high', even
      with full detection and zero dark — capped at 'medium', escalate True
  E6  detected-but-unexpected types are surfaced too (the model is partial)
  E7  deterministic
"""
from __future__ import annotations


from workspaces.completeness import completeness_report, TYPE_MODELS


def test_expected_but_absent_is_reported():                       # E1
    rep = completeness_report(
        "services-contract-de",
        detected_types=["liability_cap", "data_processing"],
        covered_chars=900, total_chars=1000)
    assert "ip_assignment" in rep["expected_absent"]
    assert "liability_cap" not in rep["expected_absent"]


def test_dark_fraction_from_coverage():                           # E2
    rep = completeness_report("services-contract-de",
                              detected_types=list(TYPE_MODELS["services-contract-de"]),
                              covered_chars=600, total_chars=1000)
    assert rep["dark_fraction"] == 0.4


def test_low_completeness_escalates_high_does_not():              # E3
    low = completeness_report("services-contract-de",
                              detected_types=["liability_cap"],
                              covered_chars=300, total_chars=1000)
    assert low["band"] == "low" and low["escalate"] is True

    full = list(TYPE_MODELS["services-contract-de"])
    high = completeness_report("services-contract-de",
                               detected_types=full,
                               covered_chars=950, total_chars=1000)
    assert high["band"] == "high" and high["escalate"] is False


def test_complete_doc_is_high_band():                             # E4
    full = list(TYPE_MODELS["services-contract-de"])
    rep = completeness_report("services-contract-de", detected_types=full,
                              covered_chars=980, total_chars=1000)
    assert rep["band"] == "high"
    assert rep["expected_absent"] == []


def test_open_world_floor_unknown_type_never_high():             # E5
    rep = completeness_report("unmodelled-doc-type",
                              detected_types=["a", "b", "c"],
                              covered_chars=1000, total_chars=1000)
    assert rep["band"] != "high"          # cannot certify without a model
    assert rep["band"] == "medium"
    assert rep["escalate"] is True
    assert rep["has_type_model"] is False


def test_unexpected_detected_types_are_surfaced():               # E6
    rep = completeness_report("services-contract-de",
                              detected_types=["liability_cap", "alien_clause"],
                              covered_chars=900, total_chars=1000)
    assert "alien_clause" in rep["unexpected"]


def test_deterministic():                                         # E7
    a = completeness_report("services-contract-de",
                            detected_types=["liability_cap"],
                            covered_chars=500, total_chars=1000)
    b = completeness_report("services-contract-de",
                            detected_types=["liability_cap"],
                            covered_chars=500, total_chars=1000)
    assert a == b
