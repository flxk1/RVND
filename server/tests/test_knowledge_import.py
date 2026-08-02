# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Importing an external source registry into the grounding works layer."""

from __future__ import annotations

from workspaces import knowledge_import
from workspaces.workspace_grounder import GroundingLedger


_CSV = (
    "canonical_urn,title_guess,author_or_institution_guess,document_type,"
    "detected_year,jurisdiction,topics,inference_level\n"
    "urn:dls:celex:32024r1689,AI Act,European Union,legal_act,2024,EU,"
    "AI Act and Regulation; AI and Law,identity-derived\n"
    "urn:dls:doi:10.1000/xyz,Some Paper,Author A,article,2023,,Machine Learning,"
    "filename-derived\n"
    "urn:dls:source:field-note,Field Note,,other,2022,,,filename-derived\n"
    "urn:dls:ecli:ecli-de-bgh-2024-1,A Ruling,BGH,case,2024,DE,Case Law,"
    "identity-derived\n"
    "urn:dls:celex:32024r1689,AI Act (duplicate file),European Union,legal_act,"
    "2024,EU,AI Act and Regulation,identity-derived\n"
    "urn:dls:source:,,,other,,,,\n"
)


def test_import_registers_works_on_the_neutral_spine(tmp_path):
    csv_path = tmp_path / "source_registry.csv"
    csv_path.write_text(_CSV, encoding="utf-8")
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    summary = knowledge_import.import_source_registry(corpus, csv_path)
    assert summary["imported"] == 4        # celex, doi, source, ecli
    assert summary["deduped"] == 1         # the second celex row
    assert summary["skipped"] == 1         # the row with no title and no id

    urns = {w["canonical_urn"] for w in GroundingLedger(corpus).works.values()}
    assert "urn:lg:celex:32024r1689" in urns      # re-minted from urn:dls: -> urn:lg:
    assert "urn:lg:doi:10.1000/xyz" in urns
    assert "urn:lg:ecli:ecli-de-bgh-2024-1" in urns   # a non-CELEX namespace imports too
    assert "urn:lg:source:field-note" in urns


def test_import_maps_document_type_and_attribution(tmp_path):
    csv_path = tmp_path / "reg.csv"
    csv_path.write_text(_CSV, encoding="utf-8")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    knowledge_import.import_source_registry(corpus, csv_path)

    by_urn = {w["canonical_urn"]: w for w in GroundingLedger(corpus).works.values()}
    ai_act = by_urn["urn:lg:celex:32024r1689"]
    assert ai_act["type"] == "statute"            # legal_act -> statute
    assert ai_act["creators"] == [{"name": "European Union", "role": "author"}]
    assert by_urn["urn:lg:ecli:ecli-de-bgh-2024-1"]["type"] == "case"
    # jurisdiction + topics become facet tags; inference_level rides as confidence
    assert "jurisdiction:EU" in ai_act["tags"]
    assert "topic:AI Act and Regulation" in ai_act["tags"]
    assert ai_act["confidence"] == "identity-derived"
