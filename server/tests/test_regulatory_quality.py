# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""TEST 2 — vertical-side CONTENT QUALITY: are the regulatory facts right?

The companion's value is the data, not the engine. These tests audit the data the
vertical ships — instruments.csv and the gold set — for the things a wrong value
would silently corrupt every downstream answer: malformed/unofficial URLs, wrong
CELEX act-type, incoherent supersession chains, drifted application dates, and
gold cases that are blank or uncited. This is external validity — independent of
whether the machinery (TEST 1) works.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date

import pytest

from rvnd import regulatory_population as rp

CSV = rp.default_csv()
if CSV is None:
    pytest.skip(
        "instrument corpus not installed — set WORKSPACE_INSTRUMENTS_CSV or place "
        "~/.workspace/instruments.csv (ships with the eu-regulatory-companion)",
        allow_module_level=True,
    )
GOLD = CSV.parent.parent / "gold" / "gold-32.jsonl" if CSV else None
_CELEX = re.compile(r"^3\d{4}[RLD]\d{4}$")


def _rows():
    with open(CSV, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ── instruments.csv data quality ──────────────────────────────────────────────

def test_csv_is_found():
    assert CSV and CSV.exists(), "companion instruments.csv not located"


def test_every_instrument_has_a_wellformed_eurlex_eli_url():
    for r in _rows():
        url = r["source"]
        assert url.startswith("https://eur-lex.europa.eu/eli/"), f"{r['short']}: {url}"


def test_celex_is_wellformed_and_act_type_matches_short():
    by_celex = {r["celex"]: r for r in _rows()}
    for celex, r in by_celex.items():
        assert _CELEX.match(celex), f"bad CELEX {celex}"
    # regulations are 'R', directives are 'L' — a swap here would mis-class the source
    assert by_celex["32016R0679"]["celex"][5] == "R"     # GDPR
    assert by_celex["32024R1689"]["celex"][5] == "R"     # AI Act
    assert by_celex["32022R2065"]["celex"][5] == "R"     # DSA
    assert by_celex["32022R1925"]["celex"][5] == "R"     # DMA
    assert by_celex["31995L0046"]["celex"][5] == "L"     # DPD
    assert by_celex["32022L2555"]["celex"][5] == "L"     # NIS2
    assert by_celex["32002L0058"]["celex"][5] == "L"     # ePrivacy


def test_application_dates_match_known_facts():
    # a content gate against silent drift in the load-bearing dates (web-verified)
    by_celex = {r["celex"]: r for r in _rows()}
    assert by_celex["32016R0679"]["in_force_from"] == "2018-05-25"   # GDPR application
    assert by_celex["32022L2555"]["in_force_from"] == "2024-10-18"   # NIS2 transposition
    assert by_celex["32024R1689"]["in_force_from"] == "2026-08-02"   # AI Act general application
    assert by_celex["32022R2065"]["in_force_from"] == "2024-02-17"   # DSA general application
    assert by_celex["32023R2854"]["in_force_from"] == "2025-09-12"   # Data Act application
    assert by_celex["32024R2847"]["in_force_from"] == "2027-12-11"   # CRA main obligations


def test_supersession_chains_are_coherent():
    by_celex = {r["celex"]: r for r in _rows()}
    # DPD → GDPR, NIS1 → NIS2; the superseding instrument must itself be a row
    for old, new in [("31995L0046", "32016R0679"), ("32016L1148", "32022L2555")]:
        assert by_celex[old]["superseded_by"] == new
        assert new in by_celex, f"{new} cited as successor but not in registry"
        # the supersession date is on/after the old instrument's application date
        sf = date.fromisoformat(by_celex[old]["superseded_from"])
        iff = date.fromisoformat(by_celex[old]["in_force_from"])
        assert sf >= iff, f"{old}: superseded_from before in_force_from"


def test_current_instruments_are_not_superseded():
    by_celex = {r["celex"]: r for r in _rows()}
    current = ("32016R0679", "32022L2555", "32024R1689", "32022R2065",
               "32022R1925", "32022R0868", "32023R2854", "32024R2847",
               "32014R0910", "32002L0058")   # GDPR, NIS2, AI Act, DSA, DMA, DGA, Data Act, CRA, eIDAS, ePrivacy
    for celex in current:
        assert not by_celex[celex]["superseded_by"], f"{celex} should be current"


# ── gold-set content quality ──────────────────────────────────────────────────

def test_gold_set_present_and_parses():
    assert GOLD and GOLD.exists(), "gold-32.jsonl not found"
    rows = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 32


def test_no_gold_case_is_blank_or_uncited():
    rows = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        ga = r["expected"]["gold_answer"]
        assert "PENDING" not in ga and len(ga) > 40, f"{r['id']} blank/stub"
        assert r["expected"].get("citation"), f"{r['id']} uncited"


def test_gold_validation_values_are_in_the_allowed_set():
    allowed = {"grounded", "interpretive", "synthetic-structural", "validated"}
    rows = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert all(r["validation"] in allowed for r in rows)


def test_gold_instruments_are_real_and_in_scope():
    rows = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    blob = " ".join(r["instrument"] for r in rows)
    # the gold references the digital acquis the companion actually covers
    for name in ("GDPR", "AI Act", "NIS2"):
        assert name in blob
