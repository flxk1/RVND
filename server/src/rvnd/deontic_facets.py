# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND-side deontic *facet extraction* — a consumer, not a language.

The deontic **language** (formula shape, the ``O`` / ``P`` / ``F`` operator
vocabulary, incident classification, conflict detection) is owned by the
``deontic`` package and reaches RVND only through :mod:`.adapters.deontic`.
There is no ``R`` operator: a *right* is CONSTRUCTED — the operator is ``P``
(a liberty/privilege) or the correlative duty ``O`` (a claim), carried by the
Hohfeld ``incident`` the grammar assigns (:func:`classify_incident` /
:func:`correlative`), never a primitive.

This module wires that consumed grammar to RVND's kept surface reader
(:mod:`.rule_extractor`, which reads the norm across the 24 EU languages) to
produce, from raw instrument text, the ND *pair* dicts that
:mod:`.memo` / :mod:`.requirements_house` consume. It replaces the retired
``deontic.py`` (the parallel deontic core + its ``DeonticFormulaND`` nD-facet
dispatcher).

TODO(flow): per the settled flow ``versum → solver → patchbay → rvnd``, the
deontic nD facet is emitted by ingest into versum and should reach RVND as an
editable **patchbay relation**, consumed here rather than re-derived from raw
text. Until that patchbay→rvnd path exists, the ``*_from_text`` convenience
builders re-run the surface reader locally through this module. This is a
deliberate, bounded fallback — not a second deontic language.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .adapters.deontic import (
    DeonticFormula,
    classify_incident,
    detect_conflicts as _detect_conflicts_grammar,
    dimension_affinity,
    formula_from_fields,
    is_grounded,
)
from .adapters.solver.dimensions import Dimension
from .rule_extractor import RuleFacet, extract_rules

# Re-exported so consumers/tests reach the grammar's grounding predicate under
# its established RVND name without importing the package directly.
obligation_is_grounded = is_grounded

_ND_ID = "nd-deontic"


# ---------------------------------------------------------------------------
# surface read -> deontic formula (via the consumed grammar)
# ---------------------------------------------------------------------------

def formula_from_rule(rule: RuleFacet) -> DeonticFormula:
    """Lift one :class:`RuleFacet` into a :class:`DeonticFormula` using the
    consumed grammar. The modal class picks the operator (``right`` → ``P``,
    never ``R``); the Hohfeld incident is taken from the rule if the surface
    reader supplied one, otherwise classified by the grammar. An uncatalogued
    modal falls back to ``O`` and drops confidence — that rule lives in
    :func:`formula_from_fields`, not here."""
    incident = (getattr(rule, "incident", "") or "").strip()
    if not incident:
        incident = classify_incident(rule.modal, rule.action, rule.raw_sentence)
    return formula_from_fields(
        rule.modal,
        rule.subject,
        rule.action,
        condition=rule.condition,
        exception=rule.exception,
        incident=incident,
        counterparty=getattr(rule, "counterparty", "") or "",
        language=rule.language,
        raw_sentence=rule.raw_sentence,
        confidence=rule.confidence,
    )


def extract_formulae(content: str, *, gated_by_fingerprint: bool = True) -> list[DeonticFormula]:
    """Extract deontic formulae from normative content. Thin lift over
    :func:`extract_rules`: every rule the surface reader finds becomes one
    formula. Same gating contract as ``extract_rules``."""
    rules = extract_rules(content, gated_by_fingerprint=gated_by_fingerprint)
    return [formula_from_rule(r) for r in rules]


# ---------------------------------------------------------------------------
# deontic formula -> ND pair dicts
# ---------------------------------------------------------------------------

def _hash_pair(content: str, source: str | None) -> str:
    h = hashlib.sha256()
    h.update(_ND_ID.encode("utf-8"))
    h.update(b"|")
    h.update((source or "inline").encode("utf-8"))
    h.update(b"|")
    h.update(content.encode("utf-8"))
    return "sha256:" + h.hexdigest()[:32]


def _edge(subject: str, predicate: str, obj: str, dimension: Dimension) -> dict[str, Any]:
    return {"subject": subject, "predicate": predicate, "object": obj,
            "dimension": dimension.value}


