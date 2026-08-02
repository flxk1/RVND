# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Deontic-logic formalisation of normative rules.

The rule extractor (:mod:`.rule_extractor`) reads the *surface* of a norm —
subject, modal phrase, action, condition, exception — across the 24 EU
languages. This module lifts that surface read into a formal **deontic
formula** in pragmatic Standard Deontic Logic (SDL):

    operator ∈ { O, P, F, R }   (Obligation, Permission, Prohibition, Right)
    bearer                       — the norm-addressee (who the duty binds)
    action                       — the proposition the operator scopes
    condition                    — applicability antecedent (→ a conditional norm)
    exception                    — a defeasibility carve-out

A formula is rendered both as structured fields (so the cloud LLM sees the
operator and bearer in lock-scrubbed safe-context triples) and as a
readable one-line string, e.g.::

    if [processing is carried out] then O(controller : implement TOMs)  unless [Art.11]

Design constraints (match the concept's "ship mechanism, not judgment"):

- **No solver, no theorem-proving.** SDL has well-known paradoxes (Ross,
  the Good Samaritan, contrary-to-duty) that any *automated* inference would
  trip over. We do not infer; we *transcribe* the norm into a typed formula
  a lawyer can audit. The formula is a candidate, gated downstream by the
  confidence floor and the oversight dial like every other extractor output.
- **Operator mapping is total and explicit.** The rule extractor's four
  ``modal`` classes map 1:1 onto SDL operators (see :data:`_MODAL_TO_OP`);
  an unknown class falls back to ``O`` (the safe legal default — read an
  ambiguous norm as a duty, surface for review) and lowers confidence.
- **Negation is detected, not asserted.** "shall not" already arrives as
  ``modal="prohibition"`` from the extractor; we additionally normalise a
  prohibition to ``F(action)`` ≡ ``O(¬action)`` so the duality is explicit
  in the structured fields without us *deriving* anything.

The ND :class:`DeonticFormulaND` wraps this so the formula lands as typed,
dimensioned pairs in folder memory alongside the plain rule pairs. The two
are complementary: the rule pair carries the prose; the deontic pair carries
the logic. They cross-reference by ``raw_sentence``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any

from workspaces.adapters.solver.dimensions import Dimension
from .nd_routing import BaseNDDispatcher, Classification
from .rule_extractor import RuleFacet, extract_rules


# ---------------------------------------------------------------------------
# SDL operator model
# ---------------------------------------------------------------------------

# The four deontic operators we transcribe to. Strings are stable identifiers
# used in the pair facets and the audit log — do not rename without a
# migration.
OP_OBLIGATION = "O"    # it is obligatory that …
OP_PERMISSION = "P"    # it is permitted that …
OP_PROHIBITION = "F"   # it is forbidden that …  (F ≡ O¬)
OP_RIGHT = "R"         # the bearer holds a (claim/liberty) right to …

VALID_OPERATORS = (OP_OBLIGATION, OP_PERMISSION, OP_PROHIBITION, OP_RIGHT)

# The rule extractor's modal classes → SDL operator. Total over the four
# classes the extractor produces; anything else falls back to O (safe legal
# default) and the formula's confidence is reduced.
_MODAL_TO_OP: dict[str, str] = {
    "obligation": OP_OBLIGATION,
    "permission": OP_PERMISSION,
    "prohibition": OP_PROHIBITION,
    "right": OP_RIGHT,
}

# Human-readable gloss per operator, for the rendered string + UI.
_OP_GLOSS: dict[str, str] = {
    OP_OBLIGATION: "obligatory",
    OP_PERMISSION: "permitted",
    OP_PROHIBITION: "forbidden",
    OP_RIGHT: "right",
}

# Each operator's natural reasoning dimension on the KG edge it produces:
#   O / F  — a duty/prohibition is *caused* (triggered) by its condition.
#   P / R  — a permission/right exists *for* the bearer's benefit (purpose).
# When there is no condition, an obligation still *governs* the bearer
# (intentional). These are the dimensions the deontic ND assigns because it
# knows what its edges mean (cf. domain_nds._rule_edges).
_OP_PRIMARY_DIM: dict[str, Dimension] = {
    OP_OBLIGATION: Dimension.CAUSAL,
    OP_PROHIBITION: Dimension.CAUSAL,
    OP_PERMISSION: Dimension.INTENTIONAL,
    OP_RIGHT: Dimension.INTENTIONAL,
}


@dataclass
class DeonticFormula:
    """A norm transcribed into pragmatic Standard Deontic Logic.

    ``operator`` is one of :data:`VALID_OPERATORS`. ``negated`` is True when
    the operator scopes a negative proposition (a prohibition is stored as
    ``operator=F, negated=False`` *and* exposes its ``O¬`` dual in
    :meth:`dual`; a "may not" that the extractor already classed as
    prohibition is the canonical case).
    """

    operator: str
    bearer: str
    action: str
    condition: str = ""
    exception: str = ""
    negated: bool = False
    language: str = "en"
    raw_sentence: str = ""
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.operator not in VALID_OPERATORS:
            # Safe legal default: read an uncatalogued modal as an obligation
            # and let the confidence floor / oversight surface it.
            self.operator = OP_OBLIGATION

    # -- duality ---------------------------------------------------------
    def dual(self) -> str:
        """The SDL identity that makes the operator's meaning explicit.

        F(a) ≡ O(¬a) ; P(a) ≡ ¬O(¬a). Returned as a readable string only —
        we expose the duality, we do not *infer* with it.
        """
        if self.operator == OP_PROHIBITION:
            return f"O(¬ {self.action})"
        if self.operator == OP_PERMISSION:
            return f"¬O(¬ {self.action})"
        if self.operator == OP_OBLIGATION:
            return f"¬P(¬ {self.action})"
        return ""  # rights have no single canonical deontic dual

    # -- rendering -------------------------------------------------------
    def core(self) -> str:
        """The operator applied to (bearer : action), without context."""
        act = f"¬ {self.action}" if self.negated else self.action
        return f"{self.operator}({self.bearer} : {act})"

    def render(self) -> str:
        """One-line human-readable formula with condition + exception."""
        s = self.core()
        if self.condition:
            s = f"if [{self.condition}] then {s}"
        if self.exception:
            s = f"{s} unless [{self.exception}]"
        return s

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["formula"] = self.render()
        d["dual"] = self.dual()
        d["operator_gloss"] = _OP_GLOSS.get(self.operator, "")
        d["conditional"] = bool(self.condition)
        d["defeasible"] = bool(self.exception)
        return d


def formula_from_rule(rule: RuleFacet) -> DeonticFormula:
    """Lift one :class:`RuleFacet` into a :class:`DeonticFormula`.

    The operator comes from the rule's modal class; confidence inherits the
    rule's confidence, reduced by 0.1 when the modal class was not one of the
    four catalogued ones (an uncatalogued class means the operator is a
    fallback, not a read).
    """
    op = _MODAL_TO_OP.get(rule.modal)
    confidence = rule.confidence
    if op is None:
        op = OP_OBLIGATION
        confidence = max(0.0, confidence - 0.1)
    return DeonticFormula(
        operator=op,
        bearer=rule.subject or "(unspecified)",
        action=rule.action or "(unspecified)",
        condition=rule.condition,
        exception=rule.exception,
        negated=False,  # prohibition is carried by operator=F, not by negating action
        language=rule.language,
        raw_sentence=rule.raw_sentence,
        confidence=round(confidence, 3),
    )


def obligation_is_grounded(f: "DeonticFormula") -> bool:
    """No span, no obligation. A formula is a *grounded* obligation only if it
    cites its source sentence (``raw_sentence``) AND names a concrete bearer and
    action — not the ``"(unspecified)"`` fallback. Ungrounded formulas should be
    routed through oversight (``governance.decide_output``), not emitted as duties.
    The legal-ND analogue of the Grounder's no-citation-no-claim."""
    return (bool((f.raw_sentence or "").strip())
            and f.bearer not in ("", "(unspecified)")
            and f.action not in ("", "(unspecified)"))


def extract_formulae(content: str, *, gated_by_fingerprint: bool = True) -> list[DeonticFormula]:
    """Extract deontic formulae from normative content.

    Thin lift over :func:`extract_rules`: every rule the extractor finds
    becomes one formula. Same gating contract as ``extract_rules``.
    """
    rules = extract_rules(content, gated_by_fingerprint=gated_by_fingerprint)
    return [formula_from_rule(r) for r in rules]


# ---------------------------------------------------------------------------
# Conflict detection (optional, candidate-only)
# ---------------------------------------------------------------------------

def detect_conflicts(formulae: list[DeonticFormula]) -> list[dict[str, Any]]:
    """Flag *candidate* normative conflicts within one document.

    A conflict candidate is the classic SDL clash: the same bearer + action
    bound by an obligation/prohibition on one side and a permission/right on
    the other (O(a) vs P(¬a) surfaces as F-vs-P over the same action; we
    detect the same-action operator clash and let a human resolve it).

    This *flags*; it never resolves — per the locked decision *ship the law,
    never the resolution*. Genuine conflicts route to the oversight queue.
    """
    out: list[dict[str, Any]] = []
    for i in range(len(formulae)):
        for j in range(i + 1, len(formulae)):
            a, b = formulae[i], formulae[j]
            if _norm_key(a.bearer) != _norm_key(b.bearer):
                continue
            if _norm_key(a.action) != _norm_key(b.action):
                continue
            if _clashes(a.operator, b.operator):
                out.append({
                    "kind": "deontic-conflict",
                    "bearer": a.bearer,
                    "action": a.action,
                    "operator_a": a.operator,
                    "operator_b": b.operator,
                    "formula_a": a.render(),
                    "formula_b": b.render(),
                    "resolution": "genuine-conflict-escalate",
                    "confidence": round(min(a.confidence, b.confidence), 3),
                })
    return out


# An obligation and a prohibition over the same action clash; so do a
# prohibition and a permission/right (you cannot be both forbidden and
# permitted/entitled to do the same thing).
_CLASH_PAIRS = {
    frozenset((OP_OBLIGATION, OP_PROHIBITION)),
    frozenset((OP_PROHIBITION, OP_PERMISSION)),
    frozenset((OP_PROHIBITION, OP_RIGHT)),
}


def _clashes(op_a: str, op_b: str) -> bool:
    if op_a == op_b:
        return False
    return frozenset((op_a, op_b)) in _CLASH_PAIRS


def _norm_key(s: str) -> str:
    return " ".join((s or "").lower().split())


# ---------------------------------------------------------------------------
# ND dispatcher
# ---------------------------------------------------------------------------

def _hash_pair(content: str, nd_id: str, source: str | None) -> str:
    h = hashlib.sha256()
    h.update(nd_id.encode("utf-8"))
    h.update(b"|")
    h.update((source or "inline").encode("utf-8"))
    h.update(b"|")
    h.update(content.encode("utf-8"))
    return "sha256:" + h.hexdigest()[:32]


def _edge(subject: str, predicate: str, obj: str, dimension: Dimension) -> dict[str, Any]:
    return {"subject": subject, "predicate": predicate, "object": obj,
            "dimension": dimension.value}


def _formula_edges(pid: str, f: DeonticFormula) -> list[dict[str, Any]]:
    """Dimensioned edges for a deontic formula.

    The formula binds a bearer — that is the norm's purpose (intentional);
    when conditional, the condition triggers it (causal); an obligation /
    prohibition with no condition still governs the bearer (the operator's
    primary dimension).
    """
    edges: list[dict[str, Any]] = [
        _edge(pid, "binds", f.bearer, Dimension.INTENTIONAL),
    ]
    if f.condition:
        edges.append(_edge(pid, "applies-when", f.condition, Dimension.CAUSAL))
    else:
        edges.append(_edge(pid, "governs", f.bearer, _OP_PRIMARY_DIM.get(f.operator, Dimension.RELATIONAL)))
    if f.exception:
        edges.append(_edge(pid, "defeated-by", f.exception, Dimension.CAUSAL))
    return edges


class DeonticFormulaND(BaseNDDispatcher):
    """ND that transcribes normative content into deontic formulae.

    Fires on the same normative content the domain NDs do, but produces
    ``kind=deontic-formula`` pairs carrying the structured SDL fields. The
    operator + bearer surface in safe-context triples so the cloud LLM can
    answer "what does the controller have to do, and when?" from the logic,
    not just the prose.
    """

    nd_id = "nd-deontic"
    handles_types = ["normative"]
    handles_facets: list[str] = []   # any normative content
    confidence_floor = 0.45

    def __init__(self, *, detect_conflicts: bool = False) -> None:
        self._detect_conflicts = detect_conflicts

    def extract(self, content, classification, *, source_document=None):
        formulae = extract_formulae(content)
        pair_id_base = _hash_pair(content, self.nd_id, source_document)
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
                        "operator_gloss": _OP_GLOSS.get(f.operator, ""),
                        "bearer": f.bearer,
                        "conditional": bool(f.condition),
                        "defeasible": bool(f.exception),
                        "language": f.language,
                    },
                    "context": {"kind_of_model": "deontic-logic"},
                },
                "solution": {
                    "id": pid,
                    "problem_id": f"{pid}-p",
                    # Structured SDL fields — surface in lock-scrubbed triples.
                    "operator": f.operator,
                    "bearer": f.bearer,
                    "action": f.action,
                    "condition": f.condition,
                    "exception": f.exception,
                    "formula": fd["formula"],
                    "dual": fd["dual"],
                    "body": f"DEONTIC FORMULA\n{fd['formula']}\ndual: {fd['dual']}",
                    "body_format": "structured-deontic",
                    "authority_tier": 1,
                    "confidence": f.confidence,
                },
                "edges": _formula_edges(pid, f),
            })

        if self._detect_conflicts:
            conflicts = detect_conflicts(formulae)
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
                    # A candidate conflict is RELATIONAL until a human resolves
                    # it; it is never auto-walked as a causal/temporal edge.
                    "edges": [_edge(cid, "may-conflict-with", c["action"], Dimension.RELATIONAL)],
                })
        return out


def register_deontic_nd(router, *, detect_conflicts: bool = False) -> None:
    """Register the deontic-formula ND on a router."""
    router.register(DeonticFormulaND(detect_conflicts=detect_conflicts))
