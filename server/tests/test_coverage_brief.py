# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The bounded supervisor brief over a coverage report.

`render_coverage` shows a supervisor everything. `coverage_brief` shows only
what is unsettled and says how much it left out. The property that matters is
that its size tracks the org's gaps, not the size of its document set — a run
over hundreds of furnished rooms with one gap is still one thing to read.

Selection belongs to `loomground-brief`; these tests pin the RVND-side mapping
and the property, not the plane's internals.
"""
from __future__ import annotations

from workspaces.evidence_coverage import EvidenceDoc, coverage_brief, map_coverage
from workspaces.requirements_house import RequirementsHouse, Room


def _house(n_furnished: int = 2, with_gap: bool = True) -> RequirementsHouse:
    rooms = [Room(room_id=f"r{i}", title=f"Room {i}", category="governance",
                  evidence_cues=[f"cue{i}end"]) for i in range(n_furnished)]
    if with_gap:
        rooms.append(Room(room_id="gap", title="Unevidenced", category="conformity",
                          evidence_cues=["nothing-matches-this"]))
    return RequirementsHouse(domain="test", rooms=rooms)


def _docs(n: int, orphan: bool = False) -> list[EvidenceDoc]:
    docs = [EvidenceDoc(f"d{i}", f"Doc {i}", f"cue{i}end") for i in range(n)]
    if orphan:
        docs.append(EvidenceDoc("orphan", "Offsite notes", "unrelated minutes"))
    return docs


def test_an_empty_room_is_a_gap_the_supervisor_must_read():
    report = map_coverage(_house(), _docs(2))
    brief = coverage_brief(report)
    assert any(item.ref == "room:gap" for item in brief.items)


def test_a_furnished_room_is_omitted_and_counted_not_shown():
    report = map_coverage(_house(), _docs(2))
    brief = coverage_brief(report)
    assert brief.settled_omitted == 2
    assert not any(item.ref.startswith("room:r") for item in brief.items)


def test_an_orphan_document_is_surfaced_rather_than_filed_silently():
    report = map_coverage(_house(), _docs(2, orphan=True))
    brief = coverage_brief(report)
    assert any(item.ref == "doc:orphan" for item in brief.items)


def test_brief_size_tracks_gaps_not_document_volume():
    """The whole point. Ten furnished rooms or five hundred, one gap is one
    thing to read — otherwise the brief is just the report again."""
    sizes = []
    for n in (2, 20, 200):
        report = map_coverage(_house(n_furnished=n), _docs(n))
        brief = coverage_brief(report)
        sizes.append(len(brief.items))
        assert brief.settled_omitted == n
    assert sizes == [1, 1, 1]


def test_a_fully_furnished_house_asks_the_supervisor_to_read_nothing():
    report = map_coverage(_house(with_gap=False), _docs(2))
    brief = coverage_brief(report)
    assert brief.items == () or list(brief.items) == []
    assert brief.settled_omitted == 2
