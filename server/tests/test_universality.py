# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The engines are jurisdiction-NEUTRAL — a non-EU legal system works by REGISTERING packs,
with zero engine change. (Audit finding: the layer read as EU-captured; these tests pin that
EU/DE is just the first shipped pack, not the engine.)

  N1  a registered US court pack → detect_court + full readings for a US opinion;
  N2  a registered genre (SG statute shape) → detect_genre routes it, no engine edit;
  N3  a registered trigger reader (PDPA 'organisation') → duties allocate the custom role;
  N4  a registered instrument vocab → its role gets a step; its room cues classify;
  N5  ask matches ANY instrument present in the map's data (PDPA), none is hardcoded.
"""
from __future__ import annotations

import os

import pytest

from workspaces import jurisdiction_packs as JP

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture
def clean_packs(monkeypatch):
    """Register into COPIES of the pack registries — auto-restored, never global pollution."""
    monkeypatch.setattr(JP, "_COURT_PACKS", dict(JP._COURT_PACKS))
    monkeypatch.setattr(JP, "_COURT_ORDER", list(JP._COURT_ORDER))
    monkeypatch.setattr(JP, "_MARKER_PACKS", dict(JP._MARKER_PACKS))
    monkeypatch.setattr(JP, "_ROLE_STEPS", dict(JP._ROLE_STEPS))
    monkeypatch.setattr(JP, "_ROOM_CUES_EXTRA", list(JP._ROOM_CUES_EXTRA))
    monkeypatch.setattr(JP, "_ASK_SYNONYMS", dict(JP._ASK_SYNONYMS))


US_OPINION = (
    "Supreme Court of the United States\n"
    "No. 21-869 — decided 2023-05-18\n"
    "Opinion of the Court, delivered by Justice Sotomayor.\n"
    "The Court holds that the purpose and character of the use under § 107 XYZ turns on "
    "whether the use is transformative. We affirm. It is so ordered.\n"
)


def test_us_court_pack_yields_readings(clean_packs):                   # N1
    from workspaces import judgment_reading as JR
    assert JR.detect_court(US_OPINION) is None          # honest before: unknown, NOT fabricated
    JP.register_court_pack("us", [
        (r"\bSupreme Court of the United States\b|\bSCOTUS\b",
         "SCOTUS", "Supreme Court of the United States", "court-judgment", 1, "BINDING"),
    ])
    court = JR.detect_court(US_OPINION)
    assert court and court.label == "SCOTUS" and court.tier == 1
    readings = JR.to_readings(US_OPINION)
    assert readings and readings[0].court == "SCOTUS"   # full pipeline, zero engine change
    assert readings[0].requires_ratification is True


def test_registered_genre_routes(clean_packs):                         # N2
    from workspaces.adapters.ingest.governance import genre_router as GR
    sg = "Personal Data Protection Act 2012\nSection 13. An organisation shall not collect data."
    before = GR.detect_genre(sg)
    GR.register_genre("sg-statute",
                      lambda text, low: "personal data protection act" in low
                      and bool(__import__("re").search(r"(?m)^\s*Section\s+\d", text)),
                      jurisdiction="SG", is_law=True, position=0)
    try:
        assert GR.detect_genre(sg) == "sg-statute"
        assert GR.route(sg)["jurisdiction"] == "SG"
    finally:
        GR._GENRES[:] = [g for g in GR._GENRES if g["genre"] != "sg-statute"]
        assert GR.detect_genre(sg) == before            # registry restored


def test_registered_trigger_reader_allocates_custom_role(clean_packs, monkeypatch):   # N3
    from workspaces import applicability as AP
    from workspaces import duty_identification as DI
    monkeypatch.setattr(AP, "_TRIGGER_READERS", dict(AP._TRIGGER_READERS))
    AP.register_trigger_reader("pdpa", lambda bearer, condition: (
        {"role": "organisation"} if "organisation" in f"{bearer} {condition}".lower() else {}))
    duties = DI.identify_duties(
        "An organisation shall notify the Commission of any notifiable data breach.",
        source="s. 26D", domain="pdpa")
    assert duties and duties[0].role == "organisation"  # a NON-EU role, via the pack seam
    with pytest.raises(ValueError):
        DI.identify_duties("An operator shall keep records.", domain="not-registered")


def test_instrument_vocab_supplies_steps_and_rooms(clean_packs):       # N4
    from workspaces import governance_map as GM
    JP.register_instrument_vocab("pdpa",
        role_steps={"organisation": "collect · use · disclose"},
        room_cues=[("Breach response", ("notify the commission",))])
    assert GM._role_steps()["organisation"] == "collect · use · disclose"
    class _D:  # duck-typed duty — no neutral cue word, so only the pack's room can claim it
        action = "notify the commission within 72 hours"
        raw = action
    assert GM._classify_room(_D()) == "Breach response"
    # precedence is stable: a NEUTRAL cue ("breach" → Security & robustness) still wins over
    # a pack's appended room — packs extend the map, they do not reorder it.
    class _D2:
        action = "notify the commission of a data breach"
        raw = action
    assert GM._classify_room(_D2()) == "Security & robustness"


def test_ask_matches_any_instrument_from_data(clean_packs):            # N5
    from workspaces import governance_ask as GA
    view = GA.parse("which PDPA rules need a human?",
                    facet_values={"instrument": ["PDPA"], "role": [], "risk": [],
                                  "room": [], "demand": [], "status": []})
    assert view.filters.get("instrument") == ["PDPA"]   # from the DATA, not a hardcoded pair


def test_subject_vocabulary_covers_generic_regulated_entities():
    # the normative gate's curated subject list must cover generic regulated entities and
    # their plurals, not only the EU/contract role nouns it started with — while still
    # rejecting casual prose.
    from workspaces import rule_extractor as RE
    for sent in ["An organisation shall notify the authority.",
                 "Organizations shall notify the authority.",
                 "Employers shall notify the authority.",
                 "Companies must keep records.",
                 "Banks shall verify identity.",
                 "Institutions must retain audit logs for five years.",
                 "The undertaking shall not abuse its dominant position."]:
        assert RE.extract_rules(sent), sent
    for casual in ["He shall return tomorrow.",
                   "I must remember to call her.",
                   "You may not like this movie."]:
        assert not RE.extract_rules(casual), casual
