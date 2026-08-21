# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Case records: problem → grounds (receipts/gaps) → chain → resolution."""

from __future__ import annotations

import pytest

from rvnd import problem_kg, legal_corpus
from rvnd.decisions import surface as ds
from rvnd.rule_registry import RuleRegistry

GDPR = """REGULATION (EU) 2016/679 (General Data Protection Regulation)
Article 17
1. The data subject shall have the right to obtain erasure of personal data without undue delay.
3. Paragraphs 1 and 2 shall not apply to the extent processing is necessary for compliance with a legal obligation.
Article 33
1. The controller shall notify the personal data breach to the supervisory authority within 72 hours."""


@pytest.fixture()
def registry(tmp_path):
    legal_corpus.seed_registry(tmp_path)
    reg = RuleRegistry(tmp_path, user="alex")
    reg.place_legal_text(GDPR, "gdpr", source_document="gdpr.txt")
    return reg


def test_determinate_case_with_full_coverage(registry):
    case = problem_kg.build_case(
        "When must the controller notify a personal data breach under Regulation (EU) 2016/679?",
        registry=registry, document="memo.md",
        required_rooms=["Art. 33(1)"],
        answer="Within 72 hours of awareness (GDPR Art. 33(1)); document per Art. 33(5).")
    assert case.resolution["type"] == "determinate"
    assert case.coverage == 1.0 and case.gaps == []
    assert case.grounds[0].receipted and "33" in case.grounds[0].pinpoint


def test_gap_is_reported_never_hidden(registry):
    case = problem_kg.build_case(
        "Erasure obligations under Regulation (EU) 2016/679?", registry=registry,
        required_rooms=["Art. 17(1)", "Art. 17(3)", "Art. 19"],   # Art. 19 not ingested
        answer="n/a")
    assert "Art. 19" in case.gaps
    assert case.coverage < 1.0


def test_residual_case_carries_surface_and_choice(registry):
    surface = ds.build_surface("Erase on request?", [
        {"id": "erase", "label": "Erase", "conclusion": "erase",
         "supporting": [{"pinpoint": "Art. 17(1)"}], "consequences": ["notify recipients"]},
        {"id": "retain", "label": "Retain & restrict", "conclusion": "retain",
         "supporting": [{"pinpoint": "Art. 17(3)(b)"}], "consequences": ["restrict (Art. 18)"]},
    ], esc_reason="17(1) vs 17(3)(b)")
    choice = ds.record_choice(surface, chosen_option_id="retain",
                              rationale="§147 AO retention engages 17(3)(b)", actor="alex")
    case = problem_kg.build_case(
        "Must we erase the customer's data on request (Regulation (EU) 2016/679)?",
        registry=registry, required_rooms=["Art. 17(1)", "Art. 17(3)"],
        chain=[{"step": "Norm", "text": "Art. 17(1) erasure right"},
               {"step": "Ausnahme", "text": "Art. 17(3)(b) legal obligation"},
               {"step": "Subsumtion", "text": "§ 147 AO covers invoiced fields"},
               {"step": "Ergebnis", "text": "retain-and-restrict the obliged fields"}],
        surface=surface, choice=choice)
    assert case.resolution["type"] == "residual"
    assert case.resolution["choice"]["chosen_label"] == "Retain & restrict"
    assert len(case.chain) == 4 and case.coverage == 1.0


def test_residual_without_choice_is_open_not_an_answer(registry):
    surface = ds.build_surface("q", [
        {"id": "a", "label": "A", "conclusion": "a"},
        {"id": "b", "label": "B", "conclusion": "b"}])
    case = problem_kg.build_case("Open question under Regulation (EU) 2016/679",
                                 registry=registry, required_rooms=["Art. 17(1)"],
                                 surface=surface, choice=None)
    assert case.resolution["type"] == "residual" and case.resolution["choice"] is None


def test_determinate_and_residual_are_mutually_exclusive(registry):
    surface = ds.build_surface("q", [{"id": "a", "label": "A", "conclusion": "a"},
                                     {"id": "b", "label": "B", "conclusion": "b"}])
    with pytest.raises(ValueError):
        problem_kg.build_case("q", registry=registry, answer="x", surface=surface)


def test_projection_emits_dimensioned_pairs(registry):
    case = problem_kg.build_case(
        "Breach notification timing under Regulation (EU) 2016/679?",
        registry=registry, required_rooms=["Art. 33(1)", "Art. 34"],
        answer="72h to the authority (Art. 33(1)).")
    pairs = problem_kg.project_pairs(case, case_id="c1")
    kinds = {p["problem"]["type"] for p in pairs}
    assert {"problem", "ground", "gap", "resolution"} <= kinds
    edges = [e for p in pairs for e in p["edges"]]
    preds = {e["predicate"] for e in edges}
    assert {"grounded_in", "required_room_missing", "resolved_by"} <= preds
    from rvnd import reasoning
    assert reasoning.extract_edges(pairs)          # composes with the 5D machinery


# ── end-to-end: contract → case files ─────────────────────────────────────────

_DPA = """The processor shall notify the controller of any personal data breach without undue delay in accordance with Art. 33 Regulation (EU) 2016/679.
The processor shall process personal data only on documented instructions pursuant to Art. 28 Regulation (EU) 2016/679.
Der Auftragsverarbeiter muss Rechnungsdaten gemäß § 147 AO aufbewahren."""


