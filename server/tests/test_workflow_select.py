# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""select-context as a workflow step: runs the selection AND records it to the
signed log — proving the selection is a reproducible, audited workflow step, not
agent improvisation."""

from __future__ import annotations

from rvnd.workflow_select import run_select_context_step


WORKSPACE = [{"id": "acme:msa:pay", "text": "Zahlung innerhalb 30 Tagen."},
        {"id": "acme:msa:fee", "text": "Vergütung 10000 EUR monatlich."},
        {"id": "globex:msa:pay", "text": "Zahlung binnen 14 Tagen."}]


def test_step_runs_selection_and_records_a_signed_event(tmp_path):
    res = run_select_context_step(
        str(tmp_path / "acme"),
        {"entity": "acme", "legal_system": "DE", "corpus": WORKSPACE,
         "clause_needs": ["Zahlung Tagen", "Vergütung EUR"],
         "card_facets": {"vat_status": "reverse-charge"},
         "fact_needs": [{"key": "vat_status", "prompt": "VAT?", "scope": "standing"}]},
        log_root=str(tmp_path / ".log"), run_id="r1", step_index=0)

    # the selection happened …
    ids = {c["doc_id"] for c in res["bundle"]["clauses"]}
    assert ids == {"acme:msa:pay", "acme:msa:fee"}      # entity-scoped; Globex excluded
    assert res["bundle"]["facts"]["vat_status"]["source"] == "standing"
    assert res["complete"]
    # … and it was RECORDED (signed audit id), not a silent model choice
    assert res["audit_id"]


def test_step_records_incompleteness_when_a_required_clause_is_missing(tmp_path):
    res = run_select_context_step(
        str(tmp_path / "acme"),
        {"entity": "acme", "legal_system": "DE",
         "clause_needs": ["Schiedsgerichtsklausel"], "corpus": WORKSPACE},
        log_root=str(tmp_path / ".log"), run_id="r2")
    assert not res["complete"]
    assert "Schiedsgerichtsklausel" in res["bundle"]["missing_clauses"]
    assert res["audit_id"]            # even a refusal is logged


def test_corpus_can_be_passed_per_run(tmp_path):
    res = run_select_context_step(
        str(tmp_path / "acme"),
        {"entity": "acme", "legal_system": "DE", "corpus": WORKSPACE,
         "clause_needs": ["Vergütung EUR"]},
        log_root=str(tmp_path / ".log"))
    assert [c["doc_id"] for c in res["bundle"]["clauses"]] == ["acme:msa:fee"]
