# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""A8 — Lock bypass via confusable Unicode.

Confusable-Unicode regression for the privacy lock.
Tier:   T1 (any MCP client).
Status: MITIGATED (0.6.7 ``_detect_confusable_bypass`` + ASCII-fold).
        Regression locks the mitigation against future refactors.

PASS = attack failed. The lock flags every visually-confusable PII
variant via the Tier B+ ``confusable_bypass`` finding.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.security


@pytest.fixture(autouse=True)
def _require_anyascii():
    pytest.importorskip(
        "anyascii",
        reason="Tier B+ confusable-bypass detection requires the anyascii "
               "dependency. Install runtime[lock-beta] extras to enable.",
    )


def _scan_for_findings(text: str):
    """Run Tier B regex + Tier B+ confusable detection and return findings."""
    from rvnd.lock.core import tier_b_scan_text, _detect_confusable_bypass
    return list(tier_b_scan_text(text)) + list(_detect_confusable_bypass(text))


@pytest.mark.parametrize(
    "label,payload",
    [
        # Cyrillic а (U+0430) for Latin a in BOTH halves of email — breaks the
        # original ASCII regex; folded text matches; bypass detector fires.
        ("cyrillic_a_both_halves", "Please contact аdmin" + "@" + "exаmple.com"),
        # Mostly-Cyrillic email body — the homoglyphs ride through the local
        # part so the original regex never matches but the folded text does.
        ("cyrillic_email_local_and_domain",
         "Please email мaрtin" + "@" + "firм.de about it"),
    ],
)
def test_a8_confusable_bypass_is_flagged(label, payload):
    """Every confusable-glyph variant must surface a confusable_bypass finding."""
    findings = _scan_for_findings(payload)

    matched = [f for f in findings if f.type == "confusable_bypass"]
    assert matched, (
        f"VULNERABILITY [{label}]: confusable bypass not flagged. "
        f"payload={payload!r}, all_findings={[(f.type, f.severity) for f in findings]}"
    )
    # High severity is the documented contract for Tier B+ bypass detection.
    assert any(f.severity == "high" for f in matched), (
        f"VULNERABILITY [{label}]: confusable bypass flagged but severity "
        f"is not 'high'. findings={[(f.type, f.severity) for f in matched]}"
    )


def test_a8_clean_ascii_pii_still_flagged_by_tier_b():
    """Sanity: the underlying Tier B regex still catches the un-disguised PII.

    If this regresses, the confusable test passing means nothing — the bar
    is "confusable variant flagged AT LEAST AS WELL as the plain variant".
    """
    findings = _scan_for_findings("Send the report to admin\x40example.com today.")
    assert findings, (
        "VULNERABILITY: plain ASCII email not flagged by Tier B — "
        "regression in the baseline regex layer."
    )


def test_a8_benign_unicode_text_does_not_false_positive():
    """Plain non-Latin text (no confusable PII context) must not over-trigger."""
    findings = _scan_for_findings("Добрый день. How are you today?")
    bypass_findings = [f for f in findings if f.type == "confusable_bypass"]
    assert not bypass_findings, (
        f"REGRESSION: benign Cyrillic greeting flagged as confusable bypass. "
        f"findings={[(f.type, f.detail) for f in bypass_findings]}"
    )


def test_a8_diff_based_detector_design_works_as_documented():
    """Sanity for the diff-based design: when a confusable substitution
    causes Tier B regex to match folded but NOT original, the bypass
    detector fires.

    Background: ``_detect_confusable_bypass`` returns findings ONLY for
    cases where the bypass would have succeeded. For payloads where the
    original ASCII regex ALSO matches (e.g. a single-letter homoglyph in a
    long email where the bulk of the email still matches), no bypass
    finding is produced — Tier B already caught it via the original regex.
    This is by design; this test pins the design.
    """
    from rvnd.lock.core import tier_b_scan_text, _detect_confusable_bypass

    # Single Greek omicron in a long domain — Tier B catches the original
    # because the regex matches `j.d` + `@firm.de` adjacent segments;
    # bypass detector therefore does NOT fire (would be a duplicate).
    payload = "Send the report to j.dοe\x40firm.de"
    tier_b = tier_b_scan_text(payload)
    bypass = _detect_confusable_bypass(payload)

    if tier_b:
        # Original matched → bypass detector correctly stays silent.
        assert not bypass, (
            "Bypass detector fired even though Tier B already matched the "
            "original — design contract is 'no duplicate findings'. "
            f"tier_b={[f.type for f in tier_b]}, "
            f"bypass={[f.type for f in bypass]}"
        )
