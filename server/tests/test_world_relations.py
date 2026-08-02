# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P0 relational pass: the law between the nodes — memberships, enforcement,
treaty bindings, adequacy, lineage, conformity. Plus the loader fixes it forced
(org codes, China subtable)."""

from __future__ import annotations

import pytest

from workspaces import world_relations as wr
from workspaces.legal_world import EntityKind
from workspaces.world_corpus_loader import _default_refdir

if not _default_refdir().is_dir():
    pytest.skip(
        "world-map corpus not installed — set WORKSPACE_WORLD_MAP_DIR or seed "
        "~/.workspace/world-map (ships with the eu-regulatory-companion)",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def world():
    w, stats = wr.build_enriched_world()
    return w, stats


def _edges(w, rel=None, subject=None, obj=None):
    out = []
    for ed in w.edges:
        if rel and ed.connection.value != rel:
            continue
        if subject and ed.subject != subject:
            continue
        if obj and ed.object != obj:
            continue
        out.append(ed)
    return out


def test_graph_is_substantially_richer(world):
    w, stats = world
    assert len(w.edges) > 700                       # was 305 before the pass
    rels = {ed.connection.value for ed in w.edges}
    assert {"member_of", "applies_in", "adopted_by", "established_by", "enforces",
            "supervises", "party_to", "bound_by", "equivalent_to",
            "descends_from", "presumes_conformity", "supersedes"} <= rels


def test_eu_acquis_merged_with_supersession(world):
    w, _ = world
    assert "gdpr" in w.entities and "ai-act" in w.entities and "dsa" in w.entities
    assert _edges(w, "supersedes", subject="gdpr", obj="dpd-95")
    assert _edges(w, "applies_in", subject="ai-act", obj="EU")


def test_no_regulator_is_orphaned(world):
    w, _ = world
    regs = [e.code for e in w.entities.values() if e.kind is EntityKind.REGULATOR]
    connected = {ed.subject for ed in w.edges}
    orphans = [r for r in regs if r not in connected]
    assert orphans == [], f"orphan regulators: {orphans}"


def test_regulators_supervise_and_enforce(world):
    w, _ = world
    assert _edges(w, "supervises", subject="cnil", obj="FR")
    assert _edges(w, "enforces", subject="cnil", obj="gdpr")        # Art. 51/55
    assert _edges(w, "supervises", subject="cac", obj="CN")
    assert _edges(w, "enforces", subject="cac")                     # PIPL/DSL/CSL etc.
    assert _edges(w, "enforces", subject="cppa")                    # CCPA/CPRA


def test_china_laws_are_in_the_corpus(world):
    w, _ = world
    names = " ".join(e.name.lower() for e in w.entities.values())
    assert "personal information protection law" in names or "pipl" in names
    assert _edges(w, "applies_in", obj="CN")


def test_memberships(world):
    w, _ = world
    assert _edges(w, "member_of", subject="DE", obj="EU")
    assert _edges(w, "member_of", subject="UA", obj="coe")          # CoE incl. Ukraine
    assert not _edges(w, "member_of", subject="RU", obj="coe")      # expelled 2022
    assert _edges(w, "member_of", subject="SG", obj="asean")
    assert _edges(w, "member_of", subject="NG", obj="au")
    assert _edges(w, "member_of", subject="JP", obj="oecd")


def test_treaty_bindings(world):
    w, _ = world
    assert _edges(w, "party_to", subject="DE")                      # ECHR/Berne/Budapest
    trips = [ed for ed in _edges(w, "bound_by") if ed.subject == "US"]
    assert trips and "TRIPS" in trips[0].basis                      # WTO ⇒ TRIPS


def test_adequacy_decisions(world):
    w, _ = world
    targets = {ed.object for ed in _edges(w, "equivalent_to", subject="EU")}
    assert {"JP", "UK", "KR", "US"} <= targets
    assert all("verify" in ed.basis.lower() for ed in _edges(w, "equivalent_to"))


def test_lineage_and_conformity(world):
    w, _ = world
    assert _edges(w, "descends_from")                               # WCT→Berne etc.
    pc = _edges(w, "presumes_conformity")
    assert pc and any(ed.object == "ai-act" for ed in pc)           # M/593 → AI Act


def test_every_derived_edge_has_a_basis(world):
    w, _ = world
    assert all((ed.basis or "").strip() for ed in w.edges)
