# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Music vertical — domain #2, proving the engine generalises on authored data.

This is the same grounded-compliance engine pointed at music, to validate the
thesis: the matcher, house builder, and coverage mapper contain NO domain logic;
a vertical is just (vocabulary + room taxonomy + register) — all authored,
human-validatable knowledge, no engine change.

Key reframe vs. the AI-Act domain:
- There, rooms were *extracted* from one statute (obligations → required
  artifacts). Here, the rooms are the **known income streams an artist should be
  collecting** — public, dated, re-verifiable knowledge (how master /
  publishing / neighbouring / sync / performance rights and their collectors
  work). So the house is **authored**, not extracted. That is exactly the
  governing principle: public knowledge ships populated; the *specific* artist's
  deal stays the human's input (their split sheet, their statements).
- The payoff register is **money**, not law: an EMPTY room = an income stream
  you are not collecting = money left on the table. Same coverage arithmetic,
  different words.

Rooms carry their own ``evidence_cues`` (how a document proves the stream is
being collected) and a ``note`` (what the stream is, in plain words), so the
shared coverage mapper + a money renderer need no music-specific code.
"""

from __future__ import annotations


from .subject_card import DomainVocabulary, FacetSpec, register_vocabulary
from .requirements_house import Room, RequirementsHouse
from .evidence_coverage import CoverageReport


# ---------------------------------------------------------------------------
# Vocabulary (subject card facets for a music rights-holder)
# ---------------------------------------------------------------------------

MUSIC_VOCAB = DomainVocabulary(
    domain="music",
    facets=(
        FacetSpec("role",
                  ("artist", "label", "publisher", "manager", "distributor"),
                  "What the user is in the value chain.", multi=True),
        FacetSpec("capacity",
                  ("performer", "songwriter", "master-owner", "publisher-owner",
                   "featured-artist", "session-musician"),
                  "Which rights-bearing capacities the user holds on the work.",
                  multi=True),
        FacetSpec("exploitation",
                  ("streaming", "download", "physical", "sync", "radio-tv",
                   "public-performance", "user-generated"),
                  "How the music is exploited (drives which streams apply).",
                  multi=True),
        FacetSpec("territory", ("de", "eu", "us", "uk", "worldwide"),
                  "Primary territory (affects which CMOs collect)."),
        FacetSpec("released", ("yes", "no"),
                  "Has the work been released/distributed yet."),
    ),
    # capacity → which income streams (rooms) become relevant, as is-a edges
    # used by any future matcher; the house here is authored directly.
    subsumption=(
        ("performer", "neighbouring-rights"),
        ("master-owner", "master-revenue"),
        ("songwriter", "publishing-revenue"),
        ("publisher-owner", "publishing-revenue"),
    ),
)


# ---------------------------------------------------------------------------
# The house: income-stream rooms (authored public knowledge)
# ---------------------------------------------------------------------------

def _room(room_id, title, note, cues) -> Room:
    return Room(room_id=room_id, title=title, category="income",
                note=note, evidence_cues=cues,
                sources=["public music-rights framework (authored)"])


def music_income_house(*, title: str = "Music income streams") -> RequirementsHouse:
    """The authored house of income streams an artist should be collecting.

    Each room is a revenue stream; furnished = you hold the registration /
    agreement / statement that proves you're collecting it; empty = money on
    the table.
    """
    h = RequirementsHouse(domain="music", title=title)
    h.rooms = [
        _room("master-revenue",
              "Master / recording revenue (streaming & downloads)",
              "Royalties from your sound recording via your distributor/label "
              "(Spotify, Apple, etc.).",
              ("distribution", "distributor", "distrokid", "tunecore", "cd baby",
               "believe", "streaming royalty", "master royalty", "isrc",
               "phonographic", "label agreement", "recording agreement")),
        _room("publishing-revenue",
              "Publishing / songwriter revenue (mechanical & performance)",
              "Royalties for the composition — mechanical + performance — via "
              "your publisher and PRO/collecting society.",
              ("publishing agreement", "songwriter", "composition", "iswc",
               "mechanical royalty", "publishing royalty", "gema", "ascap",
               "prs", "sacem", "mlc", "split sheet", "songwriter split")),
        _room("neighbouring-rights",
              "Neighbouring rights (performer & master, via CMO)",
              "Performer + phonogram-producer royalties from radio, public "
              "performance and similar — collected by a CMO (e.g. GVL, PPL).",
              ("neighbouring rights", "gvl", "ppl", "soundexchange",
               "performer registration", "related rights", "leistungsschutz")),
        _room("sync-revenue",
              "Sync licensing (film / TV / ads / games)",
              "Fees from licensing your music into audiovisual works — needs a "
              "sync agent / catalogue registration.",
              ("sync licence", "sync license", "synchronisation", "sync agent",
               "music supervisor", "licensing agreement", "master use",
               "sync placement")),
        _room("performance-pro",
              "Public-performance royalties (PRO/CMO registration)",
              "Performance royalties when your work is played publicly or "
              "broadcast — needs your works registered with your PRO/CMO.",
              ("pro registration", "society registration", "works registration",
               "gema registration", "ascap registration", "performing rights")),
    ]
    return h


def register() -> None:
    """Register the music vocabulary so cards/matcher can resolve it."""
    register_vocabulary(MUSIC_VOCAB)


# auto-register on import (idempotent)
register()


# ---------------------------------------------------------------------------
# Money-register renderer (front-of-house; NEVER legal/obligation language)
# ---------------------------------------------------------------------------

def render_money_map(house: RequirementsHouse, report: CoverageReport,
                     *, artist: str = "") -> str:
    """Render the coverage report in the MONEY register for an artist/label.

    Same data the compliance memo uses, completely different words: furnished =
    "you're collecting this", empty = "money you may be leaving on the table",
    orphan = "a document I couldn't match to a stream". No CELEX, no operators,
    no 'obligation' — the instrument side stays back-of-house.
    """
    out: list[str] = []
    who = f" — {artist}" if artist else ""
    out.append("=" * 70)
    out.append(f"YOUR MUSIC MONEY MAP{who}".upper())
    out.append("=" * 70)
    out.append("Draft, for orientation — not financial or legal advice. "
               "'Maybe missing' needs your confirmation.")
    pct = int(report.coverage_ratio * 100)
    out.append(f"\nYou appear to be collecting {len(report.furnished)} of "
               f"{len(report.furnished) + len(report.empty)} income streams "
               f"({pct}%).")
    out.append("")

    out.append("MONEY YOU'RE COLLECTING")
    for f in report.furnished:
        room = house.room(f["room_id"])
        out.append(f"  ✓ {f['title']}")
        if room and room.note:
            out.append(f"      {room.note}")
        for d in f["documents"]:
            out.append(f"      evidence: {d['title']} "
                       f"({int(d['confidence']*100)}%)")
    if not report.furnished:
        out.append("  (no income streams confirmed yet)")
    out.append("")

    out.append("MONEY YOU MAY BE LEAVING ON THE TABLE")
    for e in report.empty:
        room = house.room(e["room_id"])
        out.append(f"  ✗ {e['title']}")
        if room and room.note:
            out.append(f"      {room.note}")
        out.append(f"      → no document on file showing you collect this — confirm.")
    if not report.empty:
        out.append("  (every stream has evidence — nice)")
    out.append("")

    if report.orphans:
        out.append("DOCUMENTS I COULDN'T PLACE")
        for o in report.orphans:
            out.append(f"  ? {o['title']} — doesn't match a known stream "
                       f"(or a stream I haven't modelled)")
        out.append("")

    out.append("Every stream above is public-knowledge; your specific deals and "
               "statements are what furnish each room. Nothing here leaves your "
               "workspace.")
    return "\n".join(out)
