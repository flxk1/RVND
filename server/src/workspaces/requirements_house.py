# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Requirements house — a structured tree of what an instrument requires.

The NDs produce flat atoms: obligation pairs, required-artifact pairs,
cross-references. This module *assembles* them into a "house": a set of
**rooms**, where each room is a requirement node — a thing the org must do or
produce — with its obligations, the artifact it calls for, and its
cross-references all hanging under it.

A room is keyed, in order of preference, to:
  1. the instrument's own architecture — an Akoma Ntoso ``eId`` / article number
     (so room = "Article 9: Risk Management System"), when the source was parsed
     structure-aware; else
  2. a required-artifact (room = "DPIA", "Technical Documentation"); else
  3. the obligation's bearer + a hash (a bare-obligation room).

Why this shape: it turns "47 obligations" into "8 rooms you must furnish",
which is what a compliance owner actually plans against — and it gives the
coverage mapper concrete targets to place documents into. Every room traces to
a source provision, so the house is auditable, not a vibe-grouping.

Assembly only — no new judgment. Deterministic. The room taxonomy is domain
data (the required-artifact catalogue), so the same builder works for any
domain's NDs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Room:
    """One requirement node: a thing the org must do/produce/collect."""
    room_id: str                       # stable key (artifact key, eId, or hash)
    title: str                         # human label
    category: str                      # assessment | policy | register | contract
                                       # | appointment | technical | obligation
                                       # | income (music) | ...
    artifact_key: str = ""             # the required-artifact key, if this room
                                       # corresponds to a deliverable
    obligations: list[dict[str, Any]] = field(default_factory=list)  # pair ids + text
    cross_refs: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # eIds / citations / scope
    # Recognition cues for the coverage mapper: how an evidence document
    # announces it furnishes THIS room. Domain-authored; when present they make
    # placement room-local (no global table needed). Optional.
    evidence_cues: tuple[str, ...] = ()
    # Free-text "why this room exists / what it's worth" — used by money/plain
    # registers (e.g. "neighbouring-rights royalties via your CMO").
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RequirementsHouse:
    domain: str
    title: str = ""
    rooms: list[Room] = field(default_factory=list)

    def room(self, room_id: str) -> Optional[Room]:
        return next((r for r in self.rooms if r.room_id == room_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {"domain": self.domain, "title": self.title,
                "rooms": [r.to_dict() for r in self.rooms]}


def _hash(s: str) -> str:
    return "room:" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _ob_brief(pair: dict[str, Any]) -> dict[str, Any]:
    """A compact obligation reference for hanging in a room."""
    sol = pair.get("solution") or {}
    prob = pair.get("problem") or {}
    return {
        "pair_id": pair.get("id", ""),
        "operator": sol.get("operator", ""),
        "bearer": sol.get("bearer", ""),
        "action": sol.get("action", ""),
        "source": str(prob.get("source_document")
                      or sol.get("source_eId")
                      or (sol.get("cited_sources") or [""])[0]
                      or prob.get("scope", "")),
        "applicability": pair.get("applicability", {}),
    }


# Keywords that link an obligation's text to a required-artifact room, so an
# obligation "shall draw up the technical documentation" hangs under the
# Technical Documentation room even though the artifact ND emitted them
# separately. Shared vocabulary with the artifact catalogue triggers.
_ARTIFACT_HINT = {
    "dpia": ("impact assessment", "data protection impact"),
    "fria": ("fundamental rights impact",),
    "conformity-assessment": ("conformity assessment",),
    "ropa": ("record of processing", "records of processing"),
    "risk-management-system": ("risk management system",),
    "technical-documentation": ("technical documentation",),
    "logs": ("logs", "logging", "record-keeping", "record keeping"),
    "toms": ("technical and organisational measures", "technical and organizational"),
    "dpo": ("data protection officer",),
    "privacy-policy": ("privacy policy", "information to be provided"),
    "dpa": ("data processing agreement", "governed by a contract"),
}


def _obligation_artifact_key(pair: dict[str, Any]) -> str:
    """Which artifact room (if any) this obligation belongs under, by its text."""
    sol = pair.get("solution") or {}
    text = f"{sol.get('action','')} {sol.get('bearer','')}".lower()
    for key, hints in _ARTIFACT_HINT.items():
        if any(h in text for h in hints):
            return key
    return ""


def build_house(
    *,
    obligations: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    cross_refs: Optional[list[dict[str, Any]]] = None,
    domain: str = "",
    title: str = "",
) -> RequirementsHouse:
    """Assemble the atoms into a requirements house.

    1. Every required-artifact becomes a room (the deliverables).
    2. Each obligation is hung in its artifact room if its text references that
       artifact; otherwise it gets its own bearer-keyed obligation room.
    3. Cross-references are attached to the rooms whose obligations mention the
       same source, and also kept at house level via a synthetic 'cross-refs'
       room is NOT created — they hang on rooms only.
    """
    house = RequirementsHouse(domain=domain, title=title)
    rooms_by_artifact: dict[str, Room] = {}

    # 1. artifact rooms
    for a in artifacts:
        sol = a.get("solution") or {}
        key = sol.get("artifact", "") or a.get("problem", {}).get("facets", {}).get("artifact", "")
        if not key or key in rooms_by_artifact:
            continue
        room = Room(
            room_id=key,
            title=sol.get("artifact_name", key),
            category=sol.get("category", "obligation"),
            artifact_key=key,
            sources=[str(sol.get("trigger_phrase", "") or a.get("problem", {}).get("scope", ""))],
        )
        rooms_by_artifact[key] = room
        house.rooms.append(room)

    # 2. hang obligations
    for ob in obligations:
        akey = _obligation_artifact_key(ob)
        brief = _ob_brief(ob)
        if akey and akey in rooms_by_artifact:
            rooms_by_artifact[akey].obligations.append(brief)
            if brief["source"]:
                rooms_by_artifact[akey].sources.append(brief["source"])
        else:
            # bare-obligation room keyed by bearer (group same-bearer duties)
            bearer = (brief["bearer"] or "general").strip().lower()
            rid = _hash(f"{domain}:{bearer}")
            room = house.room(rid)
            if room is None:
                room = Room(room_id=rid,
                            title=f"Obligations on: {bearer}",
                            category="obligation",
                            sources=[])
                house.rooms.append(room)
            room.obligations.append(brief)
            if brief["source"] and brief["source"] not in room.sources:
                room.sources.append(brief["source"])

    # 3. attach cross-refs to rooms whose obligations cite the same instrument
    for cr in (cross_refs or []):
        for room in house.rooms:
            if room.obligations:   # cross-refs hang where duties live
                room.cross_refs.append(cr)
                break  # attach once (to the first furnished-with-duty room)

    return house


def build_house_from_text(instrument_text: str, domain: str,
                          *, title: str = "") -> RequirementsHouse:
    """Convenience: run the NDs over instrument text, then assemble the house."""
    from .nd_routing import DefaultClassifier
    from .deontic_facets import extract_deontic_pairs
    from .instrument_obligation_extractor import RequiredArtifactExtractor
    from .crossref_extractor import extract_cross_references
    from .applicability import enrich_pairs

    cls = DefaultClassifier().classify(instrument_text)
    # TODO(flow): consume the deontic facet as a patchbay relation
    # (versum → solver → patchbay → rvnd) rather than re-reading the surface here.
    obligations = extract_deontic_pairs(instrument_text, source_document=domain)
    enrich_pairs(obligations, domain)
    artifacts = RequiredArtifactExtractor().extract(instrument_text, cls, source_document=domain)
    refs = [r.to_dict() for r in extract_cross_references(instrument_text, host_key=domain)]
    return build_house(obligations=obligations, artifacts=artifacts,
                       cross_refs=refs, domain=domain, title=title)
