# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The class-C pipeline gate (corpus → negative → chain → validate → contract)
and the live-connector binding that self-populates the currency registry."""

from __future__ import annotations

from datetime import date

from workspaces import law_sources as lsrc
from workspaces import legal_pipeline as lp
import workspaces.currency as cur


# ── Live-connector binding ───────────────────────────────────────────────────

class _StubEurLexConnector:
    """Stands in for a real EUR-Lex / Legal-Data-Hunter MCP. Returns EUR-Lex-shaped
    records; the registry self-populates through the deterministic adapter."""
    source_id = "eur-lex"
    _DB = {
        "32016R0679": {"celex": "32016R0679", "dateOfEffect": "2018-05-25", "title": "GDPR"},
        "31995L0046": {"celex": "31995L0046", "dateOfEffect": "1995-12-13",
                       "dateEndValidity": "2018-05-25", "repealedBy": "32016R0679"},
    }

    def fetch_instrument(self, ref):
        return self._DB[ref]

    def search(self, query, *, jurisdiction="", as_of=""):
        return list(self._DB.values())


def test_connector_self_populates_the_registry():
    reg = lsrc.populate_from_connector(["32016R0679", "31995L0046"], _StubEurLexConnector())
    assert cur.validity_status(reg.get("32016R0679"), date(2024, 1, 1)) == "in-force"
    assert cur.validity_status(reg.get("31995L0046"), date(2024, 1, 1)) == "superseded"


# ── The class-C pipeline ─────────────────────────────────────────────────────

def _conforming_pair():
    return {"id": "c1", "problem": {"id": "c1-p", "type": "rule", "facets": {
                "modal": "muss", "has_exception": False,
                "applicability": {"role": "provider"}, "jurisdiction": ["EU"]}},
            "solution": {"id": "c1", "authority_tier": 1, "confidence": 0.95,
                "source": "CELEX:32024R1689 Art. 9",
                "temporal": {"status": "in-force", "date_source": "registry"}},
            "edges": []}


def _good_atoms():
    return [
        {"role": "norm", "ref": "Art.9", "source": "CELEX:32024R1689", "authority_tier": 2},
        {"role": "tatbestand", "ref": "tb", "source": "CELEX:32024R1689", "authority_tier": 2},
        {"role": "subsumtion", "ref": "sub", "source": "CELEX:32024R1689", "authority_tier": 2},
        {"role": "ergebnis", "ref": "erg", "source": "CELEX:32024R1689", "authority_tier": 2},
    ]


CORPUS = [{"id": "d1", "text": "Der Anbieter muss ein Risikomanagementsystem einrichten."},
          {"id": "d2", "text": "Allgemeine Hinweise zur Anwendung."}]


def test_pipeline_certifies_a_complete_case():
    res = lp.run_class_c(
        declared_docs=["d1", "d2"], processed_docs=["d1", "d2"],
        query="Pflichten des Anbieters", corpus=CORPUS,
        atoms=_good_atoms(), pairs=[_conforming_pair()], legal_system="EU")
    assert res.ok and res.verdict == "CERTIFIED", res.to_dict()


def test_pipeline_refuses_when_not_all_documents_were_read():
    res = lp.run_class_c(
        declared_docs=["d1", "d2", "d3"], processed_docs=["d1", "d2"],   # d3 unread
        query="q", corpus=CORPUS, atoms=_good_atoms(), pairs=[_conforming_pair()])
    assert res.verdict == "REFUSED"
    assert res.blocked[0].stage == "corpus"
    assert "d3" in res.blocked[0].detail["unread"]


def test_pipeline_refuses_on_a_broken_subsumption_chain():
    incomplete = [{"role": "norm", "ref": "n", "source": "CELEX:x"}]  # missing tb/sub/erg
    res = lp.run_class_c(
        declared_docs=["d1"], processed_docs=["d1"], query="q",
        corpus=[CORPUS[0]], atoms=incomplete, pairs=[_conforming_pair()])
    assert res.verdict == "REFUSED"
    assert any(s.stage == "subsumption" and s.status == "blocked" for s in res.stages)


def test_pipeline_escalates_when_discretion_is_present():
    corpus = CORPUS + [{"id": "d3", "text": "Die Behörde kann nach Ermessen absehen, im Härtefall."}]
    res = lp.run_class_c(
        declared_docs=["d1", "d2", "d3"], processed_docs=["d1", "d2", "d3"],
        query="Härtefall?", corpus=corpus, atoms=_good_atoms(),
        pairs=[_conforming_pair()], legal_system="EU")
    assert res.must_escalate
    assert res.verdict == "ESCALATE-TO-HUMAN"


def test_pipeline_refuses_on_noncompliant_emission():
    bad = _conforming_pair(); del bad["solution"]["temporal"]      # NT-2 violation
    res = lp.run_class_c(
        declared_docs=["d1", "d2"], processed_docs=["d1", "d2"], query="q",
        corpus=CORPUS, atoms=_good_atoms(), pairs=[bad], legal_system="EU")
    assert res.verdict == "REFUSED"
    assert any(s.stage == "contract" and s.status == "blocked" for s in res.stages)
