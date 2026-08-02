# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The NEUTRAL use-case intake — universal facets that fit any policy, over the EXISTING card
spine (no parallel structure).

  U1  the universal axes are neutral; `category` is the escalated one; no AI-Act names leak;
  U2  the card is a real subject_card.SubjectCard on the registered `neutral` vocab (reuse);
  U3  capture-first — valid with only a description; unknown facets drive MAY_APPLY; completeness
      is SubjectCard's own; category stays an open determination.
"""
from __future__ import annotations

import os

from workspaces import use_case_intake as UC
from workspaces import subject_card as SC

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def test_universal_facets_are_neutral():                               # U1
    names = [f for f, _ in UC.UNIVERSAL_FACETS]
    assert names == ["role", "sector", "jurisdiction", "category", "scope"]
    assert UC.facet_kind("category") == "escalated" and UC.facet_kind("role") == "declared"
    for banned in ("gpai", "annex_iii_area", "risk_tier", "high_risk"):
        assert banned not in names
    # the neutral vocab is registered on the shared spine — not a parallel registry
    assert SC.get_vocabulary("neutral") is not None


def test_card_is_a_reused_subject_card():                              # U2
    card = UC.blank(description="An AI tool that screens job applicants")
    assert isinstance(card, SC.SubjectCard) and card.domain == "neutral"
    assert card.is_empty()
    assert set(UC.unknown_facets(card)) == {"role", "sector", "jurisdiction", "category", "scope"}
    card2 = UC.blank(role="deployer", sector="employment")
    assert "role" not in UC.unknown_facets(card2) and "category" in UC.unknown_facets(card2)


def test_completeness_and_determinations():                            # U3
    assert UC.blank().completeness() == 0.0
    card = UC.blank(role="provider", sector="employment", jurisdiction="EU", scope="personal_data")
    assert card.completeness() == 0.8                    # 4 of 5 universal facets (SubjectCard's own)
    assert UC.open_determinations() == ["category"]      # carried, never asserted
