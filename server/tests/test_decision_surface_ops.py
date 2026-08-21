# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The decision-surface op pair: build is pure and banded, record is the one
governed write and keeps the review trail honest.

Claims under test (written before the logic):
  D1  decision_build refuses zero candidates; one candidate passes through
      with the single-reading warning set
  D2  options leave banded (thin / moderate / firm) with a supporting count —
      no grounding float anywhere in the response
  D3  decision_record refuses an empty rationale, an unnamed actor, and a
      chosen id that is not an option
  D4  a recorded choice lands on the signed chain (audit_id) carrying
      rationale, actor, considered, asked and evidence_refs
  D5  considered records exactly what was passed — an empty list stays empty,
      never auto-claimed as all options
  D6  the facade routes both ops and its help documents them

Run: python -m pytest server/tests/test_decision_surface_ops.py -q
"""
from __future__ import annotations

import json

import pytest

import rvnd.mcp_server as S
from rvnd.mcp_impl import decision_build, decision_record

CANDS = [
    {"id": "erase", "label": "Erase everything now",
     "conclusion": "erase (Art. 17(1)(a) GDPR)",
     "supporting": [{"pinpoint": "GDPR Art. 17(1)(a)", "text": "erased where no longer necessary"}],
     "consequences": ["accounting records go too"]},
    {"id": "split", "label": "Split the records",
     "conclusion": "erase profile; retain invoices restricted",
     "supporting": [{"pinpoint": "GDPR Art. 17(3)(b)", "text": "retention required by law"},
                    {"pinpoint": "§ 147(3) AO", "text": "keep accounting records ten years"}],
     "consequences": ["profile gone; invoices frozen"]},
]


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    return tmp_path


def test_build_refuses_empty_and_warns_on_single(folder):         # D1
    assert decision_build("q", [])["ok"] is False
    one = decision_build("q", CANDS[:1])
    assert one["ok"] is True and one["single_reading_warning"] is True
    two = decision_build("q", CANDS)
    assert two["single_reading_warning"] is False
    assert [o["id"] for o in two["options"]] == ["erase", "split"]   # server order kept


def test_options_leave_banded_never_scored(folder):               # D2
    out = decision_build("q", CANDS)
    for o in out["options"]:
        assert o["grounding_band"] in ("thin", "moderate", "firm")
        assert o["supporting_count"] == len(o["supporting"])
    assert '"grounding":' not in json.dumps(out), "a raw grounding score escaped"


def test_record_gates_hold(folder):                               # D3
    surface = decision_build("q", CANDS)
    no_rat = decision_record(str(folder), surface, "split", "", "alex")
    assert no_rat["ok"] is False and "rationale" in no_rat["error"]
    no_actor = decision_record(str(folder), surface, "split", "both duties held", "")
    assert no_actor["ok"] is False
    not_an_option = decision_record(str(folder), surface, "nope", "reason", "alex")
    assert not_an_option["ok"] is False


def test_recorded_choice_carries_the_trail(folder):               # D4
    surface = decision_build("q", CANDS, esc_reason="two conflicting duties")
    out = decision_record(
        str(folder), surface, "split",
        "Art. 17(3)(b) carves out what § 147(3) AO demands; K. confirmed by phone.",
        "alex",
        considered=["split"],
        asked=[{"query": "are the invoices open?", "answer_hash": "sha256:aa",
                "declined_to_rank": False, "degraded": True}],
        evidence_refs=["work:call-note-2026-07-04"])
    assert out["ok"] is True and out.get("audit_id")
    assert out["considered"] == ["split"]
    assert out["asked"][0]["query"] == "are the invoices open?"
    assert out["evidence_refs"] == ["work:call-note-2026-07-04"]
    assert out["esc_reason"] == "two conflicting duties"


def test_considered_is_never_auto_claimed(folder):                # D5
    surface = decision_build("q", CANDS)
    out = decision_record(str(folder), surface, "erase",
                          "decided on the card face alone", "alex", considered=[])
    assert out["ok"] is True
    assert out["considered"] == [], "an empty review must record empty, not all options"


def test_facade_routes_and_documents(folder):                     # D6
    built = S.workspace_dispatch("decision_build", {"query": "q", "candidates": CANDS})
    assert built["ok"] is True
    rec = S.workspace_dispatch("decision_record", {
        "folder_context": str(folder), "surface": built, "chosen_option_id": "split",
        "rationale": "the split follows both duties", "actor": "alex",
        "considered": ["split", "erase"]})
    assert rec["ok"] is True and rec.get("audit_id")
    ops = {o["op"] for o in S.workspace_dispatch("help")["ops"]}
    assert {"decision_build", "decision_record"} <= ops
