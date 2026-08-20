# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tiered-eval tests. Tier 1 (clean templates) keeps its floors; tiers 2–3
are measured, not gated — EXCEPT the one invariant that holds everywhere:
zero silently-wrong values. Incomplete is allowed; wrong-and-confident is not.

Also pins the extractor behaviours the hard tiers forced into existence:
conflicting governing law abstains, time quantities never become money,
written-out dates parse, OCR-shattered party names never emit as garbage."""


from workspaces.contracts.eval import run_tiers
from workspaces.contracts.extractor import (classify_contract_type,
                                      extract_effective_date,
                                      extract_governing_law, extract_parties)
from workspaces.predicate import parse_condition
from workspaces.temporal import Date


class TestTierStructure:
    def test_all_three_tiers_present(self):
        out = run_tiers()
        assert set(out["tiers"]) == {"tier1", "tier2", "tier3"}
        assert out["tiers"]["tier2"]["contracts"] == 3
        assert out["tiers"]["tier3"]["contracts"] == 3

    def test_tier1_floors_still_enforced(self):
        rep = run_tiers()["tiers"]["tier1"]
        assert rep["ok"], f"tier1 floor breach: {rep['breaches']}"

    def test_silently_wrong_zero_everywhere(self):
        out = run_tiers()
        assert out["silently_wrong_total"] == 0, (
            "a wrong-and-confident extraction exists: "
            + str([n for t in out["tiers"].values() for n in t["notes"]
                   if "SILENTLY WRONG" in n]))

    def test_overall_pass(self):
        assert run_tiers()["ok"]

    def test_tier2_recall_is_measured_not_hidden(self):
        rep = run_tiers()["tiers"]["tier2"]
        assert 0 < rep["scores"]["obligations_recall"] <= 1
        assert rep["floors"] == {"silently_wrong": 0}     # no other gates


class TestHardCaseBehaviours:
    def test_conflicting_governing_law_abstains(self):
        text = ("This Agreement shall be governed by the laws of England and "
                "Wales. Notwithstanding the foregoing, IP disputes shall be "
                "governed by the laws of the State of Delaware.")
        assert extract_governing_law(text) == (None, 0.0)

    def test_single_governing_law_still_extracts(self):
        text = "This Agreement is governed by the laws of England and Wales."
        assert extract_governing_law(text)[0] == "UK"

    def test_undated_effectiveness_clause_stays_empty(self):
        text = "This Agreement is effective as of the date of the last signature."
        assert extract_effective_date(text)[0] is None

    def test_dayfirst_english_date(self):
        d, _ = extract_effective_date("shall become effective on 1 October 2026.")
        assert d == Date("2026-10-01")

    def test_german_textual_date(self):
        d, _ = extract_effective_date(
            "Dieser Vertrag tritt mit Wirkung zum 15. Januar 2027 in Kraft.")
        assert d == Date("2027-01-15")

    def test_elapsed_days_never_become_money(self):
        p = parse_condition("where an invoice remains unpaid for more than "
                            "30 days after the due date")
        assert p is None or p.unit not in ("DAY", "DAYS")
        assert p is None                       # full abstention expected

    def test_real_money_threshold_still_parses(self):
        p = parse_condition("where the aggregate revenue exceeds EUR 250,000")
        assert p is not None and p.value == "250000" and p.unit == "EUR"

    def test_ocr_shattered_party_name_repaired_or_dropped(self):
        text = 'between  Brandgaard  Soft-\nware ApS (the "Supplier") and others'
        parties = extract_parties(text)
        assert parties and parties[0].name == "Brandgaard Software ApS"

    def test_legal_form_only_name_dropped_not_emitted(self):
        text = 'between GmbH (the "Supplier") and someone'
        assert extract_parties(text) == []

    def test_amendment_classified_as_amendment_not_licence(self):
        text = ("AMENDMENT NO. 1 / NACHTRAG NR. 1 zum Lizenzvertrag vom "
                "01.10.2026. The License Agreement is amended as follows.")
        assert classify_contract_type(text)[0] == "amendment"

    def test_pinpoint_german_section_and_absatz(self):
        from workspaces.contracts.extractor import derive_pinpoint
        text = ("§ 1 Begriffe\n\nEgal.\n\n§ 2 Pflichten\n\n(1) Erstens.\n\n"
                "(2) Die Auftragnehmerin muss melden.\n")
        pos = text.find("Die Auftragnehmerin muss")
        assert derive_pinpoint(text, pos) == "§ 2 (2)"

    def test_pinpoint_decimal_clause(self):
        from workspaces.contracts.extractor import derive_pinpoint
        text = "3. INVOICING\n\n3.1 The Client shall pay.\n\n3.2 Late payment.\n"
        assert derive_pinpoint(text, text.find("Late payment")) == "3.2"

    def test_pinpoint_numbered_heading(self):
        from workspaces.contracts.extractor import derive_pinpoint
        text = "4. Breach notification. The Processor shall notify.\n"
        assert derive_pinpoint(text, text.find("The Processor")) == "4."

    def test_pinpoint_absent_structure_is_empty_not_guessed(self):
        from workspaces.contracts.extractor import derive_pinpoint
        text = "The parties agree as follows. The Supplier shall deliver.\n"
        assert derive_pinpoint(text, text.find("The Supplier")) == ""

    def test_ingested_clause_spans_carry_fundstelle(self, tmp_path):
        from workspaces.contracts.extractor import ingest_contract
        from workspaces.rule_registry import RuleRegistry
        text = ("TEST AGREEMENT\n\nThis Agreement is made between A GmbH "
                "(the \"Processor\") and B AG (the \"Controller\").\n\n"
                "4. Breach notification. The Processor shall notify the "
                "Controller of a personal data breach without undue delay.\n")
        ingest_contract(tmp_path, text, contract_id="pp-x",
                        log_root=tmp_path / "log")
        spans = RuleRegistry(tmp_path, log_root=tmp_path / "log")
        pins = [r["span"].get("pinpoint") for r in spans.items.values()]
        assert "4." in pins

    def test_feminine_german_roles_extract(self):
        text = ("zwischen der Beispiel Cloud GmbH (nachfolgend "
                "„Auftragnehmerin“) und der Volkmann Handels AG (nachfolgend "
                "„Auftraggeberin“)")
        roles = {p.role for p in extract_parties(text)}
        assert roles == {"auftragnehmerin", "auftraggeberin"}
