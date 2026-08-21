# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""select-context: one contracts-Workspace, three projects with different required
facts → three correct, non-overlapping, provenance-tagged selections."""

from __future__ import annotations

from rvnd.hybrid_retrieval import Document
from rvnd.fact_intake import FactNeed
from rvnd.project_scope import select, ClauseNeed


# ONE Workspace: Acme's contracts + a decoy other party (Globex). Doc id is
# "<party>:<contract>:<clause>" so the party is the entity scope.
WORKSPACE = [
    Document("acme:msa:payment", "Zahlung innerhalb 30 Tagen nach Rechnung."),
    Document("acme:msa:fee", "Vergütung 10000 EUR monatlich."),
    Document("acme:msa:liability", "Haftung begrenzt auf die Vertragssumme."),
    Document("acme:nda:notice", "Kündigungsfrist drei Monate zum Vertragsende."),
    Document("globex:msa:payment", "Zahlung binnen 14 Tagen."),    # different party
]

# Standing facts for Acme (would come from the entity's SubjectCard).
ACME_CARD = {"vat_status": "reverse-charge"}


def _ids(bundle):
    return {c.doc_id for c in bundle.clauses}


def test_three_projects_select_three_different_subsets_from_one_workspace():
    invoice = select(entity="acme", legal_system="DE", corpus=WORKSPACE, card_facets=ACME_CARD,
                     fact_needs=[FactNeed("vat_status", "VAT?", scope="standing")],
                     clause_needs=[ClauseNeed("Zahlung Tagen"), ClauseNeed("Vergütung EUR")])
    compliance = select(entity="acme", legal_system="DE", corpus=WORKSPACE,
                        clause_needs=[ClauseNeed("Haftung begrenzt")])
    renewal = select(entity="acme", legal_system="DE", corpus=WORKSPACE,
                     clause_needs=[ClauseNeed("Kündigungsfrist Monate")])

    # the three projects pull different clauses from the SAME workspace
    assert _ids(invoice) == {"acme:msa:payment", "acme:msa:fee"}
    assert _ids(compliance) == {"acme:msa:liability"}
    assert _ids(renewal) == {"acme:nda:notice"}
    # and they do not overlap
    assert _ids(invoice) & _ids(compliance) == set()
    assert _ids(compliance) & _ids(renewal) == set()


def _sel(**kw):
    return select(entity="acme", legal_system="DE", corpus=WORKSPACE, **kw)


def test_clauses_are_retrieved_with_provenance_and_entity_scoped():
    b = _sel(clause_needs=[ClauseNeed("Zahlung Tagen")])
    assert b.clauses and b.clauses[0].doc_id == "acme:msa:payment"
    # the Globex payment clause is NEVER selected under entity=acme (no cross-contamination)
    assert all(not c.doc_id.startswith("globex") for c in b.clauses)


def test_standing_fact_is_filled_from_the_card_not_asked():
    b = _sel(fact_needs=[FactNeed("vat_status", "VAT?", scope="standing")],
             card_facets=ACME_CARD)
    assert b.facts["vat_status"].value == "reverse-charge"
    assert b.facts["vat_status"].source == "standing"
    assert not b.open_facts


def test_unknown_fact_becomes_an_open_question():
    b = _sel(fact_needs=[FactNeed("po_number", "Purchase order?", scope="per_case")])
    assert any(n.key == "po_number" for n in b.open_facts)
    assert not b.complete


def test_a_required_clause_not_in_the_workspace_is_flagged_missing_not_invented():
    b = _sel(clause_needs=[ClauseNeed("Schiedsgerichtsklausel")])   # no arbitration clause exists
    assert "Schiedsgerichtsklausel" in b.missing_clauses
    assert not b.complete


def test_complete_when_all_facts_known_and_clauses_found():
    b = _sel(fact_needs=[FactNeed("vat_status", "VAT?", scope="standing")],
             card_facets=ACME_CARD,
             clause_needs=[ClauseNeed("Zahlung Tagen")])
    assert b.complete
