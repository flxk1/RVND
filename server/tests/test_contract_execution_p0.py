# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the P0 remainder of the contract-execution stack:
predicate.py, defined_terms.py, versioned span anchors (rule_registry),
NT-11/NT-12 (norm_contract), and the typed-date retrofit of
action_gate.StandingApproval + contract_reviews.request_contract_approval.

Discipline under test throughout: abstain over guess; orphan over silent
drop; reject at write over defensive parse at read."""


import pytest

from rvnd.defined_terms import (DefinedTermsRegistry, extract_defined_terms)
from rvnd.norm_contract import (check_pair, check_predicate_floor,
                                 check_typed_dates)
from rvnd.predicate import (PREDICATE_CONFIDENCE_FLOOR, Predicate,
                             PredicateError, attach_predicates,
                             parse_condition)
from rvnd.rule_extractor import RuleFacet
from rvnd.rule_registry import RuleRegistry


# ── Predicate type ────────────────────────────────────────────────────────────

class TestPredicate:
    def test_threshold_valid(self):
        p = Predicate(kind="threshold", subject_ref="amount", comparator=">",
                      value="10000", unit="EUR", confidence=0.9)
        assert Predicate.from_dict(p.to_dict()) == p

    def test_bad_kind_rejected(self):
        with pytest.raises(PredicateError):
            Predicate(kind="vibe", confidence=0.9)

    def test_threshold_needs_comparator_and_value(self):
        with pytest.raises(PredicateError):
            Predicate(kind="threshold", confidence=0.9)

    def test_threshold_value_must_be_decimal(self):
        with pytest.raises(PredicateError):
            Predicate(kind="threshold", comparator=">", value="a lot", confidence=0.9)

    def test_bad_comparator_rejected(self):
        with pytest.raises(PredicateError):
            Predicate(kind="threshold", comparator="~", value="1", confidence=0.9)

    def test_confidence_bounds(self):
        with pytest.raises(PredicateError):
            Predicate(kind="state", confidence=1.5)


# ── deterministic condition parser ────────────────────────────────────────────

class TestParseCondition:
    def test_en_deadline(self):
        p = parse_condition("within 30 days of the signing")
        assert p is not None and p.kind == "event"
        assert p.temporal.offset.iso == "P30D" and p.temporal.event == "signing"
        assert p.confidence >= PREDICATE_CONFIDENCE_FLOOR

    def test_en_no_later_than_hours(self):
        p = parse_condition("no later than 72 hours after the breach notification")
        assert p is not None and p.temporal.offset.iso == "PT72H"

    def test_de_deadline(self):
        p = parse_condition("innerhalb von 14 Tagen nach Vertragsschluss")
        assert p is not None and p.kind == "event"
        assert p.temporal.offset.iso == "P14D"

    def test_threshold_eur(self):
        p = parse_condition("where the contract value exceeds EUR 10,000")
        assert p is not None and p.kind == "threshold"
        assert p.comparator == ">" and p.value == "10000" and p.unit == "EUR"

    def test_threshold_de_grouping(self):
        p = parse_condition("sofern der Auftragswert über 10.000 EUR liegt")
        assert p is not None and p.value == "10000" and p.unit == "EUR"

    def test_threshold_at_least(self):
        p = parse_condition("of at least 500 EUR per month")
        assert p is not None and p.comparator == ">="

    @pytest.mark.parametrize("vague", [
        "where appropriate", "if the controller deems it necessary",
        "soweit erforderlich", "in exceptional circumstances", "",
    ])
    def test_unparseable_abstains(self, vague):
        assert parse_condition(vague) is None

    def test_attach_predicates_fills_only_confident(self):
        f1 = RuleFacet(condition="within 30 days of the signing", language="en")
        f2 = RuleFacet(condition="where appropriate", language="en")
        n = attach_predicates([f1, f2])
        assert n == 1
        assert f1.condition_struct is not None and f2.condition_struct is None

    def test_attach_preserves_verbatim_condition(self):
        f = RuleFacet(condition="within 30 days of the signing")
        attach_predicates([f])
        assert f.condition == "within 30 days of the signing"     # untouched

    def test_rulefacet_roundtrips_with_struct(self):
        f = RuleFacet(condition="within 30 days of the signing")
        attach_predicates([f])
        assert f.to_dict()["condition_struct"]["kind"] == "event"


# ── defined terms ─────────────────────────────────────────────────────────────

SAMPLE = ('This Agreement is made between ACME GmbH (the "Processor") and '
          'Kunde AG. "Services" means the data processing services described '
          'in Annex 1. „Auftragsverarbeiter" bezeichnet die ACME GmbH.')


class TestDefinedTerms:
    def test_extract_means_pattern(self):
        terms = {t.term: t for t in extract_defined_terms(SAMPLE)}
        assert "Services" in terms
        assert terms["Services"].definition.startswith("the data processing")

    def test_extract_parenthetical(self):
        terms = {t.term for t in extract_defined_terms(SAMPLE)}
        assert "Processor" in terms

    def test_extract_german(self):
        terms = {t.term: t for t in extract_defined_terms(SAMPLE)}
        assert "Auftragsverarbeiter" in terms

    def test_dedup_on_term(self):
        text = '"Data" means the first thing. "Data" means the second thing.'
        assert len(extract_defined_terms(text)) == 1

    def test_registry_roundtrip(self, tmp_path):
        reg = DefinedTermsRegistry(tmp_path)
        out = reg.register_from_text("dpa-1@1", SAMPLE)
        assert all(r["status"] == "created" for r in out)
        reg2 = DefinedTermsRegistry(tmp_path)
        assert {r["term"] for r in reg2.terms_for("dpa-1@1")} >= {"Services", "Processor"}

    def test_bind_requires_named_actor(self, tmp_path):
        reg = DefinedTermsRegistry(tmp_path)
        reg.register_from_text("dpa-1@1", SAMPLE)
        with pytest.raises(ValueError, match="name the actor"):
            reg.bind("dpa-1@1", "Processor", "acme-gmbh", actor="ingest")

    def test_bind_and_resolve(self, tmp_path):
        reg = DefinedTermsRegistry(tmp_path)
        reg.register_from_text("dpa-1@1", SAMPLE)
        out = reg.bind("dpa-1@1", "Processor", "acme-gmbh", actor="alex")
        assert out["status"] == "bound"
        assert reg.resolve("dpa-1@1", "Processor") == "acme-gmbh"

    def test_unbound_resolves_to_none_never_guesses(self, tmp_path):
        reg = DefinedTermsRegistry(tmp_path)
        reg.register_from_text("dpa-1@1", SAMPLE)
        assert reg.resolve("dpa-1@1", "Services") is None
        assert "Services" in reg.unbound("dpa-1@1")

    def test_unknown_term_bind_refused(self, tmp_path):
        reg = DefinedTermsRegistry(tmp_path)
        with pytest.raises(KeyError):
            reg.bind("dpa-1@1", "Ghost", "x", actor="alex")


# ── versioned span anchors ────────────────────────────────────────────────────

CLAUSE_A = "The processor shall notify the controller within 72 hours."
CLAUSE_B = "The processor shall delete all personal data upon termination."
V1_TEXT = f"1. {CLAUSE_A}\n2. {CLAUSE_B}\n"
V2_TEXT = f"Preamble.\n1. {CLAUSE_A}\n2. The processor may retain anonymised data.\n"


class TestVersionedSpans:
    def _registry(self, tmp_path) -> RuleRegistry:
        reg = RuleRegistry(tmp_path, log_root=tmp_path / "log")
        reg.place_span(CLAUSE_A, source_document="dpa.txt",
                       start=3, end=3 + len(CLAUSE_A),
                       document_hash="sha256:" + "a" * 32, document_version=1)
        reg.place_span(CLAUSE_B, source_document="dpa.txt",
                       start=len(CLAUSE_A) + 7, end=len(CLAUSE_A) + 7 + len(CLAUSE_B),
                       document_hash="sha256:" + "a" * 32, document_version=1)
        return reg

    def test_span_carries_version(self, tmp_path):
        reg = self._registry(tmp_path)
        rec = next(iter(reg.items.values()))
        assert rec["span"]["document_version"] == 1
        assert rec["span"]["document_hash"].startswith("sha256:")

    def test_reanchor_migrates_surviving_span(self, tmp_path):
        reg = self._registry(tmp_path)
        out = reg.reanchor_document("dpa.txt", V2_TEXT,
                                    new_hash="sha256:" + "b" * 32, new_version=2)
        assert len(out["migrated"]) == 1
        rid = out["migrated"][0]
        span = reg.items[rid]["span"]
        assert span["document_version"] == 2
        assert V2_TEXT[span["start"]:span["end"]] == CLAUSE_A   # offsets re-pinned

    def test_reanchor_orphans_removed_span_never_drops(self, tmp_path):
        reg = self._registry(tmp_path)
        out = reg.reanchor_document("dpa.txt", V2_TEXT,
                                    new_hash="sha256:" + "b" * 32, new_version=2)
        assert len(out["orphaned"]) == 1 and out["escalate"] is True
        rid = out["orphaned"][0]
        assert reg.items[rid]["orphaned"]["at_version"] == 2     # still in registry
        assert reg.orphans("dpa.txt")[0]["id"] == rid

    def test_reanchor_other_documents_untouched(self, tmp_path):
        reg = self._registry(tmp_path)
        reg.place_span("The licensee shall pay royalties quarterly.",
                       source_document="licence.txt", document_version=1)
        out = reg.reanchor_document("dpa.txt", V2_TEXT,
                                    new_hash="sha256:" + "b" * 32, new_version=2)
        assert len(out["migrated"]) + len(out["orphaned"]) == 2


# ── NT-11 / NT-12 ─────────────────────────────────────────────────────────────

def _pair(**sol) -> dict:
    return {"id": "p1", "problem": {"type": "rule", "facets": {}},
            "solution": sol, "edges": []}


class TestNT11:
    def test_valid_dates_pass(self):
        out = check_typed_dates(_pair(deadline="2026-07-31",
                                      events={"signing": "2026-06-15"}))
        assert all(f.level.value == "pass" for f in out)

    def test_malformed_deadline_violates(self):
        out = check_typed_dates(_pair(deadline="31.07.2026"))
        assert any(f.level.value == "violation" and f.code == "NT-11" for f in out)

    def test_malformed_event_violates(self):
        out = check_typed_dates(_pair(events={"signing": "next week"}))
        assert any(f.level.value == "violation" for f in out)

    def test_absent_dates_pass(self):
        out = check_typed_dates(_pair(body="x"))
        assert out[0].level.value == "pass"


class TestNT12:
    def test_no_struct_passes(self):
        out = check_predicate_floor(_pair(rule={"condition": "where appropriate"}))
        assert out[0].level.value == "pass"

    def test_confident_struct_passes(self):
        struct = parse_condition("within 30 days of the signing").to_dict()
        out = check_predicate_floor(_pair(rule={"condition_struct": struct}))
        assert all(f.level.value == "pass" for f in out)

    def test_subfloor_struct_violates(self):
        struct = Predicate(kind="state", subject_ref="x", confidence=0.5).to_dict()
        out = check_predicate_floor(_pair(rule={"condition_struct": struct}))
        assert any(f.code == "NT-12" and f.level.value == "violation" for f in out)

    def test_malformed_struct_violates(self):
        out = check_predicate_floor(_pair(rule={"condition_struct": {"kind": "vibe"}}))
        assert any(f.code == "NT-12" and f.level.value == "violation" for f in out)

    def test_check_pair_includes_new_invariants(self):
        codes = {f.code for f in check_pair(_pair(body="x")).findings}
        assert "NT-11" in codes and "NT-12" in codes


# ── typed-date retrofit ───────────────────────────────────────────────────────

class TestRetrofit:
    def test_standing_approval_valid_until(self):
        from rvnd.action_gate import StandingApproval
        sa = StandingApproval(agent="a", action_class="notify",
                              obligation_pair="p1", until="2026-12-31")
        assert sa.until == "2026-12-31"

    def test_standing_approval_malformed_until_rejected_at_write(self):
        from rvnd.action_gate import StandingApproval
        with pytest.raises(ValueError, match="NT-11"):
            StandingApproval(agent="a", action_class="notify",
                             obligation_pair="p1", until="31.12.2026")

    def test_standing_approval_no_expiry_allowed(self):
        from rvnd.action_gate import StandingApproval
        sa = StandingApproval(agent="a", action_class="notify", obligation_pair="p1")
        assert sa.until is None

    def test_approval_request_malformed_deadline_rejected(self, tmp_path):
        from rvnd.contracts.reviews import request_contract_approval
        with pytest.raises(ValueError, match="NT-11"):
            request_contract_approval(tmp_path, contract_id="c1",
                                      signers=["alex"], deadline="soon",
                                      log_root=tmp_path / "log")

    def test_approval_request_valid_deadline_ok(self, tmp_path):
        from rvnd.contracts.reviews import request_contract_approval
        out = request_contract_approval(tmp_path, contract_id="c1",
                                        signers=["alex"], deadline="2026-12-31",
                                        log_root=tmp_path / "log")
        assert out["approval_id"]
