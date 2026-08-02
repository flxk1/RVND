# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the extended Tier B regex coverage.

Verifies the 12 PII shapes:
email, phone, iban, us_ssn, url_with_creds, bearer_token, api_key,
ipv4, ipv6, uk_nino, de_personnummer, credit_card (Luhn-gated).
"""

from __future__ import annotations

import pytest

from workspaces.lock.core import (
    tier_b_scan_text, _luhn_ok,
)


def _kinds(text: str) -> set[str]:
    """Return the set of pattern labels Tier B matched in `text`."""
    out: set[str] = set()
    for f in tier_b_scan_text(text):
        # detail = "regex matched pattern: <kind>"
        kind = f.detail.split(":")[-1].strip()
        out.add(kind)
    return out


# ---------------------------------------------------------------------------
# Each pattern catches its target
# ---------------------------------------------------------------------------


def test_email():
    assert "email" in _kinds("contact alex\x40example.com today")


def test_iban_de():
    assert "iban" in _kinds("account: DE89370400440532013000 owner xyz")


def test_us_ssn():
    assert "us_ssn" in _kinds("SSN 123-45-6789 on file")


def test_uk_nino():
    # Valid NINO prefix (Q excluded from the spec). AB is allowed.
    assert "uk_nino" in _kinds("NINO AB123456C verified")


def test_de_personalausweis():
    # New-format DE ID = 9 chars total: starts with one of CFGHJK, then
    # 8 alphanumerics. Surrounding non-word chars give the word boundary.
    assert "de_personnummer" in _kinds("ID C9X8T7R6Q issued 2024")


def test_url_with_creds():
    assert "url_with_creds" in _kinds("clone https://alice:pw123\x40gitlab.example.com/r.git")


def test_bearer_token():
    assert "bearer_token" in _kinds("Authorization: Bearer abcd1234efgh5678ijkl9012")


def test_api_key_aws():
    assert "api_key" in _kinds(
        "aws creds " "AKIA" "IOSFODNN7EXAMPLE rotated last week"
    )


def test_api_key_stripe():
    assert "api_key" in _kinds("stripe " "sk_" "live_abc123def456ghi789jkl")


def test_api_key_github():
    assert "api_key" in _kinds("ghp_" "abcdefghijklmnopqrstuvwxyz1234")


def test_api_key_google():
    assert "api_key" in _kinds("AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def test_ipv4():
    assert "ipv4" in _kinds("client connected from 192.168.1.42")


def test_ipv6():
    assert "ipv6" in _kinds("ipv6 2001:0db8:85a3:0000:0000:8a2e:0370:7334")


def test_credit_card_valid_visa():
    """Famous Visa test number — Luhn-valid."""
    assert "credit_card" in _kinds("card 4111 1111 1111 1111 expires 12/30")


def test_credit_card_valid_mastercard():
    """Luhn-valid Mastercard test number."""
    assert "credit_card" in _kinds("MC 5555555555554444 charged")


def test_credit_card_invalid_luhn_rejected():
    """16-digit string that does NOT pass Luhn must not trip credit_card."""
    kinds = _kinds("order 1234567890123456 placed")
    assert "credit_card" not in kinds, \
        f"Luhn-invalid digit string falsely matched: {kinds}"


def test_phone_simple():
    assert "phone" in _kinds("call +49 30 1234 5678 anytime")


# ---------------------------------------------------------------------------
# Luhn helper sanity
# ---------------------------------------------------------------------------


def test_luhn_known_valid():
    assert _luhn_ok("4111111111111111") is True   # Visa
    assert _luhn_ok("5555555555554444") is True   # MC
    assert _luhn_ok("378282246310005")  is True   # Amex


def test_luhn_known_invalid():
    assert _luhn_ok("4111111111111112") is False  # last digit off
    assert _luhn_ok("1234567890123456") is False
    assert _luhn_ok("12345")             is False  # too short


# ---------------------------------------------------------------------------
# Negatives — these strings MUST NOT match anything
# ---------------------------------------------------------------------------


def test_negative_plain_prose():
    assert _kinds("The quick brown fox jumped over the lazy dog.") == set()


def test_negative_short_digits():
    assert _kinds("only 42 left in stock") == set()


def test_negative_arbitrary_long_digits():
    # 16-digit timestamp-shape, no Luhn validity → no card match
    kinds = _kinds("event timestamp 1739012345678901")
    assert "credit_card" not in kinds


# ---------------------------------------------------------------------------
# Multiple findings in one text
# ---------------------------------------------------------------------------


def test_multiple_kinds_one_text():
    text = (
        "Hi alex\x40example.com — your IBAN DE89370400440532013000 is on file. "
        "SSN 123-45-6789. Card 4111111111111111. Bearer abcdefghijklmnopqrst "
        "calls from 192.168.1.42."
    )
    kinds = _kinds(text)
    expected = {"email", "iban", "us_ssn", "credit_card", "bearer_token", "ipv4"}
    missing = expected - kinds
    assert not missing, f"missing kinds: {missing}; got {kinds}"
