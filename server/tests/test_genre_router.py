# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Genre router — policy ingest knows the document genres and picks the right structure.

Proves: (1) the genre of EU regulation / ISO standard / German §-statute / contract / generic
is detected from structural fingerprints; (2) the genre's non-normative preamble is dropped
(an EU regulation's recitals); (3) law genres segment into provisions via legal_norm_splitter.
"""
from __future__ import annotations

from workspaces import genre_router as G

EU_ACT = (
    "(1) The purpose of this Regulation is to improve the functioning of the internal market.\n"
    "(2) Whereas AI systems can cause harm, safeguards are needed.\n"
    "HAVE ADOPTED THIS REGULATION:\n"
    "Article 1\nSubject matter\n"
    "1. This Regulation lays down harmonised rules on artificial intelligence.\n"
    "Article 5\nProhibited practices\n"
    "1. The following AI practices shall be prohibited: social scoring of natural persons.\n"
)
ISO_STD = (
    "ISO/IEC 42001:2023(en)\n3 Terms and definitions\n3.1 AI system engineered system\n"
    "4 Context of the organization\n4.1 The organization shall determine external issues.\n"
    "5 Leadership\nTop management shall establish an AI policy.\n"
)
DE_LAW = (
    "Betriebsverfassungsgesetz\n§ 1 Errichtung von Betriebsräten\n"
    "§ 87 Mitbestimmungsrechte Der Betriebsrat hat mitzubestimmen.\n"
    "§ 90 Unterrichtung Der Arbeitgeber hat den Betriebsrat zu unterrichten.\n"
)
CONTRACT = ("This Agreement is made between the parties. WHEREAS the Provider wishes to supply "
            "services, the parties agree as follows. The Provider shall deliver the services.")
GENERIC = "All automated decisions must be reviewed by a manager before they take effect."


def test_detect_genre_across_five_shapes():
    assert G.detect_genre(EU_ACT) == "eu-regulation"
    assert G.detect_genre(ISO_STD) == "iso-standard"
    assert G.detect_genre(DE_LAW) == "paragraph-statute"
    assert G.detect_genre(CONTRACT) == "contract"
    assert G.detect_genre(GENERIC) == "generic"


def test_eu_regulation_drops_recitals_and_segments_articles():
    r = G.route(EU_ACT)
    assert r["genre"] == "eu-regulation" and r["jurisdiction"] == "EU"
    body = r["body"]
    assert "Whereas" not in body and "purpose of this Regulation" not in body   # recitals gone
    assert "Article 1" in body and "Prohibited practices" in body               # articles kept
    assert r["n_units"] >= 1                                                     # provisions cut


def test_iso_standard_routes_without_law_segmentation():
    r = G.route(ISO_STD)
    assert r["genre"] == "iso-standard"
    assert r["jurisdiction"] is None and r["n_units"] == 0     # not a law-genre provision split


def test_de_statute_segments_by_paragraph():
    r = G.route(DE_LAW)
    assert r["genre"] == "paragraph-statute" and r["jurisdiction"] == "DE"
    assert r["n_units"] >= 1                                   # § provisions cut


def test_ingest_prepare_returns_genre_and_normative_body():
    genre, text = G.ingest_prepare(EU_ACT)
    assert genre == "eu-regulation"
    assert "shall be prohibited" in text and "Whereas" not in text
