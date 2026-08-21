# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Three legal-retrieval gaps: corpus read-coverage, negative search, and the
richer (gap-surfacing) subsumption chain."""

from __future__ import annotations

import pytest

from rvnd.corpus import coverage as cc
from rvnd import negative_search as ns
from rvnd import subsumption_path as sp


# ── Vollständigkeit at the ingestion layer: 30 declared, 23 read ─────────────

def test_unread_documents_are_surfaced_not_dropped():
    declared = [f"doc{i}" for i in range(30)]
    processed = [f"doc{i}" for i in range(23)]        # only 23 of 30 actually read
    rep = cc.assess(declared, processed)
    assert rep.total == 30 and len(rep.read) == 23
    assert len(rep.unread) == 7                        # the silent-drop made visible
    assert not rep.complete
    assert abs(rep.ratio - 23 / 30) < 1e-9


def test_skipped_needs_a_reason_else_it_is_unread():
    rep = cc.assess(["a", "b", "c"], ["a"], skipped={"b": "out of scope (different Land)"})
    assert rep.complete is False                        # c is unread
    assert rep.unread == ["c"]
    assert rep.skipped[0].reason


def test_require_full_blocks_an_incomplete_corpus():
    rep = cc.assess(["a", "b"], ["a"])
    with pytest.raises(cc.CorpusIncomplete):
        cc.require_full(rep)
    full = cc.assess(["a", "b"], ["a", "b"])
    assert cc.require_full(full).complete


# ── Negative search: prove what was looked for and ruled out ─────────────────

def test_negative_search_probes_every_mandatory_category():
    corpus = [
        {"id": "n1", "text": "Der Anbieter muss die Daten offenlegen."},
        {"id": "e1", "text": "Von der Einziehung kann abgesehen werden, soweit unbillig."},
        {"id": "c1", "text": "BGH, Urteil vom 1.1.2020, Az. X ZR 1/19, Rn. 5."},
    ]
    rec = ns.run("Rückforderung trotz Härtefall?", corpus)
    assert rec.complete                                 # all mandatory categories searched
    cats = {p.category: p.hits for p in rec.probes}
    assert cats["exception"] == ["e1"]
    assert cats["counter-jurisprudence"] == ["c1"]


def test_absence_of_an_exception_is_a_recorded_negative_not_an_assumption():
    corpus = [{"id": "n1", "text": "Der Anspruch besteht in Höhe von X."}]
    rec = ns.run("Anspruch auf Leistung X", corpus)
    assert "exception" in rec.found_nothing             # searched, found none — recorded
    assert "transitional" in rec.found_nothing
    probe = next(p for p in rec.probes if p.category == "exception")
    assert probe.searched and not probe.hits and "negative result" in probe.note


def test_excluded_documents_carry_a_reason():
    corpus = [{"id": "e1", "text": "abweichend hiervon gilt ..."}]
    rec = ns.run("q", corpus, excluded={"exception": {"e1": "applies only to public bodies"}})
    probe = next(p for p in rec.probes if p.category == "exception")
    assert probe.excluded and probe.excluded[0]["reason"]


# ── Subsumption chain with the five gaps surfaced ────────────────────────────

def test_complete_chain_has_all_required_roles_and_no_blocking_gap():
    atoms = [
        {"role": "norm", "ref": "§1", "source": "BGBl", "authority_tier": 2},
        {"role": "tatbestand", "ref": "tb", "authority_tier": 2},
        {"role": "subsumtion", "ref": "sub", "authority_tier": 2},
        {"role": "ergebnis", "ref": "erg", "authority_tier": 2},
    ]
    s = sp.build(atoms)
    assert s.complete
    assert "norm:§1" in s.render()


def test_missing_required_role_is_a_retrieval_gap():
    atoms = [{"role": "norm", "ref": "§1"}, {"role": "tatbestand", "ref": "tb"}]
    s = sp.build(atoms)                                 # no subsumtion / ergebnis
    assert not s.complete
    kinds = {g.kind for g in s.gaps}
    assert "retrieval" in kinds


def test_conflict_is_surfaced_not_smoothed():
    atoms = [{"role": "norm", "ref": "§1"}, {"role": "tatbestand", "ref": "tb"},
             {"role": "subsumtion", "ref": "sub"}, {"role": "ergebnis", "ref": "erg"}]
    s = sp.build(atoms, conflicts=[{"a": "Urteil2018", "b": "Urteil2024",
                                    "detail": "divergent Auslegung of X"}])
    assert any(g.kind == "conflict" for g in s.gaps)
    assert not s.complete                               # a conflict blocks completeness


def test_partial_link_is_a_context_gap():
    atoms = [{"role": "norm", "ref": "§1", "partial": True},
             {"role": "tatbestand", "ref": "tb"},
             {"role": "subsumtion", "ref": "sub"}, {"role": "ergebnis", "ref": "erg"}]
    s = sp.build(atoms)
    assert any(g.kind == "context" for g in s.gaps)
    assert s.complete                                   # context gap surfaced, not blocking


def test_unlinked_role_is_a_reasoning_gap():
    atoms = [{"role": "norm", "ref": "§1"}, {"role": "auslegung", "ref": "UrteilC"},
             {"role": "tatbestand", "ref": "tb"}, {"role": "subsumtion", "ref": "sub"},
             {"role": "ergebnis", "ref": "erg"}]
    # edges connect everything EXCEPT the auslegung UrteilC
    edges = [{"subject": "tb", "object": "sub"}, {"subject": "sub", "object": "erg"}]
    s = sp.build(atoms, edges=edges)
    assert any(g.kind == "reasoning" and "UrteilC" in g.detail for g in s.gaps)
