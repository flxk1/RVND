# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""select-context — the substrate mechanism behind a workflow's context step.

A project/workflow declares *what it needs*; this resolves that requirement set
against a Workspace and returns exactly the scoped subset — deterministically, with
provenance on every item. It is the machinery a `tool`-kind workflow step calls;
it is NOT the agent improvising, and it is domain-agnostic (a Workspace thing, not a
companion thing): the companion only configures *which* Workspace and the dials.

A requirement set has two kinds of need:

  * **fact needs** — key facts (party VAT status, role). Resolved from the
    entity's standing facts (its SubjectCard) or this run's data
    (``fact_intake``). What isn't known becomes the minimal form to ask.
  * **clause needs** — things that live in the Workspace's *documents* (the fee
    clause, the notice period). Resolved by entity-scoped relevance retrieval
    (``hybrid_retrieval``), each returned with the document id it came from.

Three projects over one contracts-Workspace declare three requirement sets → three
different bundles from the same store. Every selected item carries where it came
from, so a fact from contract X can never silently be used as contract Y's.

Pure stdlib + workspaces internals. Deterministic; no cloud calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional

from .fact_intake import FactNeed, build_form
from .hybrid_retrieval import Document, HybridIndex
from . import currency as _cur


@dataclass
class ClauseNeed:
    query: str
    required: bool = True


@dataclass
class SelectedFact:
    value: Any
    source: str                  # "standing" | "this-run"


@dataclass
class SelectedClause:
    query: str
    doc_id: str
    score: float


@dataclass
class ContextBundle:
    entity: str
    facts: dict[str, SelectedFact] = field(default_factory=dict)
    clauses: list[SelectedClause] = field(default_factory=list)
    open_facts: list[FactNeed] = field(default_factory=list)      # facts still to ask
    missing_clauses: list[str] = field(default_factory=list)       # required clauses not found

    @property
    def complete(self) -> bool:
        """The workflow may proceed without asking when no required fact or clause
        is still open."""
        return not any(n.required for n in self.open_facts) and not self.missing_clauses

    def to_dict(self) -> dict[str, Any]:
        return {"entity": self.entity, "complete": self.complete,
                "facts": {k: {"value": v.value, "source": v.source} for k, v in self.facts.items()},
                "clauses": [{"query": c.query, "doc_id": c.doc_id, "score": c.score} for c in self.clauses],
                "open_facts": [n.key for n in self.open_facts],
                "missing_clauses": list(self.missing_clauses)}


def _entity_of(doc: Document) -> str:
    """Entity scope key: the part of the doc id before the first ':' (e.g.
    'acme:msa:fee' → 'acme'). Keeps a Workspace's contracts addressable per party."""
    return doc.id.split(":", 1)[0]


def select(*, entity: str,
           fact_needs: Iterable[FactNeed] = (),
           clause_needs: Iterable[ClauseNeed] = (),
           card_facets: Optional[dict] = None,
           per_case_data: Optional[dict] = None,
           corpus: Optional[Iterable[Document]] = None,
           legal_system: str = "DE",
           as_of: Optional[date] = None,
           registry: Optional[_cur.CurrencyRegistry] = None) -> ContextBundle:
    """Resolve a requirement set against the Workspace, scoped to ``entity``."""
    fact_needs = list(fact_needs)
    clause_needs = list(clause_needs)
    bundle = ContextBundle(entity=entity)

    # 1. Facts — from the entity's standing card, or this run's data; the rest open.
    form = build_form(fact_needs, standing=card_facets or {}, per_case_data=per_case_data or {})
    for key, val in form.prefilled.items():
        bundle.facts[key] = SelectedFact(val, form.provenance.get(key, "standing"))
    bundle.open_facts = list(form.questions)

    # 2. Clauses — entity-scoped relevance retrieval over the Workspace's documents.
    docs = [d for d in (corpus or []) if _entity_of(d) == entity]
    index = HybridIndex(docs) if docs else None
    for cn in clause_needs:
        hits = index.retrieve(cn.query, k=1, legal_system=legal_system,
                              as_of=as_of, registry=registry) if index else []
        if hits:
            bundle.clauses.append(SelectedClause(cn.query, hits[0].doc.id, round(hits[0].score, 3)))
        elif cn.required:
            bundle.missing_clauses.append(cn.query)
    return bundle
