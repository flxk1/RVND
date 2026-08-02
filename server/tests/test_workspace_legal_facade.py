# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""One facade tool (`workspace_legal`) bundles the legal ops behind an
op enum — proving capabilities can be grouped instead of 1 tool per function."""

from __future__ import annotations

from workspaces.workspace_legal_facade import workspace_legal_op, ops_catalogue


def test_one_tool_exposes_the_legal_ops():
    cat = ops_catalogue()
    ops = {c["op"] for c in cat}
    assert ops == {"card.save", "card.load", "card.list", "facts.form",
                   "facts.record", "select.context", "select.context_step",
                   "subsumption.validate", "pipeline.run_class_c"}
    # self-documenting: each op lists its required params
    assert all("required" in c and "doc" in c for c in cat)


def test_unknown_op_returns_error_not_exception():
    r = workspace_legal_op("nope")
    assert "error" in r and "valid_ops" in r


def test_missing_required_param_is_reported():
    r = workspace_legal_op("card.load", {"folder_context": "/x"})  # subject_id missing
    assert "missing params" in r["error"]


def test_facts_form_op_returns_the_minimal_form():
    r = workspace_legal_op("facts.form", {
        "needs": [{"key": "vat", "prompt": "VAT?", "scope": "standing"},
                  {"key": "po", "prompt": "PO?", "scope": "per_case"}],
        "standing": {"vat": "reverse-charge"}})
    assert r["prefilled"]["vat"] == "reverse-charge"
    assert any(q["key"] == "po" for q in r["questions"])      # the one genuine unknown


def test_select_context_op_scopes_to_entity_with_provenance():
    r = workspace_legal_op("select.context", {
        "entity": "acme", "legal_system": "DE",
        "clause_needs": ["Zahlung Tagen"],
        "corpus": [{"id": "acme:msa:pay", "text": "Zahlung innerhalb 30 Tagen."},
                   {"id": "globex:msa:pay", "text": "Zahlung binnen 14 Tagen."}]})
    ids = {c["doc_id"] for c in r["clauses"]}
    assert ids == {"acme:msa:pay"}            # globex excluded by entity scope


def test_card_roundtrip_through_the_facade(tmp_path):
    save = workspace_legal_op("card.save", {
        "folder_context": str(tmp_path / "acme"), "log_root": str(tmp_path / ".log"),
        "card": {"domain": "invoice", "subject_id": "acme",
                 "facets": {"vat_status": "reverse-charge"}}})
    assert save["audit_id"]
    load = workspace_legal_op("card.load", {
        "folder_context": str(tmp_path / "acme"), "subject_id": "acme"})
    assert load["found"] and load["card"]["facets"]["vat_status"] == "reverse-charge"
