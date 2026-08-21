# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""select-context as a workflow step — deterministic, and recorded to the log.

The architecture answer was: selecting which facts a project needs is a *workflow
step*, not the agent improvising. This is that step. It runs ``project_scope.select``
over the Workspace and **records the selected bundle (with provenance) as a signed
workflow-event** on the folder's mutation log — so the selection is reproducible
and auditable (you can see exactly which clauses/facts were chosen, from which
contract, on which run), never a silent model choice.

The workflow runner invokes this for a ``select-context`` step before the action
steps; downstream steps consume the returned bundle.

Pure + the existing mutation log; deterministic; no cloud calls.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Optional

from .project_scope import select, ClauseNeed
from .fact_intake import FactNeed
from .hybrid_retrieval import Document
from .mutation_log import MutationLog, LogEvent


def _needs(rows) -> list[FactNeed]:
    return [FactNeed(r["key"], r.get("prompt", ""), r.get("scope", "standing"),
                     bool(r.get("required", True))) for r in (rows or [])]


def _clauses(rows) -> list[ClauseNeed]:
    out = []
    for r in (rows or []):
        out.append(ClauseNeed(r) if isinstance(r, str)
                   else ClauseNeed(r["query"], bool(r.get("required", True))))
    return out


def _docs(rows) -> list[Document]:
    return [Document(r["id"], r.get("text", ""), celex=r.get("celex"),
                     authority_tier=int(r.get("authority_tier", 3))) for r in (rows or [])]


def run_select_context_step(folder: str | Path, step_params: dict[str, Any], *,
                            log_root: Optional[str | Path] = None,
                            run_id: str = "", step_index: int = 0,
                            actor: str = "agent:select-context") -> dict[str, Any]:
    """Execute one select-context step and record it.

    step_params: ``{entity, fact_needs?, clause_needs?, card_facets?, per_case_data?,
        corpus?, legal_system?, as_of?}`` (the project/workflow's requirement set).

    Returns ``{bundle, audit_id, complete}``. ``audit_id`` is the signed
    mutation-log entry — the proof the selection happened and what it chose.
    """
    sp = step_params or {}
    bundle = select(
        entity=sp["entity"], fact_needs=_needs(sp.get("fact_needs")),
        clause_needs=_clauses(sp.get("clause_needs")),
        card_facets=sp.get("card_facets"), per_case_data=sp.get("per_case_data"),
        corpus=_docs(sp.get("corpus")), legal_system=sp.get("legal_system", "DE"),
        as_of=date.fromisoformat(sp["as_of"]) if sp.get("as_of") else None)

    log = MutationLog(Path(folder), log_root=Path(log_root) if log_root else None)
    audit_id = log.append(LogEvent(
        event="extract", folder_path=str(folder),
        pair_id=f"select-context:{run_id}:{step_index}",
        channel="reasoning", actor=actor,
        extra={"kind": "select-context", "entity": bundle.entity,
               "selected_clauses": [c.doc_id for c in bundle.clauses],
               "facts": {k: v.source for k, v in bundle.facts.items()},
               "complete": bundle.complete,
               "open_facts": [n.key for n in bundle.open_facts],
               "missing_clauses": list(bundle.missing_clauses)}))
    return {"bundle": bundle.to_dict(), "audit_id": audit_id, "complete": bundle.complete}
