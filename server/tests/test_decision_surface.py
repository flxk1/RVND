# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The decision surface: meaningful approval at a residual choice — structurally
no single answer-to-confirm, no rubber-stamp."""

from __future__ import annotations

import pytest

from rvnd.decisions import surface as ds


# the canonical residual: erasure (Art. 17(1)) vs retention duty (Art. 17(3)(b))
ERASURE = [
    {"id": "erase", "label": "Erase",
     "conclusion": "the data is erased without undue delay",
     "supporting": [{"pinpoint": "GDPR Art. 17(1)", "text": "right to erasure"}],
     "reasons": "The data subject's Art. 17(1) right applies absent an exception.",
     "consequences": ["notify recipients (Art. 19)", "confirm erasure to the subject"],
     "authority_tier": 1},
    {"id": "retain", "label": "Retain & restrict",
     "conclusion": "retention-obliged fields are kept and restricted (Art. 18)",
     "supporting": [{"pinpoint": "GDPR Art. 17(3)(b)", "text": "legal-obligation exception"},
                    {"pinpoint": "§ 147 AO", "text": "10-year tax retention"}],
     "reasons": "A statutory retention duty engages the Art. 17(3)(b) exception.",
     "consequences": ["restrict processing (Art. 18)", "erase non-retained fields",
                      "re-assess when the retention period lapses"],
     "authority_tier": 1},
]


def test_surface_presents_at_least_two_options():
    s = ds.build_surface("Must the controller erase on request?", ERASURE,
                         esc_reason="norm collision: Art.17(1) vs 17(3)(b)")
    assert s.residual and not s.single_reading_warning
    assert {o.id for o in s.options} == {"erase", "retain"}
    assert all(o.consequences and o.supporting for o in s.options)


def test_options_are_not_anchored_no_recommendation_stable_order():
    s = ds.build_surface("q", ERASURE)
    # ordered by id (stable), NOT by grounding → no anchoring on a "best" option
    assert [o.id for o in s.options] == sorted(o.id for o in s.options)
    d = s.to_dict()
    assert all("recommended" not in o for o in d["options"])      # no recommended flag
    # grounding is exposed but is support strength, not correctness
    assert all(0.0 <= o.grounding <= 1.0 for o in s.options)


def test_choice_schema_has_no_default():
    s = ds.build_surface("q", ERASURE)
    sch = s.choice_schema()
    assert sch["chosen_option_id"] is None       # nothing pre-selected
    assert sch["rationale"] == ""                # must be supplied
    assert set(sch["option_ids"]) == {"erase", "retain"}


def test_single_reading_is_flagged_never_silent():
    s = ds.build_surface("q", [ERASURE[0]])
    assert s.single_reading_warning and not s.residual
    assert "choice" in s.note.lower()            # presented as a choice, not an answer
    assert len(s.options) == 1                   # still shown, never dropped


def test_zero_options_is_an_error():
    with pytest.raises(ValueError):
        ds.build_surface("q", [])


# ── recording the originated choice ───────────────────────────────────────────

def test_record_requires_a_real_option():
    s = ds.build_surface("q", ERASURE)
    r = ds.record_choice(s, chosen_option_id="nope", rationale="x", actor="alex")
    assert "error" in r and "not an option" in r["error"]


def test_record_refuses_empty_rationale_no_rubber_stamp():
    s = ds.build_surface("q", ERASURE)
    r = ds.record_choice(s, chosen_option_id="retain", rationale="   ", actor="alex")
    assert "error" in r and "rationale" in r["error"]


def test_record_refuses_anonymous_choice():
    s = ds.build_surface("q", ERASURE)
    r = ds.record_choice(s, chosen_option_id="retain", rationale="tax duty applies", actor="")
    assert "error" in r and "actor" in r["error"]


def test_valid_choice_is_recorded_and_audited(tmp_path):
    s = ds.build_surface("Must the controller erase?", ERASURE,
                         esc_reason="Art.17(1) vs 17(3)(b)")
    r = ds.record_choice(s, chosen_option_id="retain",
                         rationale="§147 AO 10-yr retention engages Art.17(3)(b) for the invoiced fields",
                         actor="alex", folder=tmp_path, considered=["erase", "retain"])
    assert r["chosen_option_id"] == "retain" and r["chosen_label"] == "Retain & restrict"
    assert r["actor"] == "alex" and r["rationale"]
    assert r.get("audit_id")                     # signed into the mutation log
    assert r["considered"] == ["erase", "retain"]


# ── canonical URN spine — the decision layer's address and decides edges ──────

def _rule_in(folder):
    from rvnd.rule_registry import RuleRegistry
    reg = RuleRegistry(folder, user="t")
    return reg.place_span(
        "The controller shall erase personal data on request.",
        source_document="gdpr.txt", pinpoint="Art. 17(1)")


def test_decision_urn_is_deterministic_and_timestamp_free(tmp_path):
    s = ds.build_surface("Erase?", [{"id": "a", "label": "Erase", "conclusion": "yes"}])
    r1 = ds.record_choice(s, chosen_option_id="a", rationale="the law", actor="dpo")
    s2 = ds.build_surface("Erase?", [{"id": "a", "label": "Erase", "conclusion": "yes"}])
    r2 = ds.record_choice(s2, chosen_option_id="a", rationale="the law again", actor="dpo")
    assert r1["canonical_urn"].startswith("urn:lg:source:decision-")
    assert r1["canonical_urn"] == r2["canonical_urn"]     # same query/option/actor
    r3 = ds.record_choice(s, chosen_option_id="a", rationale="x", actor="other")
    assert r3["canonical_urn"] != r1["canonical_urn"]     # a different actor decides


def test_choice_emits_decides_edges_for_supporting_rules(tmp_path):
    from rvnd.legal_corpus import EntityRegistry
    placed = _rule_in(tmp_path)
    s = ds.build_surface("Erase?", [
        {"id": "a", "label": "Erase", "conclusion": "yes",
         "supporting": [
             {"pinpoint": "Art. 17(1)", "source_document": "gdpr.txt",
              "text": "right to erasure"},                    # recovered by lookup
             {"rule_urn": placed["canonical_urn"],
              "pinpoint": "Art. 17(1)", "text": "explicit"},  # explicit wins
         ]}])
    r = ds.record_choice(s, chosen_option_id="a", rationale="the law", actor="dpo",
                      corpus=tmp_path)
    assert r["decides"] == [placed["canonical_urn"]] * 2
    edges = EntityRegistry(tmp_path).edges
    key = f'{r["canonical_urn"]}|decides|{placed["canonical_urn"]}'
    assert key in edges
    assert edges[key]["basis"] == "Art. 17(1)"
    assert edges[key]["source"] == "decision"


def test_unresolvable_supporting_rule_is_surfaced_not_fatal(tmp_path):
    s = ds.build_surface("Q?", [
        {"id": "a", "label": "A", "conclusion": "c",
         "supporting": [{"pinpoint": "Art. 99(9)", "text": "no such rule"}]}])
    r = ds.record_choice(s, chosen_option_id="a", rationale="r", actor="me",
                      corpus=tmp_path)
    assert "error" not in r                        # the choice is never blocked
    assert r["decides"] == []
    assert r["decides_unresolved"] == ["Art. 99(9)"]


def test_without_corpus_no_edges_but_urn_present(tmp_path):
    s = ds.build_surface("Q?", [{"id": "a", "label": "A", "conclusion": "c"}])
    r = ds.record_choice(s, chosen_option_id="a", rationale="r", actor="me")
    assert r["canonical_urn"].startswith("urn:lg:source:decision-")
    assert "decides" not in r
