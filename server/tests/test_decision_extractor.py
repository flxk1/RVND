# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the decision extractor (decision_extractor.py)."""

from __future__ import annotations

from workspaces.decisions.extractor import (
    DecisionExtractor,
    DecisionPoint,
    Option,
    extract_decisions,
)
from workspaces.nd_routing import Classification


def _cls(facets=None):
    return Classification(primary_type="normative", facets=facets or [],
                          confidence=0.9, metadata={})


# --- extraction ------------------------------------------------------------

def test_member_state_derogation_is_a_decision():
    content = ("Member States may derogate from the provisions of this Article "
               "where national security so requires.")
    decisions = extract_decisions(content)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.decider == "member states"
    assert d.kind == "derogation"
    assert d.trigger  # "national security so requires"


def test_commission_delegated_act_is_discretion():
    content = ("The Commission may, by means of delegated acts, amend the list "
               "in Annex III.")
    decisions = extract_decisions(content)
    assert len(decisions) == 1
    assert decisions[0].decider == "the commission"
    assert decisions[0].kind == "discretion"


def test_either_or_fork_produces_two_options():
    content = ("The controller may either appoint a data protection officer or "
               "designate an external representative.")
    decisions = extract_decisions(content)
    assert len(decisions) == 1
    assert len(decisions[0].options) == 2


def test_choose_between_fork():
    content = "The provider shall choose between conformity assessment A and assessment B."
    decisions = extract_decisions(content)
    assert decisions
    assert len(decisions[0].options) == 2


def test_pure_obligation_is_not_a_decision():
    content = "The controller shall implement appropriate technical measures."
    assert extract_decisions(content) == []


def test_opt_out_detected():
    content = "The data subject may object to processing for direct marketing."
    decisions = extract_decisions(content)
    assert decisions
    # 'may object' → option/opt-out; decider falls back since 'data subject'
    # is not in the decider list, that's acceptable — the choice still surfaces.
    assert decisions[0].kind in ("option", "opt-out")


def test_question_is_phrased_as_a_question():
    content = "Member States may provide for specific rules."
    d = extract_decisions(content)[0]
    assert d.question.endswith("?")
    assert d.question.lower().startswith("should")


def test_confidence_higher_with_decider_and_strong_cue():
    strong = extract_decisions("Member States may derogate from Article 5.")[0]
    weak = extract_decisions("It may rain tomorrow over the data centre.")
    assert strong.confidence >= 0.8
    # 'may rain' has no decider; still a weak option candidate but low conf
    if weak:
        assert weak[0].confidence < strong.confidence


# --- ND dispatcher ---------------------------------------------------------

def test_decision_nd_handles_normative():
    assert DecisionExtractor().can_handle(_cls(["ai-act"])) is True


def test_decision_nd_emits_pairs_with_edges():
    nd = DecisionExtractor()
    content = ("Member States may derogate from this Article where national "
               "security requires. The Commission may adopt implementing acts.")
    pairs = nd.extract(content, _cls(["ai-act"]), source_document="aiact.txt")
    assert len(pairs) >= 2
    p = pairs[0]
    assert p["problem"]["kind"] == "decision-point"
    assert p["solution"]["question"].endswith("?")
    assert any(e["predicate"] == "decided-by" for e in p["edges"])
    assert all("dimension" in e for e in p["edges"])


def test_decision_nd_option_edges_present():
    nd = DecisionExtractor()
    content = ("The controller may either appoint a DPO or designate a "
               "representative.")
    pairs = nd.extract(content, _cls(["gdpr"]), source_document="g.txt")
    edges = pairs[0]["edges"]
    assert sum(1 for e in edges if e["predicate"] == "has-option") == 2
