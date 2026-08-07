# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""End-to-end legal stress test — does a Workspace retrieve the right law at the
right time, and does it hold up against the legal-reasoning limits?

Each ``Aspect`` class maps 1:1 to a section of the source prompt:

  Aspect 1  Temporal model (ratione temporis) — right Fassung at a given date.
  Aspect 2  Top-k != completeness — exceptions, discretion, empty-room gaps.
  Aspect 3  Distributed evidence & multi-hop — composed inference, conflict,
            authority — surfaced, not smoothed.
  Aspect 4  Query-document mismatch — dense/hybrid retrieval (NOT YET BUILT:
            the one honest strict-xfail).
  Aspect 5  Attention dilution — the decisive clause in a long document is
            extracted, not averaged away.
  Aspect 6  Governance floor — the norm-theory contract gates the whole thing.

The tests drive the real runtime modules (currency, reasoning, deontic,
requirements_house/evidence_coverage, dimensions, norm_contract). No mocks of
the logic under test. A green run (with exactly one xfail) is the evidence.
"""

from __future__ import annotations

from datetime import date

import pytest

import workspaces.currency as cur
from workspaces.dimensions import Dimension, classify_query_dimension
from workspaces.reasoning import extract_edges, compose_paths
from workspaces.adapters.deontic import (
    DeonticFormula, OP_OBLIGATION, OP_PROHIBITION, detect_conflicts,
)
from workspaces.deontic_facets import extract_formulae
from workspaces.requirements_house import build_house_from_text
from workspaces.evidence_coverage import EvidenceDoc, map_coverage
from workspaces.norm_contract import gate, check_pair, Level, ContractViolation


# ───────────────────────── shared scenario corpus ──────────────────────────
# A small but real legal landscape: Directive 95/46 superseded by the GDPR from
# 2018-05-25, the GDPR itself, and the AI Act. The supersession is the crux of
# "right law at the right time".

REG = cur.CurrencyRegistry.from_rows([
    {"celex": "31995L0046", "in_force_from": "1995-12-13",
     "superseded_by": "32016R0679", "superseded_from": "2018-05-25"},
    {"celex": "32016R0679", "in_force_from": "2018-05-25",
     "consolidation_version": "2016-05-04"},
    {"celex": "32024R1689", "in_force_from": "2026-08-02"},          # AI Act, future
])


def _pair(pid, *, cited, edges=None, body="", facets=None, solution=None):
    p = {
        "id": pid,
        "problem": {"id": f"{pid}-p", "type": "rule",
                    "facets": facets or {}},
        "solution": {"id": pid, "problem_id": f"{pid}-p", "cited_sources": cited,
                     "body": body, "authority_tier": 1, "confidence": 0.9},
        "edges": edges or [],
    }
    if solution:
        p["solution"].update(solution)
    return p


def _edge(s, p, o, dim):
    return {"subject": s, "predicate": p, "object": o, "dimension": dim.value}


# ───────────────────────────── Aspect 1: time ──────────────────────────────

class TestAspect1Temporal:
    """Right law at the right time — the registry decides, never the model."""

    def test_old_directive_governs_a_2017_case_gdpr_not_yet(self):
        asof = date(2017, 6, 1)
        assert cur.validity_status(REG.get("31995L0046"), asof) == "in-force"
        assert cur.validity_status(REG.get("32016R0679"), asof) == "not-yet-in-force"

    def test_gdpr_governs_a_2024_case_directive_superseded(self):
        asof = date(2024, 6, 1)
        assert cur.validity_status(REG.get("32016R0679"), asof) == "in-force"
        assert cur.validity_status(REG.get("31995L0046"), asof) == "superseded"

    def test_point_in_time_selection_flips_between_the_two_dates(self):
        """The in-force set the retrieval layer MUST scope to is different in
        2017 vs 2024 — anachronism is impossible if you scope by as_of."""
        def in_force(asof):
            return {c for c in ("31995L0046", "32016R0679", "32024R1689")
                    if cur.validity_status(REG.get(c), asof) == "in-force"}
        assert in_force(date(2017, 6, 1)) == {"31995L0046"}
        assert in_force(date(2024, 6, 1)) == {"32016R0679"}

    def test_a_date_is_never_guessed(self):
        p = _pair("x", cited=["CELEX:39999R0000 Art.1"])   # not in registry
        cur.attach_validity(p, REG, as_of=date(2024, 1, 1))
        assert p["solution"]["validity"]["status"] == "unknown"
        assert p["solution"]["validity"]["in_force_from"] is None

    def test_refresh_reopens_assessments_resting_on_now_stale_law(self):
        """A finding written under 95/46 must resurface once the GDPR supersedes
        it — refresh is a graph query over the registry, not a re-read."""
        finding = _pair("finding-2016", cited=["CELEX:31995L0046 Art.7"])
        dependent = _edge("conclusion", "rests-on", "finding-2016", Dimension.STRUCTURAL)
        res = cur.refresh([finding], REG, as_of=date(2024, 1, 1), edges=[dependent])
        assert "finding-2016" in res.superseded
        assert dependent in res.affected_edges
        assert res.needs_review


# ─────────────────────────── Aspect 2: completeness ────────────────────────

AI_ACT_TEXT = (
    "Providers of high-risk AI systems shall establish a risk management system. "
    "Providers of high-risk AI systems shall draw up the technical documentation. "
    "Where the high-risk AI system is used for employment, the deployer shall carry "
    "out a data protection impact assessment. Deployers shall designate a data "
    "protection officer."
)


class TestAspect2Completeness:
    """Top-k is a priorisation tool, not a vollständigkeit tool. The house makes
    every required room explicit and surfaces the empty ones as honest gaps."""

    def test_empty_rooms_are_surfaced_not_smoothed(self):
        house = build_house_from_text(AI_ACT_TEXT, "ai-act", title="AI Act high-risk")
        docs = [EvidenceDoc("d1", "DPIA", "Our data protection impact assessment ...")]
        rep = map_coverage(house, docs)
        empty = {e["room_id"] for e in rep.empty}
        # technical-documentation / risk-management have no evidence → visible gaps
        assert empty, "completeness instrument must surface empty rooms"
        assert 0.0 <= rep.coverage_ratio < 1.0

    def test_discretion_is_escalated_never_decided(self):
        """kann / Härtefall ⇒ the contract refuses to let the system decide."""
        p = _conforming_rule_pair()
        p["problem"]["facets"]["modal"] = "kann"
        p["problem"]["facets"]["modal_phrase"] = "kann abgesehen werden"
        rep = check_pair(p, risk_class="C")
        assert rep.ok  # well-formed
        assert any(f.code == "NT-4" and f.level is Level.ESCALATE for f in rep.escalations)

    def test_a_buried_exception_cannot_be_silently_absorbed(self):
        p = _conforming_rule_pair()
        p["solution"]["body"] = "Die Behörde fordert zurück, es sei denn die Einziehung wäre unbillig."
        p["problem"]["facets"]["has_exception"] = False     # the failure the essay warns about
        rep = check_pair(p)
        assert any(f.code == "NT-5" and f.level is Level.VIOLATION for f in rep.violations)


# ─────────────────────── Aspect 3: multi-hop / conflict ─────────────────────

class TestAspect3MultiHop:
    """Distributed evidence composed into an auditable chain; conflicts and
    authority surfaced rather than smoothed into one confident answer."""

    def test_norm_to_case_to_concept_composes_with_provenance(self):
        pairs = [
            _pair("norm", cited=["§ X"], edges=[_edge("Norm", "interpreted-by", "Urteil", Dimension.RELATIONAL)]),
            _pair("case", cited=["Urteil C"], edges=[_edge("Urteil", "narrows", "Begriff", Dimension.RELATIONAL)]),
        ]
        infs = compose_paths(extract_edges(pairs))
        chain = [i for i in infs if (i.subject, i.object) == ("Norm", "Begriff")]
        assert chain, "two-hop Norm→Urteil→Begriff inference must be derived"
        i = chain[0]
        assert i.hops == 2
        assert i.confidence < 0.9              # weaker chain is less certain
        assert [h["source_pair"] for h in i.path] == ["norm", "case"]   # provenance

    def test_genuine_conflict_is_flagged_for_escalation(self):
        formulae = [
            DeonticFormula(operator=OP_OBLIGATION, bearer="provider", action="disclose the data", confidence=0.9),
            DeonticFormula(operator=OP_PROHIBITION, bearer="provider", action="disclose the data", confidence=0.8),
        ]
        conflicts = detect_conflicts(formulae)
        assert len(conflicts) == 1
        # the consumed deontic grammar flags, never resolves — escalate to a human
        assert conflicts[0]["resolution"] == "candidate-escalate"

    def test_a_conflict_may_not_carry_an_auto_resolved_winner(self):
        p = _conforming_rule_pair()
        p["solution"]["predicate"] = "may-conflict-with"
        p["solution"]["resolution"] = "a-overrides-b"      # forbidden: lex-* not auto-derived
        rep = check_pair(p)
        assert any(f.code == "NT-6" and f.level is Level.VIOLATION for f in rep.violations)


# ─────────────────────── Aspect 4: query-document mismatch ──────────────────

class TestAspect4Mismatch:
    """The query is phrased unlike the norm. Built 2026-06-02: the hybrid retriever
    (BM25 + legal query expansion + concept coverage) bridges 'verzichten'↔'abgesehen'
    and 'Härtefall'↔'unbillig' and retrieves the GOVERNING norm where plain keyword
    overlap retrieves a lexically-similar decoy. Full experiment + numbers:
    test_hybrid_retrieval.py (baseline einschlägigkeit@1=0.00, hybrid=1.00)."""

    def test_query_intent_is_classified(self):
        assert classify_query_dimension("why does the obligation arise") == Dimension.CAUSAL

    def test_layperson_query_retrieves_the_differently_worded_norm(self):
        from workspaces.hybrid_retrieval import Document, HybridIndex, baseline_retrieve
        corpus = [
            Document("GOV", "Von der Einziehung kann abgesehen werden, soweit sie "
                     "nach Lage des Einzelfalls unbillig wäre.", authority_tier=1),
            Document("DECOY", "Das Amt betreibt die Einziehung der Forderung und "
                     "überwacht die fristgerechte Zahlung.", authority_tier=1),
        ]
        query = "Darf das Amt trotz Härtefall auf die Einziehung verzichten?"
        hybrid = HybridIndex(corpus).retrieve(query, k=2)
        baseline = baseline_retrieve(query, corpus, k=2)
        assert hybrid[0].doc.id == "GOV"          # hybrid finds the governing norm …
        assert baseline[0].doc.id != "GOV"        # … keyword overlap does not.


# ───────────────────────── Aspect 5: attention dilution ─────────────────────

class TestAspect5Dilution:
    """A long document whose outcome turns on one 'unless' clause. The answer is
    issue-spotting over extracted atoms, so the decisive clause is isolated
    rather than averaged into the bulk.

    Stress finding (2026-06-02): the first run exposed that the extractor missed
    German periphrastic obligation ('ist verpflichtet, … zu') because of the comma,
    and misread 'darf … nicht' as an obligation. Both were fixed in rule_extractor
    (commit of 2026-06-02); the tests below now pass and stand as regressions.
    Defense in depth still holds independently: even if extraction missed the
    clause, the norm-theory contract refuses to let it pass."""

    LONG_EN = (
        "Background. " + ("The parties dispute the reclaim. " * 200)
        + "The provider shall disclose the data unless disclosure would be unreasonable. "
        + ("See the file for further detail. " * 200)
    )
    BURIED_DE = (
        "Tatbestand. " + ("Die Parteien streiten über die Rückforderung. " * 50)
        + "Der Anbieter ist verpflichtet, die Daten offenzulegen, es sei denn die "
          "Offenlegung wäre im Einzelfall unbillig. "
        + ("Wegen der weiteren Einzelheiten wird auf die Akten verwiesen. " * 50)
    )

    def test_length_does_not_dilute_the_decisive_clause_english(self):
        """Long vs short: the operative norm is still extracted from the bulk."""
        assert extract_formulae(self.LONG_EN), "issue-spotting must survive document length"

    def test_german_decisive_clause_is_extracted(self):
        """Regression: periphrastic 'ist verpflichtet, … zu' (comma + zu-infinitive)
        must be extracted from a long German document. Was the original xfail."""
        assert extract_formulae(self.BURIED_DE), "German operative norm should be extracted"

    def test_german_prohibition_not_misread_as_obligation(self):
        """Regression for 'falsche Rechtsfolge': separated negation 'darf … nicht'
        is a prohibition, not an obligation (the essay's most dangerous error)."""
        from workspaces.rule_extractor import extract_rules
        r = extract_rules("Der Anbieter darf die Daten nicht offenlegen.",
                          gated_by_fingerprint=False)
        assert r and r[0].modal == "prohibition"

    def test_contract_catches_the_buried_clause_even_when_extraction_misses_it(self):
        """Defense in depth: extraction may miss the German clause, but the contract
        still refuses a pair whose body carries an unflagged exception."""
        p = _conforming_rule_pair()
        p["solution"]["body"] = self.BURIED_DE         # decisive clause buried inside
        p["problem"]["facets"]["has_exception"] = False
        rep = check_pair(p)
        assert any(f.code == "NT-5" and f.level is Level.VIOLATION for f in rep.violations), \
            "the buried 'es sei denn' must not pass unflagged"


# ───────────────────────── Aspect 6: governance floor ───────────────────────

class TestAspect6ContractFloor:
    """Nothing ships unless the norm-theory contract passes; today's bare ND
    output is rejected, proving the floor bites."""

    def test_conforming_pair_clears_the_gate(self):
        rep = gate([_conforming_rule_pair()], risk_class="C")
        assert rep.ok

    def test_bare_pair_is_rejected_by_the_gate(self):
        bare = _pair("bare", cited=[], body="Der Anbieter muss die Daten offenlegen.")
        # no source, no temporal, no applicability, no jurisdiction
        with pytest.raises(ContractViolation):
            gate([bare])

    def test_every_norm_theory_invariant_has_teeth(self):
        rep = check_pair(_conforming_rule_pair(), risk_class="C")
        codes = {f.code for f in rep.findings}
        assert {"NT-1","NT-2","NT-3","NT-4","NT-5","NT-6","NT-7","NT-8","NT-9"} <= codes


# ───────────────────────────── fixtures/helpers ────────────────────────────

def _conforming_rule_pair() -> dict:
    """A fully norm-theory-conforming pair (mirrors test_norm_contract)."""
    return {
        "id": "ok",
        "problem": {"id": "ok-p", "type": "rule", "facets": {
            "domain": "ai-act", "subject": "provider", "modal": "muss",
            "modal_phrase": "muss sicherstellen", "has_exception": False,
            "applicability": {"role": "provider", "risk_tier": "high"},
            "jurisdiction": ["EU"]}},
        "solution": {"id": "ok", "problem_id": "ok-p",
            "body": "Der Anbieter muss ein Risikomanagementsystem einrichten.",
            "authority_tier": 1, "confidence": 0.93,
            "source": "CELEX:32024R1689 Art. 9",
            "temporal": {"status": "in-force", "in_force_from": "2026-08-02", "date_source": "registry"}},
        "edges": [],
    }
