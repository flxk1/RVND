# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P1 tests: contract intake pipeline, Phase-2 LLM seam (stub-tested), and the
template-corpus eval gate (floors enforced as a test, so a regression in any
extractor breaks the build, not the claim)."""

import json


from rvnd.contracts.eval import FLOORS, run_eval
from rvnd.contracts.extractor import (classify_contract_type,
                                      extract_effective_date,
                                      extract_governing_law, extract_parties,
                                      ingest_contract, intake_contract)
from rvnd.rule_extractor_llm import extract_rules_llm
from rvnd.temporal import Date

DPA = """DATA PROCESSING AGREEMENT

This Data Processing Agreement is made between Norddata Services GmbH (the
"Processor") and Beispielkunde AG (the "Controller") under Article 28 GDPR.

This Agreement is effective as of 2026-07-01.

1. The Processor shall notify the Controller of a personal data breach no
later than 72 hours after the personal data breach.

2. The Processor must not engage a Sub-processor without the prior written
authorisation of the Controller.

3. Governing law. This Agreement shall be governed by the laws of the
Federal Republic of Germany.
"""


# ── field extractors ──────────────────────────────────────────────────────────

class TestFieldExtractors:
    def test_type_classification(self):
        ctype, conf = classify_contract_type(DPA)
        assert ctype == "dpa" and conf >= 0.6

    def test_untyped_is_representable(self):
        ctype, conf = classify_contract_type("A poem about clouds.")
        assert ctype == "" and conf == 0.0

    def test_parties_from_parenthetical_roles(self):
        parties = extract_parties(DPA)
        roles = {p.role: p for p in parties}
        assert set(roles) == {"processor", "controller"}
        assert "Norddata" in roles["processor"].name

    def test_no_role_term_no_party(self):
        assert extract_parties('made with ACME GmbH ("Friend")') == []

    def test_governing_law_needs_cue_and_jurisdiction(self):
        law, conf = extract_governing_law(DPA)
        assert law == "DE" and conf >= 0.9
        assert extract_governing_law("Germany is mentioned here.")[0] is None

    def test_effective_date_needs_cue(self):
        d, conf = extract_effective_date(DPA)
        assert d == Date("2026-07-01")
        assert extract_effective_date("On 2026-07-01 nothing happened.")[0] is None

    def test_de_date_format(self):
        d, _ = extract_effective_date("Diese Vereinbarung tritt am 01.11.2026 in Kraft.")
        assert d == Date("2026-11-01")


# ── intake assembly ───────────────────────────────────────────────────────────

class TestIntake:
    def test_intake_assembles_instance(self):
        intake = intake_contract(DPA)
        inst = intake.instance
        assert inst.contract_type == "dpa" and inst.governing_law == "DE"
        assert inst.effective_date == Date("2026-07-01")
        assert len(inst.parties) == 2
        assert inst.document_hash.startswith("sha256:")

    def test_meta_clause_filtered_from_duties(self):
        intake = intake_contract(DPA)
        assert not any("governed" in f.raw_sentence and f.modal == "obligation"
                       for f in intake.rules)

    def test_duties_extracted_with_predicate(self):
        intake = intake_contract(DPA)
        notify = [f for f in intake.rules if "notify" in f.raw_sentence.lower()]
        assert notify and notify[0].condition_struct["temporal"]["offset"] == "PT72H"

    def test_cold_start_missing_fields_honest(self):
        intake = intake_contract("Short note. The supplier shall deliver goods.")
        assert "effective_date" in intake.missing
        assert "governing_law" in intake.missing
        assert intake.confidences["contract_type"] == 0.0

    def test_ingest_end_to_end(self, tmp_path):
        out = ingest_contract(tmp_path, DPA, contract_id="dpa-x",
                              log_root=tmp_path / "log")
        assert out["contract"]["status"] == "created"
        assert len(out["spans_placed"]) >= 2
        assert len(out["obligations"]["created"]) == 2     # duty + prohibition
        # re-ingest: idempotent
        again = ingest_contract(tmp_path, DPA, contract_id="dpa-x",
                                log_root=tmp_path / "log")
        assert again["contract"]["status"] == "updated"
        assert again["obligations"]["created"] == []

    def test_ingested_obligation_carries_deadline(self, tmp_path):
        from rvnd.obligation_runtime import ObligationRegistry
        ingest_contract(tmp_path, DPA, contract_id="dpa-x", log_root=tmp_path / "log")
        obs = ObligationRegistry(tmp_path).for_contract("dpa-x@1")
        assert any(o.deadline_rel and o.deadline_rel.offset.iso == "PT72H"
                   for o in obs)


# ── Phase-2 LLM seam ──────────────────────────────────────────────────────────

GOOD_REPLY = json.dumps([{
    "subject": "Processor", "modal": "obligation", "modal_phrase": "shall",
    "action": "maintain a record of processing activities",
    "condition": "", "exception": "",
    "raw_sentence": "The Processor shall maintain a record of processing "
                    "activities and, where the Controller so requests in "
                    "writing, make it available within a reasonable period.",
}])

MULTI_SENTENCE = ("The Processor shall maintain a record of processing "
                  "activities and, where the Controller so requests in "
                  "writing, make it available within a reasonable period.")


class TestPhase2Seam:
    def test_no_model_degrades_to_phase1(self):
        out = extract_rules_llm(DPA, model_fn=None)
        assert out.used_model is False and out.facets

    def test_valid_model_rule_merges(self):
        out = extract_rules_llm(MULTI_SENTENCE, model_fn=lambda p: GOOD_REPLY,
                                defined_terms=("Processor",))
        llm = [f for f in out.facets if f.confidence <= 0.85
               and "record of processing" in f.raw_sentence]
        assert llm and llm[0].modal == "obligation"

    def test_confidence_capped(self):
        out = extract_rules_llm(MULTI_SENTENCE, model_fn=lambda p: GOOD_REPLY)
        assert all(f.confidence <= 0.85 for f in out.facets
                   if "record of processing" in f.raw_sentence)

    def test_ungrounded_sentence_dropped(self):
        fab = json.dumps([{"subject": "Processor", "modal": "obligation",
                           "modal_phrase": "shall", "action": "pay a penalty",
                           "condition": "", "exception": "",
                           "raw_sentence": "The Processor shall pay a penalty of EUR 1m."}])
        out = extract_rules_llm(MULTI_SENTENCE, model_fn=lambda p: fab)
        assert not any("penalty" in f.raw_sentence for f in out.facets)
        assert any("not found in source" in d["why"] for d in out.dropped)

    def test_invalid_modal_dropped(self):
        bad = json.dumps([{"subject": "x", "modal": "vibe", "modal_phrase": "",
                           "action": "y", "condition": "", "exception": "",
                           "raw_sentence": MULTI_SENTENCE}])
        out = extract_rules_llm(MULTI_SENTENCE, model_fn=lambda p: bad)
        assert any("invalid modal" in d["why"] for d in out.dropped)

    def test_garbage_reply_is_abstention(self):
        out = extract_rules_llm(MULTI_SENTENCE, model_fn=lambda p: "I think that…")
        assert out.dropped and out.facets == extract_rules_llm(
            MULTI_SENTENCE, model_fn=None).facets

    def test_model_exception_degrades_gracefully(self):
        def boom(p):
            raise RuntimeError("no endpoint")
        out = extract_rules_llm(MULTI_SENTENCE, model_fn=boom)
        assert out.used_model is False
        assert any("model_fn raised" in d["why"] for d in out.dropped)

    def test_predicates_stay_deterministic_only(self):
        out = extract_rules_llm(MULTI_SENTENCE, model_fn=lambda p: GOOD_REPLY)
        assert all(f.condition_struct is None for f in out.facets
                   if "record of processing" in f.raw_sentence)


# ── the eval gate ─────────────────────────────────────────────────────────────

class TestEvalGate:
    def test_template_corpus_meets_all_floors(self):
        out = run_eval()
        assert out["ok"], f"floor breaches: {out['breaches']}\nnotes: {out['notes']}"

    def test_floors_match_plan(self):
        assert FLOORS["parties_precision"] == 0.95
        assert FLOORS["obligations_precision"] == 0.80
        assert FLOORS["obligations_recall"] == 0.75
        assert FLOORS["predicates_precision"] == 0.85
