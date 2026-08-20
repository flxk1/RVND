# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""End-to-end test: AI Act text + subject card -> regulatory-placement memo."""

from __future__ import annotations

from workspaces.subject_card import make_card, AI_ACT_VOCAB
from workspaces.applicability import enrich_pairs, applicability_facets_ai_act
from workspaces.matcher import assess, Match
from workspaces.deontic_facets import extract_deontic_pairs
from workspaces.memo import build_memo, render_memo


AI_ACT = (
    "Providers of high-risk AI systems shall establish a risk management "
    "system. Providers of high-risk AI systems shall draw up the technical "
    "documentation before placing the system on the market. Deployers of "
    "high-risk AI systems shall assign human oversight to natural persons. "
    "Where the high-risk AI system is used for employment, the deployer shall "
    "carry out a data protection impact assessment under Regulation (EU) "
    "2016/679. Importers of high-risk AI systems shall verify conformity."
)


def _obligations(text=AI_ACT):
    pairs = extract_deontic_pairs(text, source_document="ai-act")
    enrich_pairs(pairs, "ai-act")
    return pairs


# --- vocabulary + card ------------------------------------------------------

def test_card_validates_facets():
    card = make_card("ai-act", role="provider", risk_tier="high-risk",
                     annex_iii_area=["employment"], description="hiring screener")
    assert card.get("role") == "provider"
    assert "employment" in card.get("annex_iii_area")


def test_card_bad_value_is_captured_not_rejected_by_default():
    # Capture-first: a bad value is preserved as a note, never silently dropped
    # and never fatal — the user's input is not lost.
    card = make_card("ai-act", role="overlord")
    assert card.get("role") is None              # not accepted as a facet
    assert "[unmapped] role=overlord" in card.notes


def test_card_rejects_bad_value_in_strict_mode():
    import pytest
    with pytest.raises(ValueError):
        make_card("ai-act", strict=True, role="overlord")


def test_subsumption_employment_is_high_risk():
    assert "high-risk" in AI_ACT_VOCAB.ancestors("employment")


# --- applicability enrichment ----------------------------------------------

def test_enrichment_reads_role_and_tier():
    f = applicability_facets_ai_act("providers of high-risk AI systems", "")
    assert f["role"] == "provider"
    assert f["risk_tier"] == "high-risk"


def test_enrichment_reads_activity_from_condition():
    f = applicability_facets_ai_act("the deployer",
                                    "where the system is used for employment")
    assert "employment" in f.get("annex_iii_area", [])


# --- matcher (the core) -----------------------------------------------------

def test_provider_high_risk_obligation_applies_to_matching_card():
    card = make_card("ai-act", role="provider", risk_tier="high-risk")
    pairs = _obligations()
    res = assess(pairs, card)
    # the provider risk-management obligation must be in 'applies'
    assert any("risk management" in (m.action or "").lower() for m in res.applies)


def test_deployer_obligation_not_triggered_for_provider_only_card():
    # A pure provider (not a deployer) should NOT be bound by deployer duties.
    card = make_card("ai-act", role="provider", risk_tier="high-risk")
    pairs = _obligations()
    res = assess(pairs, card)
    assert any(m.bearer.startswith("deployers") for m in res.not_triggered)


def test_unknown_tier_yields_may_apply():
    # Card states role but not risk tier → high-risk obligations are MAY_APPLY.
    card = make_card("ai-act", role="provider")  # no risk_tier
    pairs = _obligations()
    res = assess(pairs, card)
    assert res.may_apply, "expected may-apply when risk tier unknown"
    assert all("risk_tier" in m.missing_facets or m.missing_facets
               for m in res.may_apply)


def test_employment_card_subsumes_high_risk_trigger():
    # Card says annex area employment but not the word 'high-risk' — subsumption
    # must still satisfy a high-risk-keyed obligation.
    card = make_card("ai-act", role="provider", annex_iii_area=["employment"],
                     risk_tier="high-risk")
    pairs = _obligations()
    res = assess(pairs, card)
    assert any("risk management" in (m.action or "").lower() for m in res.applies)


def test_not_triggered_names_the_excluding_facet():
    card = make_card("ai-act", role="deployer", risk_tier="high-risk")
    pairs = _obligations()
    res = assess(pairs, card)
    prov = [m for m in res.not_triggered if m.bearer.startswith("providers")]
    assert prov
    assert any(v.result is Match.NOT_TRIGGERED and v.facet == "role"
               for m in prov for v in m.facet_verdicts)


# --- full memo --------------------------------------------------------------

def test_empty_card_is_valid_and_stores_freeform():
    # PoC description + contact + a note, ZERO facets, plus an out-of-vocab
    # facet that must be captured (not rejected).
    card = make_card("ai-act",
                     description="PoC tool that ranks job applicants.",
                     contact="Jamie, jamie\x40acme.eu",
                     notes="vendor model",
                     jurisdiction="germany")   # not a facet → goes to notes
    assert card.is_empty()
    assert card.completeness() == 0.0
    assert "vendor model" in card.notes
    assert "[unmapped] jurisdiction=germany" in card.notes


def test_strict_mode_still_raises_on_bad_facet():
    import pytest
    with pytest.raises(ValueError):
        make_card("ai-act", strict=True, jurisdiction="germany")


def test_empty_card_memo_is_provisional_and_asks_for_intake():
    card = make_card("ai-act", description="early PoC, role unclear",
                     contact="ops\x40acme.eu")
    memo = build_memo(AI_ACT, card, title="provisional")
    text = render_memo(memo)
    assert "PROVISIONAL" in text
    assert "needs intake" in text
    # every applicable-in-principle obligation should be MAY APPLY, not APPLY
    assert "[2] OBLIGATIONS THAT APPLY (0)" in text
    assert memo["assessment"].may_apply           # things surfaced for the user
    # the contact was stored and shown
    assert "ops\x40acme.eu" in text


def test_partial_card_some_applies_some_may_apply():
    # Role known, tier unknown: role-excluded obligations resolve, tier-gated
    # ones become may-apply rather than guessed.
    card = make_card("ai-act", role="deployer",
                     description="tool used by HR")
    res = build_memo(AI_ACT, card)["assessment"]
    assert res.may_apply  # tier unknown keeps high-risk duties uncertain
    # a provider-only duty is still positively excluded by the known role
    assert any(m.bearer.startswith("providers") for m in res.not_triggered)


def test_build_and_render_memo():
    card = make_card("ai-act", role="deployer", risk_tier="high-risk",
                     annex_iii_area=["employment"], processes_personal_data="yes",
                     description="AI CV-screening tool for recruitment")
    memo = build_memo(AI_ACT, card, title="AI Act placement — hiring screener")
    text = render_memo(memo)
    assert "PLACEMENT IN THE REGULATORY LANDSCAPE" in text
    assert "OBLIGATIONS THAT APPLY" in text
    assert "ARTIFACTS YOU MUST PRODUCE" in text
    # deployer human-oversight duty applies; provider duties do not
    assert "human oversight" in text.lower()
    # DPIA artifact surfaced
    assert "impact assessment" in text.lower()
    # not legal advice disclaimer present
    assert "not legal advice" in text.lower()
