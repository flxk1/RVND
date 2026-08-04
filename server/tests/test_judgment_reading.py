# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Judgment → authority-weighted candidate readings (interpreter-side adapter): readings are
ratification candidates bound to cited provisions, court tiers order authority (judgment over
administrative decision), a non-judgment yields no readings, and the output is deterministic.

Most tests read real decisions from a local corpus that is not part of the repo; point
RVND_TEST_CORPUS / RVND_TEST_FRAMEWORKS at the directories to enable them, otherwise they
skip with that reason.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from workspaces import judgment_reading as JR
from workspaces.adapters.ingest.governance import compiler as P
from workspaces import format_extractors as fx
from workspaces.source_classes import Effect

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

_CORPUS = os.environ.get("RVND_TEST_CORPUS", "")
_FRAMEWORKS = Path(os.environ.get("RVND_TEST_FRAMEWORKS", "") or "/nonexistent")
_BGH = ("2003 - BGH, Anforderungen an die Klageschrift bei Geltendmachung verschiedener "
        "Handlungsverbote; Urheberrechtsverletzung und Wettbewerbsverstoß eines Internet-Suchd__.pdf")
_BKARTA = "2003 - BKartA, AKK_03.pdf"


def _read(name: str) -> str:
    if not _CORPUS:
        pytest.skip("set RVND_TEST_CORPUS to a directory of judgment PDFs to run this test")
    p = Path(_CORPUS) / name
    if not p.exists():
        pytest.skip(f"corpus document absent: {name}")
    return fx._extract_text(p).text


def test_bgh_yields_ratifiable_readings():
    text = _read(_BGH)
    court = JR.detect_court(text)
    assert court is not None and court.label == "BGH"
    assert court.kind == "court-judgment" and court.effect is Effect.INTERPRETIVE
    readings = JR.to_readings(text)
    assert readings, "a BGH decision citing §§ must yield readings"
    for r in readings:
        assert r.requires_ratification is True and r.auto_applied is False
        assert r.relation in {"construes", "narrows", "extends", "confirms", "disapplies"}
        assert r.provision.startswith(("§", "Art")) and r.statute
        assert 0.0 < r.weight <= 1.0
        assert "overruled" in r.currency  # time-bound, not asserted permanent
    # the court's published holding (Leitsatz) was captured as a span
    assert JR.extract_holding(text), "BGH decision publishes a Leitsatz"


def test_authority_tiering_is_real():
    bgh = JR.detect_court(_read(_BGH))
    bka = JR.detect_court(_read(_BKARTA))
    assert bka is not None and bka.label == "BKartA"
    assert bka.kind == "administrative-decision"
    # a federal court's construction outranks a competition authority's appealable decision
    assert bgh.effect.value > bka.effect.value          # INTERPRETIVE(2) > PERSUASIVE(1)
    assert bgh.tier < bka.tier                           # lower tier number = higher authority
    r_bgh = JR.to_readings(_read(_BGH))[0]
    r_bka = JR.to_readings(_read(_BKARTA))[0]
    assert r_bgh.weight > r_bka.weight


def test_quarantine_loop_closes():
    text = _read(_BKARTA)
    twin = P.ingest(text)
    assert twin.get("quarantined") is True and twin.get("routed_to") == "interpreter"
    assert twin.get("patch") is None
    # what the guard routed away is exactly what the interpreter adapter consumes
    readings = JR.to_readings(text)
    assert readings, "the route must land on real readings, not a dead end"
    assert all(r.court == "BKartA" for r in readings)


def test_no_readings_from_a_non_judgment():
    f = _FRAMEWORKS / "eu-ai-act.txt"
    if not f.exists():
        pytest.skip("eu-ai-act.txt framework absent")
    text = f.read_text(encoding="utf-8")
    assert JR.detect_court(text) is None                 # a policy/statute has no issuing court
    assert JR.to_readings(text) == []                    # so: no fabricated readings


def test_deterministic():
    text = _read(_BGH)
    a = [r.as_dict() for r in JR.to_readings(text)]
    b = [r.as_dict() for r in JR.to_readings(text)]
    assert a == b and len(a) > 0
