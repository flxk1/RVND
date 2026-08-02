# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""National (German) statute citations: recognition, corpus auto-provisioning,
and span-norm anchoring."""

from __future__ import annotations

from workspaces import national_citations as nc
from workspaces.corpus import ingest as corpus_ingest
from workspaces import legal_corpus
from workspaces.rule_registry import RuleRegistry


# ── recognition ───────────────────────────────────────────────────────────────

def test_recognises_paragraph_and_article_citations():
    cits = {c.code: c for c in nc.extract_citations(
        "Der Anspruch folgt aus § 286 Abs. 1 BGB; vgl. § 147 AO und Art. 229 EGBGB.")}
    assert set(cits) == {"bgb", "ao", "egbgb"}
    assert cits["bgb"].section == "§ 286"
    assert cits["bgb"].url == "https://www.gesetze-im-internet.de/bgb/"
    assert cits["egbgb"].section == "Art. 229"


def test_curated_slug_url_and_uncurated_none():
    cits = {c.code: c for c in nc.extract_citations("§ 257 HGB und § 99 TKG")}
    assert cits["hgb"].url == "https://www.gesetze-im-internet.de/hgb/"
    assert cits["tkg"].url is None          # uncurated slug → no guessed link


def test_bare_mention_without_section():
    cits = {c.code: c for c in nc.extract_citations("Die Verarbeitung richtet sich nach dem BDSG.")}
    assert "bdsg" in cits and cits["bdsg"].section == ""


def test_no_false_positive_on_plain_text():
    assert nc.extract_citations("This clause has no German statute citation.") == []


# ── corpus candidates merge EU + national ────────────────────────────────────

def test_candidates_from_text_includes_eu_and_national():
    text = ("Erasure under Regulation (EU) 2016/679, subject to the retention duty "
            "in § 147 AO and § 257 HGB.")
    codes = {c["code"] for c in corpus_ingest.candidates_from_text(text)}
    assert {"gdpr", "ao", "hgb"} <= codes


def test_cited_statute_auto_provisions_into_corpus(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    out = corpus_ingest.ingest_document(reg, "Schadensersatz nach § 280 BGB.")
    assert "bgb" in out["found"]
    urls = {u["code"]: u["url"] for u in legal_corpus.EntityRegistry(tmp_path).urls()}
    assert urls.get("bgb") == "https://www.gesetze-im-internet.de/bgb/"


# ── span-norm anchoring for German clauses ───────────────────────────────────

def test_german_clause_anchors_to_national_statute_and_DE(tmp_path):
    reg = RuleRegistry(tmp_path, user="alex")
    r = reg.place_span("Der Schuldner muss bei Verzug nach § 286 Abs. 1 BGB Zinsen zahlen.",
                       source_document="vertrag.md")
    anchors = {(a["entity"], a["relation"]) for a in r["anchors"]}
    assert ("bgb", "cites") in anchors
    assert ("DE", "governed_by") in anchors
    # the pinpoint rides along as the cites-anchor basis
    bgb = next(a for a in r["anchors"] if a["entity"] == "bgb")
    assert bgb["basis"] == "§ 286"


def test_mixed_de_eu_clause_anchors_to_both(tmp_path):
    reg = RuleRegistry(tmp_path, user="alex")
    r = reg.place_span(
        "Daten sind nach Regulation (EU) 2016/679 zu löschen, soweit nicht § 147 AO entgegensteht.",
        source_document="dpa.md")
    entities = {a["entity"] for a in r["anchors"]}
    assert {"gdpr", "ao", "EU", "DE"} <= entities
