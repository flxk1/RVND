# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Genre routing in front of policy_ingest: court judgments are quarantined (routed to the
interpreter, no governance patch), while genuine instruments and plain policies compile
normally.

Two tests read real decisions/frameworks from a local corpus that is not part of the repo;
point RVND_TEST_CORPUS / RVND_TEST_FRAMEWORKS at the directories to enable them, otherwise
they skip with that reason.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from workspaces import format_extractors as fx
from workspaces import genre_router
from workspaces import policy_ingest

from tests.test_policy_ingest import POLICY   # the golden fixture — shared, not copied

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

_CORPUS = os.environ.get("RVND_TEST_CORPUS", "")
_FRAMEWORKS = os.environ.get("RVND_TEST_FRAMEWORKS", "")

_JUDGMENTS = [
    "2003 - BGH, Anforderungen an die Klageschrift bei Geltendmachung verschiedener "
    "Handlungsverbote; Urheberrechtsverletzung und Wettbewerbsverstoß eines Internet-Suchd__.pdf",
    "2003 - BKartA, AKK_03.pdf",
]


def _read_corpus_pdf(name: str) -> str:
    if not _CORPUS:
        pytest.skip("set RVND_TEST_CORPUS to a directory of judgment PDFs to run this test")
    p = Path(_CORPUS) / name
    if not p.exists():
        pytest.skip(f"corpus document absent: {name}")
    return fx._extract_text(p).text


@pytest.mark.parametrize("name", _JUDGMENTS)
def test_judgment_is_quarantined_no_patch(name):
    text = _read_corpus_pdf(name)
    assert genre_router.detect_genre(text) == "case-law"
    t = policy_ingest.ingest(text)
    assert t["ok"] is True                       # a judgment is valid input, just not a policy
    assert t.get("quarantined") is True
    assert t.get("genre") == "case-law"
    assert t.get("routed_to") == "interpreter"
    assert t.get("patch") is None                # no governance patch from a court's reasoning
    assert "netlist" not in t
    assert t["classification"]["express"] == []


def test_framework_instrument_still_compiles_a_patch():
    if not _FRAMEWORKS:
        pytest.skip("set RVND_TEST_FRAMEWORKS to the frameworks directory to run this test")
    f = Path(_FRAMEWORKS) / "eu-ai-act.txt"
    if not f.exists():
        pytest.skip("eu-ai-act.txt framework absent")
    text = f.read_text(encoding="utf-8")
    assert genre_router.detect_genre(text) != "case-law"
    t = policy_ingest.ingest(text)
    assert t["ok"] is True
    assert not t.get("quarantined")              # the guard must not suppress real instruments
    assert t.get("patch") is not None
    assert len(t["classification"]["express"]) >= 3
    assert any(e.startswith("prohibit ") for e in t["classification"]["express"])


def test_golden_policy_not_falsely_quarantined():
    assert genre_router.detect_genre(POLICY) != "case-law"
    t = policy_ingest.ingest(POLICY)
    assert t["ok"] is True
    assert not t.get("quarantined")
    assert t.get("patch") is not None
    assert "reserve automated_hiring_decision by compliance_officer" in t["classification"]["express"]
