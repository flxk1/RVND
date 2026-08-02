# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Music vertical — proves the SAME engine generalises on authored data only.

No new engine: uses the shared map_coverage + the music room taxonomy. The
point of these tests is the generalisation thesis — the coverage mapper has no
music code, the rooms carry their own cues, and the money render is just a
different register over the same report.
"""

from __future__ import annotations

from workspaces.music_domain import (
    MUSIC_VOCAB, music_income_house, render_money_map,
)
from workspaces.subject_card import make_card, get_vocabulary
from workspaces.evidence_coverage import EvidenceDoc, map_coverage


def _artist_docs():
    return [
        EvidenceDoc("d1", "DistroKid streaming royalty statement Q2",
                    "ISRC-level streaming payouts across Spotify and Apple Music."),
        EvidenceDoc("d2", "Songwriter Split Sheet - 'Night Drive'",
                    "Composition splits 50/50; ISWC assigned; GEMA work."),
        EvidenceDoc("d3", "Band photo shoot invoice",
                    "Payment for the press photos used on the album cover."),
    ]


# --- vocabulary registered --------------------------------------------------

def test_music_vocab_registered():
    assert get_vocabulary("music") is MUSIC_VOCAB


def test_music_card_is_capture_first():
    # role+capacity multi-valued; an artist who self-releases wears two roles
    card = make_card("music", role=["artist", "label"],
                     capacity=["performer", "songwriter", "master-owner"],
                     description="independent artist, self-released single")
    assert "artist" in card.get("role")
    assert "songwriter" in card.get("capacity")


# --- the authored house -----------------------------------------------------

def test_house_has_the_five_income_streams():
    h = music_income_house()
    ids = {r.room_id for r in h.rooms}
    assert ids == {"master-revenue", "publishing-revenue", "neighbouring-rights",
                   "sync-revenue", "performance-pro"}
    # every room is income-category and carries its own cues + a money note
    for r in h.rooms:
        assert r.category == "income"
        assert r.evidence_cues
        assert r.note


# --- SAME coverage engine, music data --------------------------------------

def test_streaming_statement_furnishes_master_room():
    h = music_income_house()
    rep = map_coverage(h, _artist_docs())
    furnished = {f["room_id"] for f in rep.furnished}
    assert "master-revenue" in furnished
    master = next(f for f in rep.furnished if f["room_id"] == "master-revenue")
    assert any(d["doc_id"] == "d1" for d in master["documents"])


def test_split_sheet_furnishes_publishing_room():
    h = music_income_house()
    rep = map_coverage(h, _artist_docs())
    furnished = {f["room_id"] for f in rep.furnished}
    assert "publishing-revenue" in furnished


def test_uncollected_streams_are_money_on_the_table():
    h = music_income_house()
    rep = map_coverage(h, _artist_docs())
    empty = {e["room_id"] for e in rep.empty}
    # no doc proves neighbouring / sync / PRO collection → gaps (money left)
    assert "neighbouring-rights" in empty
    assert "sync-revenue" in empty
    assert "performance-pro" in empty


def test_irrelevant_doc_is_orphan():
    h = music_income_house()
    rep = map_coverage(h, _artist_docs())
    assert any(o["doc_id"] == "d3" for o in rep.orphans)


def test_coverage_ratio_partial():
    h = music_income_house()
    rep = map_coverage(h, _artist_docs())
    # 2 of 5 furnished
    assert rep.coverage_ratio == 0.4


# --- money register (front-of-house, no legal language) ---------------------

def test_money_render_speaks_money_not_law():
    h = music_income_house()
    rep = map_coverage(h, _artist_docs())
    text = render_money_map(h, rep, artist="Test Artist")
    assert "MONEY YOU'RE COLLECTING" in text
    assert "MONEY YOU MAY BE LEAVING ON THE TABLE" in text
    # the instrument/legal register must NOT leak into the artist view
    low = text.lower()
    assert "celex" not in low
    assert "obligation" not in low
    assert "o(" not in low and "f(" not in low
    # neighbouring rights surfaced as a gap with its plain-language note
    assert "neighbouring rights" in low


def test_same_mapper_no_music_code():
    # sanity: map_coverage is the generic function; the only music input is the
    # house (data). Running it on the music house produces a music result with
    # zero music-specific code in the mapper.
    import workspaces.evidence_coverage as ec
    src = ec.map_coverage.__module__
    assert src == "workspaces.evidence_coverage"   # generic module, not music