def test_contract_to_case_files_end_to_end(registry):
    cases = problem_kg.cases_for_document(
        _DPA, registry=registry, document="dpa.md",
        decisions={0: {"answer": "Notify within 72 hours of awareness (Art. 33(1))."}})
    assert len(cases) == 3
    breach = next(c for c in cases if "breach" in c.problem["text"])
    assert breach.resolution["type"] == "determinate"
    assert any("33" in g.pinpoint for g in breach.grounds)       # held → receipted
    instr = next(c for c in cases if "Art. 28" in c.problem["text"])
    assert "Art. 28" in instr.gaps                               # cited, text not held → gap
    ao = next(c for c in cases if "147" in c.problem["text"])
    assert any("147" in g for g in ao.gaps)                      # § 147 AO text not held → gap
    assert ao.resolution["type"] == "open"                       # no decision → OPEN, not an answer


def test_printable_record_contains_the_audit_essentials(registry):
    cases = problem_kg.cases_for_document(_DPA, registry=registry, document="dpa.md")
    html = problem_kg.render_case_record_html(cases, document="dpa.md", title="DPA case record")
    for marker in ("Clauses examined", "Open gaps", "Reviewed and signed",
                   "relative to the corpus"):
        assert marker in html


def test_norm_pairs_carry_tatbestand_to_rechtsfolge_not_label_twice(registry):
    """A legal norm IS a problem→solution pair: condition → consequence.
    The projection must use that structure, not echo the label into both slots."""
    case = problem_kg.build_case(
        "Breach notification timing under Regulation (EU) 2016/679?",
        registry=registry, required_rooms=["Art. 33(1)"], answer="72h (Art. 33(1)).")
    g = case.grounds[0]
    assert "notify" in g.consequence                     # Rechtsfolge extracted
    pairs = problem_kg.project_pairs(case, case_id="c2")
    ground = next(p for p in pairs if p["problem"]["type"] == "ground")
    # solution = the Rechtsfolge, never the label echoed
    assert "notify" in ground["solution"]["body"]
    assert ground["problem"]["summary"] != ground["solution"]["body"]
    # with a conditional norm, the problem slot is the Tatbestand as question



def test_conditional_norm_problem_slot_is_the_tatbestand(tmp_path):
    legal_corpus.seed_registry(tmp_path)
    from rvnd.rule_registry import RuleRegistry
    reg = RuleRegistry(tmp_path, user="reviewer")
    reg.place_legal_text(
        """REGULATION (EU) 2016/679 (General Data Protection Regulation)
Article 33
1. In the case of a personal data breach, the controller shall notify the supervisory authority within 72 hours.""",
        "gdpr", source_document="g.txt")
    case = problem_kg.build_case("Breach? (Regulation (EU) 2016/679)",
                                 registry=reg, required_rooms=["Art. 33(1)"],
                                 answer="72h")
    assert case.grounds[0].condition                     # Tatbestand present here
    pairs = problem_kg.project_pairs(case, case_id="c3")
    ground = next(p for p in pairs if p["problem"]["type"] == "ground")
    # agreed schema, verbatim: condition in the problem slot — no template
    assert ground["problem"]["summary"] == case.grounds[0].condition
    assert "what follows" not in ground["problem"]["summary"]


# ── gap closure by fetch: the system is the janitor for public law ───────────

_ART34 = """REGULATION (EU) 2016/679 (General Data Protection Regulation)
Article 34
1. When the personal data breach is likely to result in a high risk to the rights and freedoms of natural persons, the controller shall communicate the personal data breach to the data subject without undue delay."""


def test_gap_closed_by_fetch_mints_a_real_receipt(registry):
    case = problem_kg.build_case(
        "Breach duties? (Regulation (EU) 2016/679)", registry=registry,
        required_rooms=["Art. 33(1)", "Art. 34"], answer="see record")
    assert "Art. 34" in case.gaps and case.coverage == 0.5
    fetch = lambda inst, cite: {"text": _ART34,
                                "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj"}
    problem_kg.close_gap_by_fetch(case, "Art. 34", registry=registry, fetch_fn=fetch)
    assert case.gaps == [] and case.coverage == 1.0
    g34 = next(g for g in case.grounds if "34" in g.pinpoint)
    assert g34.receipted and "communicate the personal data breach" in g34.consequence
    assert case.contract["fetched"][0]["url"].startswith("https://eur-lex")


def test_fetch_must_prove_itself_no_receipt_without_the_text(registry):
    case = problem_kg.build_case(
        "Breach duties? (Regulation (EU) 2016/679)", registry=registry,
        required_rooms=["Art. 33(1)", "Art. 34"], answer="x")
    wrong = lambda inst, cite: {"text": "Article 99\n1. Irrelevant content here shall apply.",
                                "url": "https://example.org"}
    with pytest.raises(ValueError):
        problem_kg.close_gap_by_fetch(case, "Art. 34", registry=registry, fetch_fn=wrong)
    declined = lambda inst, cite: None
    out = problem_kg.close_gap_by_fetch(case, "Art. 34", registry=registry,
                                        fetch_fn=declined)
    assert "Art. 34" in out.gaps                        # honest gap, unchanged
