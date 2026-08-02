# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Oversight ND IN face — extractor + dispatcher tests.

Covers: EN/DE approval floors, human-oversight review floors, notify duties,
MANUAL origination (human intervention, Ermessen, höchstpersönlich), the
solely-automated grade ceiling, measures + cadence, sentence merging,
the no-signal empty result, and the ND pair shape.
"""

from workspaces.oversight_extractor import (
    OVERSIGHT_LEVELS, OversightFacet, extract_oversight, render_oversight)
from workspaces.domain_nds import OversightND
from workspaces.nd_routing import Classification


def _one(text: str) -> OversightFacet:
    facets = extract_oversight(text)
    assert len(facets) == 1, f"expected 1 facet, got {len(facets)}: {facets}"
    return facets[0]


def test_level_order_is_total():
    assert OVERSIGHT_LEVELS == (
        "AUTONOMOUS", "NOTIFY", "REVIEW", "APPROVE", "SUPERVISED", "MANUAL")


def test_approval_en_with_overseer():
    f = _one("Any deletion of records requires the prior approval of "
             "the data protection officer.")
    assert f.min_level == "APPROVE"
    assert "data protection officer" in f.overseer
    assert f.language == "en"
    assert f.confidence >= 0.85


def test_approval_de_bedarf_der_genehmigung():
    f = _one("Die Übermittlung personenbezogener Daten bedarf der "
             "vorherigen Genehmigung durch den Verantwortlichen.")
    assert f.min_level == "APPROVE"
    assert "verantwortlichen" in f.overseer.lower()
    assert f.language == "de"


def test_human_oversight_en_review_floor():
    f = _one("High-risk AI systems shall be designed so that they can be "
             "effectively overseen by natural persons during use.")
    assert f.min_level == "REVIEW"


def test_oversight_de_review_floor():
    f = _one("Die Ergebnisse des Systems sind regelmäßig zu überprüfen.")
    assert f.min_level == "REVIEW"
    assert f.cadence != ""
    assert f.language == "de"


def test_notify_en_with_overseer():
    f = _one("The provider shall notify the supervisory authority without "
             "undue delay.")
    assert f.min_level == "NOTIFY"
    assert "supervisory authority" in f.overseer


def test_human_intervention_is_manual_and_personal():
    f = _one("The data subject has the right to obtain human intervention "
             "on the part of the controller.")
    assert f.min_level == "MANUAL"
    assert f.personal is True


def test_ermessen_is_manual():
    f = _one("Die Behörde entscheidet nach pflichtgemäßem Ermessen über "
             "den Antrag.")
    assert f.min_level == "MANUAL"
    assert f.language == "de"


def test_hoechstpersoenlich_is_personal_manual():
    f = _one("Die Entscheidung über die Kündigung ist höchstpersönlich "
             "zu treffen.")
    assert f.personal is True
    assert f.min_level == "MANUAL"


def test_solely_automated_sets_grade_ceiling():
    f = _one("The data subject shall not be subject to a decision based "
             "solely on automated processing which produces legal effects.")
    assert f.grade_ceiling == "L2"
    assert f.personal is True
    assert f.min_level in ("APPROVE", "MANUAL")


def test_measure_and_cadence_en():
    f = _one("The deployer shall document all overrides of the system and "
             "review them quarterly.")
    assert f.measure != ""
    assert f.cadence == "quarterly"


def test_merge_strictest_level_wins_in_one_sentence():
    f = _one("Publication requires the approval of the editor and the "
             "author must act personally in granting it.")
    assert f.min_level == "MANUAL"  # personal MANUAL outranks APPROVE
    assert f.personal is True


def test_trigger_clause_captured():
    f = _one("Where the system processes biometric data, deployment "
             "requires the prior authorisation of the competent authority.")
    assert f.trigger != ""
    assert "biometric" in f.trigger


def test_no_signal_yields_nothing():
    assert extract_oversight(
        "The weather in Hamburg was mild and the meeting ended early. "
        "Everyone went home satisfied with the result.") == []


def test_render_contains_floor_and_overseer():
    f = _one("Any deletion of records requires the prior approval of "
             "the data protection officer.")
    text = render_oversight(f)
    assert "FLOOR:    APPROVE" in text
    assert "data protection officer" in text


# ── dispatcher ──────────────────────────────────────────────────────────────

def _cls(facets=None, confidence=0.9):
    return Classification(primary_type="normative",
                          facets=facets or [], confidence=confidence)


def test_nd_emits_typed_pairs_with_edges():
    nd = OversightND()
    pairs = nd.extract(
        "Any transfer of personal data requires the prior approval of the "
        "controller. The provider shall notify the supervisory authority "
        "of serious incidents.",
        _cls(facets=["ai-act"]))
    assert len(pairs) == 2
    p = pairs[0]
    assert p["problem"]["type"] == "oversight-requirement"
    assert p["problem"]["scope"] == "oversight"
    assert p["solution"]["authority_tier"] == 1          # ai-act facet
    assert p["solution"]["oversight"]["min_level"] == "APPROVE"
    preds = {e["predicate"] for e in p["edges"]}
    assert "belongs-to" in preds and "floors-level" in preds


def test_nd_emits_nothing_without_signal():
    nd = OversightND()
    assert nd.extract("A purely descriptive paragraph about the history "
                      "of the company and its founders.", _cls()) == []


def test_nd_can_handle_normative_type():
    nd = OversightND()
    assert nd.can_handle(_cls()) is True
    assert nd.can_handle(Classification(
        primary_type="letter", facets=["oversight"], confidence=0.9)) is True
    assert nd.can_handle(Classification(
        primary_type="letter", facets=[], confidence=0.9)) is False
