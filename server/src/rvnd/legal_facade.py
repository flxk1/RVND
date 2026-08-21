# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""workspace_legal — ONE MCP tool that bundles the legal capabilities.

The Workspace MCP surface had grown to ~100 fine-grained tools. That overwhelms the
model's tool-selection and bloats context. The fix is facade tools: group related
operations behind a single tool with an ``op`` enum and typed params, instead of
one tool per function.

This is the facade for the legal workflow pieces (card, fact-intake, select-
context, the class-C pipeline, subsumption validation). It is pure Python and
dict-in/dict-out, so it is unit-testable without the `mcp` package. The actual
MCP wrapper is then a one-liner:

    @mcp.tool()
    def workspace_legal(op: str, params: dict | None = None) -> dict:
        return workspace_legal_op(op, params or {})

Eight operations, one tool. ``ops_catalogue()`` self-documents them for the tool
description, which mitigates the usual downside of an op-dispatched tool (the
model not knowing the sub-operation vocabulary).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Optional

from .subject_card import SubjectCard
from . import card_store as _cards
from .fact_intake import FactNeed, build_form, record_standing
from .hybrid_retrieval import Document
from . import project_scope as _scope
from . import subsumption_path as _sub
from . import subsumption_validator as _val
from . import legal_pipeline as _pipe


# --- param adapters (dict → typed objects) -----------------------------------

def _fact_needs(rows) -> list[FactNeed]:
    return [FactNeed(r["key"], r.get("prompt", ""), r.get("scope", "standing"),
                     bool(r.get("required", True))) for r in (rows or [])]


def _clause_needs(rows) -> list[_scope.ClauseNeed]:
    out = []
    for r in (rows or []):
        if isinstance(r, str):
            out.append(_scope.ClauseNeed(r))
        else:
            out.append(_scope.ClauseNeed(r["query"], bool(r.get("required", True))))
    return out


def _docs(rows) -> list[Document]:
    return [Document(r["id"], r.get("text", ""), celex=r.get("celex"),
                     authority_tier=int(r.get("authority_tier", 3))) for r in (rows or [])]


def _as_of(s) -> Optional[date]:
    return date.fromisoformat(s) if s else None


# --- operation handlers ------------------------------------------------------

def _op_card_save(p: dict) -> dict:
    c = p["card"]
    card = SubjectCard(domain=c.get("domain", ""), facets=dict(c.get("facets") or {}),
                       subject_id=c.get("subject_id", ""), description=c.get("description", ""),
                       notes=c.get("notes", ""), contact=c.get("contact", ""))
    return _cards.save_card(card, p["folder_context"], log_root=p.get("log_root"),
                            actor=p.get("actor", "user"))


def _op_card_load(p: dict) -> dict:
    card = _cards.load_card(p["folder_context"], p["subject_id"])
    return {"found": card is not None, "card": card.to_dict() if card else None}


def _op_card_list(p: dict) -> dict:
    return {"cards": _cards.list_cards(p["folder_context"])}


def _op_facts_form(p: dict) -> dict:
    return build_form(_fact_needs(p.get("needs")),
                      standing=p.get("standing") or {},
                      per_case_data=p.get("per_case_data") or {}).to_dict()


def _op_facts_record(p: dict) -> dict:
    return {"standing": record_standing(_fact_needs(p.get("needs")),
                                        p.get("answers") or {}, p.get("standing") or {})}


def _op_select_context(p: dict) -> dict:
    return _scope.select(
        entity=p["entity"], fact_needs=_fact_needs(p.get("fact_needs")),
        clause_needs=_clause_needs(p.get("clause_needs")),
        card_facets=p.get("card_facets"), per_case_data=p.get("per_case_data"),
        corpus=_docs(p.get("corpus")), legal_system=p.get("legal_system", "DE"),
        as_of=_as_of(p.get("as_of"))).to_dict()


def _op_subsumption_validate(p: dict) -> dict:
    sub = _sub.build(p.get("atoms") or [], edges=p.get("edges"), conflicts=p.get("conflicts"))
    rep = _val.validate(sub, legal_system=p.get("legal_system", "DE"))
    return {"subsumption": sub.to_dict(), "validation": rep.to_dict()}


def _op_select_context_step(p: dict) -> dict:
    from . import workflow_select as _ws
    return _ws.run_select_context_step(
        p["folder_context"], p["step_params"], log_root=p.get("log_root"),
        run_id=p.get("run_id", ""), step_index=p.get("step_index", 0))


def _op_pipeline_run(p: dict) -> dict:
    return _pipe.run_class_c(
        declared_docs=p.get("declared_docs", []), processed_docs=p.get("processed_docs", []),
        query=p.get("query", ""), corpus=p.get("corpus", []),
        atoms=p.get("atoms", []), pairs=p.get("pairs", []),
        skipped=p.get("skipped"), edges=p.get("edges"), conflicts=p.get("conflicts"),
        legal_system=p.get("legal_system", "DE"), risk_class=p.get("risk_class", "C")).to_dict()


# op id -> (handler, required params, one-line doc)
_OPS: dict[str, tuple[Callable[[dict], dict], tuple[str, ...], str]] = {
    "card.save":            (_op_card_save, ("card", "folder_context"), "persist a SubjectCard + signed audit event"),
    "card.load":            (_op_card_load, ("folder_context", "subject_id"), "reload an entity's standing facts"),
    "card.list":            (_op_card_list, ("folder_context",), "list entities with cards in a folder"),
    "facts.form":           (_op_facts_form, ("needs",), "minimal delta form: ask only the unknowns"),
    "facts.record":         (_op_facts_record, ("needs", "answers"), "persist standing answers (reused next run)"),
    "select.context":       (_op_select_context, ("entity",), "select the scoped fact/clause subset for a task"),
    "select.context_step":  (_op_select_context_step, ("folder_context", "step_params"), "run select-context as a logged workflow step (records the bundle)"),
    "subsumption.validate": (_op_subsumption_validate, ("atoms",), "build + validate the subsumption chain (universal+regional)"),
    "pipeline.run_class_c": (_op_pipeline_run, (), "run the class-C certify/escalate/refuse pipeline"),
}


def ops_catalogue() -> list[dict[str, Any]]:
    """Self-describing op list for the MCP tool description."""
    return [{"op": k, "required": list(req), "doc": doc} for k, (_, req, doc) in _OPS.items()]


def workspace_legal_op(op: str, params: Optional[dict] = None) -> dict:
    """Dispatch one operation. Unknown op or missing required param → an error
    dict (never an exception across the MCP boundary)."""
    params = params or {}
    entry = _OPS.get(op)
    if entry is None:
        return {"error": f"unknown op {op!r}", "valid_ops": sorted(_OPS)}
    handler, required, _doc = entry
    missing = [r for r in required if r not in params]
    if missing:
        return {"error": f"op {op!r} missing params: {missing}", "required": list(required)}
    return handler(params)
