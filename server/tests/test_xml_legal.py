# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the structure-aware legal XML reader (xml_legal.py).

Layer-1: parse the article/paragraph/point hierarchy and native cross-references
from EUR-Lex XML instead of guessing them from flattened text.
"""

from __future__ import annotations

from workspaces.xml_legal import (
    DocumentTree,
    ProvisionNode,
    all_cross_refs,
    document_tree_to_text,
    parse_akoma_ntoso,
    parse_formex,
    parse_legal_xml,
)


# A compact Akoma-Ntoso-shaped fixture: one article, two paragraphs, a point,
# and a cross-reference to the GDPR by CELEX href. Namespaced to mimic real AKN.
AKN = """<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRWork>
          <FRBRuri value="/akn/eu/act/regulation/2024/1689"/>
          <FRBRalias value="CELEX:32024R1689" name="celex"/>
        </FRBRWork>
      </identification>
    </meta>
    <preface><docTitle>Regulation (EU) 2024/1689 (AI Act)</docTitle></preface>
    <body>
      <article eId="art_26">
        <num>Article 26</num>
        <heading>Obligations of deployers of high-risk AI systems</heading>
        <paragraph eId="art_26__para_1">
          <num>1</num>
          <content><p>Deployers of high-risk AI systems shall take appropriate
          technical and organisational measures.</p></content>
        </paragraph>
        <paragraph eId="art_26__para_9">
          <num>9</num>
          <content><p>Deployers shall carry out a data protection impact
          assessment under <ref href="CELEX:32016R0679">Regulation (EU)
          2016/679</ref>.</p></content>
        </paragraph>
      </article>
    </body>
  </act>
</akomaNtoso>
"""


# Minimal Formex 4 fixture.
FORMEX = """<?xml version="1.0" encoding="UTF-8"?>
<ACT>
  <TITLE><TI>Regulation (EU) 2016/679</TI></TITLE>
  <ARTICLE IDENTIFIER="art_28">
    <NO.ART>Article 28</NO.ART>
    <TI.ART>Processor</TI.ART>
    <PARAG><NO.PARAG>1</NO.PARAG>
      <ALINEA>The controller shall use only processors providing sufficient
      guarantees.</ALINEA>
    </PARAG>
    <PARAG><NO.PARAG>3</NO.PARAG>
      <ALINEA>Processing by a processor shall be governed by a contract.</ALINEA>
    </PARAG>
  </ARTICLE>
</ACT>
"""


# --- Akoma Ntoso ------------------------------------------------------------

def test_akn_identity():
    t = parse_akoma_ntoso(AKN)
    assert t.format == "akoma-ntoso"
    assert t.celex == "32024R1689"
    assert "AI Act" in t.title


def test_akn_hierarchy():
    t = parse_akoma_ntoso(AKN)
    eids = {n.eId for n in t.nodes}
    assert "art_26" in eids
    assert "art_26__para_1" in eids
    assert "art_26__para_9" in eids
    # paragraphs carry the article as parent
    p1 = next(n for n in t.nodes if n.eId == "art_26__para_1")
    assert p1.parent_eId == "art_26"
    assert p1.kind == "paragraph"


def test_akn_leaf_text():
    t = parse_akoma_ntoso(AKN)
    p1 = next(n for n in t.nodes if n.eId == "art_26__para_1")
    assert "appropriate" in p1.text
    # the heading lives on the article node, not the paragraph
    art = next(n for n in t.nodes if n.eId == "art_26")
    assert "deployers" in art.heading.lower()


def test_akn_native_cross_reference_resolves_celex():
    t = parse_akoma_ntoso(AKN)
    refs = all_cross_refs(t)
    celexes = {r.target_celex for r in refs}
    assert "32016R0679" in celexes
    # and the ref is attached to the paragraph that contained it
    p9 = next(n for n in t.nodes if n.eId == "art_26__para_9")
    assert any(r.target_celex == "32016R0679" for r in p9.refs)


def test_akn_cross_ref_is_not_double_counted_on_parent():
    t = parse_akoma_ntoso(AKN)
    art = next(n for n in t.nodes if n.eId == "art_26")
    # the article-level node must not inherit the paragraph's ref
    assert all(r.target_celex != "32016R0679" for r in art.refs)


# --- Formex -----------------------------------------------------------------

def test_formex_hierarchy_and_text():
    t = parse_formex(FORMEX)
    assert t.format == "formex"
    arts = [n for n in t.nodes if n.kind == "article"]
    assert arts and arts[0].eId == "art_28"
    paras = [n for n in t.nodes if n.kind == "paragraph"]
    assert len(paras) == 2
    assert any("contract" in p.text for p in paras)


# --- dispatch + projection --------------------------------------------------

def test_parse_legal_xml_dispatches_akn():
    assert parse_legal_xml(AKN).format == "akoma-ntoso"


def test_parse_legal_xml_dispatches_formex():
    assert parse_legal_xml(FORMEX).format == "formex"


def test_document_tree_to_text_is_block_structured():
    t = parse_akoma_ntoso(AKN)
    text = document_tree_to_text(t)
    # provisions separated into blocks (double newline), structure preserved
    assert "\n\n" in text
    assert "appropriate technical" in text
    assert "Article 26" in text


def test_leaf_provisions_only_returns_text_bearing_nodes():
    t = parse_akoma_ntoso(AKN)
    leaves = t.leaf_provisions()
    # the two paragraphs have text; the article node itself has empty own-text
    assert all(n.text.strip() for n in leaves)
    assert {n.eId for n in leaves} >= {"art_26__para_1", "art_26__para_9"}