def _formula_edges(pid: str, f: DeonticFormula) -> list[dict[str, Any]]:
    """Dimensioned edges for a deontic formula. The formula binds a bearer
    (intentional); when conditional, the condition triggers it (causal); an
    unconditional formula governs the bearer along the operator's own
    dimension affinity (from the grammar)."""
    edges: list[dict[str, Any]] = [
        _edge(pid, "binds", f.bearer, Dimension.INTENTIONAL),
    ]
    if f.condition:
        edges.append(_edge(pid, "applies-when", f.condition, Dimension.CAUSAL))
    else:
        edges.append(_edge(pid, "governs", f.bearer,
                           Dimension(dimension_affinity(f.operator))))
    if f.exception:
        edges.append(_edge(pid, "defeated-by", f.exception, Dimension.CAUSAL))
    return edges


def extract_deontic_pairs(content: str, *, source_document: str | None = None,
                          detect_conflicts: bool = False) -> list[dict[str, Any]]:
    """Read normative ``content`` into ``kind=deontic-formula`` ND pairs.

    Replaces the retired ``DeonticFormulaND.extract``: same pair shape (so
    ``enrich_pairs`` / ``assess`` / ``build_house`` consume it unchanged), but
    built on the consumed grammar (:mod:`.adapters.deontic`) and RVND's kept
    surface reader — no parallel deontic core, no ``R`` operator.
    """
    formulae = extract_formulae(content)
    pair_id_base = _hash_pair(content, source_document)
    out: list[dict[str, Any]] = []
    for idx, f in enumerate(formulae):
        pid = f"{pair_id_base}-d{idx}"
        fd = f.to_dict()
        out.append({
            "id": pid,
            "problem": {
                "id": f"{pid}-p",
                "kind": "deontic-formula",
                "scope": "deontic",
                "type": "mental-model",
                "summary": f"{f.operator}({f.bearer} : {f.action[:60]})",
                "facets": {
                    "operator": f.operator,
                    "operator_gloss": fd.get("operator_gloss", ""),
                    "bearer": f.bearer,
                    "incident": f.incident,
                    "conditional": bool(f.condition),
                    "defeasible": bool(f.exception),
                    "language": f.language,
                },
                "context": {"kind_of_model": "deontic-logic"},
            },
            "solution": {
                "id": pid,
                "problem_id": f"{pid}-p",
                "operator": f.operator,
                "bearer": f.bearer,
                "action": f.action,
                "condition": f.condition,
                "exception": f.exception,
                "incident": f.incident,
                "formula": fd["formula"],
                "dual": fd["dual"],
                "body": f"DEONTIC FORMULA\n{fd['formula']}\ndual: {fd['dual']}",
                "body_format": "structured-deontic",
                "authority_tier": 1,
                "confidence": f.confidence,
            },
            "edges": _formula_edges(pid, f),
        })

    if detect_conflicts:
        conflicts = _detect_conflicts_grammar(formulae)
        for cidx, c in enumerate(conflicts):
            cid = f"{pair_id_base}-c{cidx}"
            out.append({
                "id": cid,
                "problem": {
                    "id": f"{cid}-p",
                    "kind": "deontic-conflict",
                    "scope": "deontic",
                    "type": "interaction",
                    "summary": f"conflict: {c['operator_a']} vs {c['operator_b']} over {c['action'][:50]}",
                    "facets": {
                        "bearer": c["bearer"],
                        "operator_a": c["operator_a"],
                        "operator_b": c["operator_b"],
                    },
                },
                "solution": {
                    "id": cid,
                    "problem_id": f"{cid}-p",
                    "body": (f"DEONTIC CONFLICT (candidate)\n"
                             f"A: {c['formula_a']}\nB: {c['formula_b']}\n"
                             f"resolution: {c['resolution']}"),
                    "body_format": "structured-conflict",
                    "authority_tier": 2,
                    "confidence": c["confidence"],
                    "resolution": c["resolution"],
                },
                # A candidate conflict is RELATIONAL until a human resolves it.
                "edges": [_edge(cid, "may-conflict-with", c["action"], Dimension.RELATIONAL)],
            })
    return out
