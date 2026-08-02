# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Article-level norm extraction: a law's own provisions enter the ND-rule map
individually, each anchored to its instrument with its article pinpoint.

The fixture is real GDPR operative text (a handful of articles) so the test
exercises actual legal drafting, not synthetic sentences.
"""

from __future__ import annotations

from workspaces import legal_norm_splitter as splitter
from workspaces import legal_corpus
from workspaces.rule_registry import RuleRegistry


# Real GDPR operative text (abridged to a few articles; wording verbatim).
GDPR = """REGULATION (EU) 2016/679 (General Data Protection Regulation)

Article 5
Principles relating to processing of personal data
1. Personal data shall be processed lawfully, fairly and in a transparent manner in relation to the data subject.
2. The controller shall be responsible for, and be able to demonstrate compliance with, paragraph 1.

Article 6
Lawfulness of processing
1. Processing shall be lawful only if and to the extent that at least one of the following applies: (a) the data subject has given consent to the processing of his or her personal data for one or more specific purposes; (b) processing is necessary for the performance of a contract.

Article 17
Right to erasure
1. The data subject shall have the right to obtain from the controller the erasure of personal data concerning him or her without undue delay.
3. Paragraphs 1 and 2 shall not apply to the extent that processing is necessary for compliance with a legal obligation which requires processing by Union or Member State law.

Article 33
Notification of a personal data breach to the supervisory authority
1. In the case of a personal data breach, the controller shall without undue delay and, where feasible, not later than 72 hours after having become aware of it, notify the personal data breach to the supervisory authority.
"""


def _seed(tmp_path):
    legal_corpus.seed_registry(tmp_path)        # so the world map has gdpr→EU→EDPB
    return RuleRegistry(tmp_path, user="alex")


# ── segmentation ──────────────────────────────────────────────────────────────

def test_segments_articles_and_paragraphs_with_pinpoints():
    provs = splitter.segment_provisions(GDPR)
    pins = [p.pinpoint for p in provs]
    assert "Art. 5(1)" in pins and "Art. 6(1)" in pins
    assert "Art. 17(1)" in pins and "Art. 17(3)" in pins and "Art. 33(1)" in pins
    # Art. 17 yields two distinct paragraph-level provisions
    assert sum(1 for p in provs if p.article == "17") == 2


def test_german_paragraph_segmentation():
    text = "§ 286\n1. Der Schuldner kommt in Verzug.\n2. Dem Verzug steht es gleich."
    provs = splitter.segment_provisions(text)
    assert {p.pinpoint for p in provs} >= {"§ 286(1)", "§ 286(2)"}


# ── per-article norms in the ND-rule map ──────────────────────────────────────

def test_law_text_places_individual_norms_anchored_to_the_article(tmp_path):
    reg = _seed(tmp_path)
    out = reg.place_legal_text(GDPR, "gdpr", source_document="gdpr.txt")
    assert out["instrument"] == "gdpr"
    assert out["count"] >= 5                      # several distinct norms
    # every placed norm carries an article pinpoint and is anchored to gdpr+EU
    pins = {p["pinpoint"] for p in out["placed"]}
    assert {"Art. 5(1)", "Art. 17(1)", "Art. 33(1)"} <= pins
    for rec in reg.workspace_items():
        ents = {a["entity"] for a in rec["anchors"]}
        assert "gdpr" in ents and "EU" in ents
        assert rec["span"]["pinpoint"].startswith("Art.")


def test_each_norm_keeps_its_own_pinpoint_as_cites_basis(tmp_path):
    reg = _seed(tmp_path)
    reg.place_legal_text(GDPR, "gdpr", source_document="gdpr.txt")
    # the Art. 33 breach-notification obligation is its own norm, basis = Art. 33(1)
    art33 = [r for r in reg.workspace_items() if r["span"]["pinpoint"] == "Art. 33(1)"]
    assert art33
    cites = next(a for a in art33[0]["anchors"] if a["entity"] == "gdpr")
    assert cites["basis"] == "Art. 33(1)"
    assert art33[0]["norm"]["modal"] == "obligation"     # "shall ... notify"


def test_distinct_articles_are_distinct_rule_items(tmp_path):
    reg = _seed(tmp_path)
    reg.place_legal_text(GDPR, "gdpr", source_document="gdpr.txt")
    # Art. 17(1) (right) and Art. 17(3) (exception) are separate entries
    a17 = {r["span"]["pinpoint"] for r in reg.workspace_items() if r["span"]["pinpoint"].startswith("Art. 17")}
    assert {"Art. 17(1)", "Art. 17(3)"} <= a17


def test_ingest_routes_law_text_to_article_extraction(tmp_path):
    # the public hook recognises GDPR and routes to per-article extraction
    legal_corpus.seed_registry(tmp_path)
    from workspaces import rule_registry
    out = rule_registry.place_into_registry(str(tmp_path), GDPR, source_document="gdpr.txt")
    assert out.get("instrument") == "gdpr" and out["count"] >= 5
