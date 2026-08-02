# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The algebra of legal connection — how legal entities are bound to one another
*by law*.

A map of legal entities is only useful if you can *compose* the edges: a company
is incorporated in Germany, Germany is a member of the EU — therefore the company
is subject to EU law. That inference is an algebra: a set of connection relations
and a composition rule that says what a two-step chain yields. This module is the
universal, jurisdiction-agnostic algebra; ``legal_world.py`` supplies the actual
entities and edges it runs over.

Three relation families connect the two entity layers (jurisdictions and legal
persons):

  jurisdiction ↔ jurisdiction : member_of, has_primacy_over, party_to, bound_by,
                                recognises, equivalent_to, refers_to (renvoi),
                                candidate_of, reserves_against
  person ↔ jurisdiction       : incorporated_in, established_in, resident_in,
                                national_of, targets, subject_to, bound_by
  person ↔ person             : controls, parent_of, subsidiary_of, agent_of,
                                party_to_contract, controller_of, processor_for

The composition table is **partial on purpose**. Where a two-step chain has a
settled legal answer it returns the resulting connection; where the chain is
legally contested (extraterritorial reach through corporate control; whether a
treaty binds a private party without incorporation) it returns ``ESCALATE`` — the
same discipline as the norm-theory contract: surface the open question, never
fabricate a resolution. Where no relation follows at all it returns ``None``.

Each connection also carries a 5D reasoning dimension, so a world-map edge
projects one-to-one into the Workspace KG (see ``workspaces.dimensions`` /
``legal_world.project``). Pure stdlib.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Union

from workspaces.adapters.solver.dimensions import Dimension


class Connection(Enum):
    # jurisdiction ↔ jurisdiction
    MEMBER_OF = "member_of"
    HAS_PRIMACY_OVER = "has_primacy_over"
    PARTY_TO = "party_to"                # party to a treaty / regime
    BOUND_BY = "bound_by"
    RECOGNISES = "recognises"            # mutual recognition of judgments/standards
    EQUIVALENT_TO = "equivalent_to"      # adequacy / equivalence decision (symmetric)
    REFERS_TO = "refers_to"              # conflict-of-laws / renvoi
    CANDIDATE_OF = "candidate_of"        # accession candidate (becoming a member)
    RESERVES_AGAINST = "reserves_against"  # reservation / opt-out
    # person ↔ jurisdiction
    INCORPORATED_IN = "incorporated_in"
    ESTABLISHED_IN = "established_in"
    RESIDENT_IN = "resident_in"
    NATIONAL_OF = "national_of"
    TARGETS = "targets"                  # offers goods/services into a market (GDPR Art 3(2))
    SUBJECT_TO = "subject_to"            # under a legal order's law (often derived)
    # person ↔ person
    CONTROLS = "controls"
    PARENT_OF = "parent_of"
    SUBSIDIARY_OF = "subsidiary_of"
    AGENT_OF = "agent_of"
    PARTY_TO_CONTRACT = "party_to_contract"
    CONTROLLER_OF = "controller_of"      # GDPR controller→processing
    PROCESSOR_FOR = "processor_for"      # GDPR processor→controller
    # corpus / catalogue edges (organisations ↔ instruments ↔ jurisdictions)
    ENFORCES = "enforces"                # a regulator enforces an instrument
    SUPERVISES = "supervises"            # a regulator supervises a person/sector
    APPLIES_IN = "applies_in"            # an instrument applies in a jurisdiction
    ESTABLISHED_BY = "established_by"     # a body established by an instrument/treaty
    ADOPTED_BY = "adopted_by"            # an instrument adopted by a legal order
    SUPERSEDES = "supersedes"            # later instrument over earlier (temporal)
    DESCENDS_FROM = "descends_from"      # standard/instrument lineage
    PRESUMES_CONFORMITY = "presumes_conformity"  # harmonised standard → instrument it serves
    DECIDES = "decides"                  # a decision applied a rule (cross-layer)


class _Escalate:
    """Sentinel: a legally contested composition — surface, do not resolve."""
    __slots__ = ()
    def __repr__(self) -> str:           # pragma: no cover - cosmetic
        return "ESCALATE"


ESCALATE = _Escalate()
Composed = Union[Connection, _Escalate, None]

VOCABULARY: frozenset = frozenset(c.value for c in Connection)


def is_connection(name: str) -> bool:
    return name in VOCABULARY


# ── The composition table ─────────────────────────────────────────────────────
# Read (a, b) → result: "entity is connected to X by a, X is connected to Y by b;
# what connects the entity to Y?" Only legally settled chains are filled; missing
# keys yield None (no relation follows); ESCALATE marks a contested chain.

_C = Connection
_COMPOSE: dict[tuple[Connection, Connection], Composed] = {
    # establishing a person under a legal order, then climbing the jurisdiction
    # ladder → the person is subject to the higher order. (Art 3(1) GDPR logic.)
    (_C.INCORPORATED_IN, _C.MEMBER_OF): _C.SUBJECT_TO,
    (_C.ESTABLISHED_IN, _C.MEMBER_OF):  _C.SUBJECT_TO,
    (_C.RESIDENT_IN, _C.MEMBER_OF):     _C.SUBJECT_TO,
    (_C.NATIONAL_OF, _C.MEMBER_OF):     _C.SUBJECT_TO,
    (_C.TARGETS, _C.MEMBER_OF):         _C.SUBJECT_TO,   # Art 3(2) targeting
    # subjection is transitive up the membership ladder
    (_C.SUBJECT_TO, _C.MEMBER_OF):      _C.SUBJECT_TO,
    (_C.SUBJECT_TO, _C.HAS_PRIMACY_OVER): None,          # wrong direction, no inference
    # being subject to an order that is party to / bound by an instrument
    (_C.SUBJECT_TO, _C.BOUND_BY):       _C.BOUND_BY,
    (_C.MEMBER_OF, _C.BOUND_BY):        _C.BOUND_BY,
    (_C.PARTY_TO, _C.BOUND_BY):         _C.BOUND_BY,
    # membership is transitive (subnational ∈ state ∈ union)
    (_C.MEMBER_OF, _C.MEMBER_OF):       _C.MEMBER_OF,
    (_C.MEMBER_OF, _C.HAS_PRIMACY_OVER): None,
    (_C.HAS_PRIMACY_OVER, _C.HAS_PRIMACY_OVER): _C.HAS_PRIMACY_OVER,
    # incorporation/establishment in a state that is merely *party to* a treaty:
    # whether the treaty reaches the private party depends on self-execution /
    # transposition — contested, so ESCALATE (mirrors the directive/treaty rule).
    (_C.INCORPORATED_IN, _C.PARTY_TO):  ESCALATE,
    (_C.ESTABLISHED_IN, _C.PARTY_TO):   ESCALATE,
    (_C.SUBJECT_TO, _C.PARTY_TO):       ESCALATE,
    # corporate-group reach is legally contested (single-economic-unit; GDPR
    # group liability; competition attribution) → ESCALATE, never auto-asserted.
    (_C.CONTROLS, _C.INCORPORATED_IN):  ESCALATE,
    (_C.CONTROLS, _C.SUBJECT_TO):       ESCALATE,
    (_C.PARENT_OF, _C.SUBJECT_TO):      ESCALATE,
    (_C.PARENT_OF, _C.INCORPORATED_IN): ESCALATE,
    (_C.CONTROLS, _C.CONTROLS):         _C.CONTROLS,      # control is transitive
    (_C.PARENT_OF, _C.PARENT_OF):       _C.PARENT_OF,
    # mutual recognition / adequacy / renvoi are NOT transitive — escalate chains
    (_C.RECOGNISES, _C.RECOGNISES):     ESCALATE,
    (_C.EQUIVALENT_TO, _C.EQUIVALENT_TO): ESCALATE,
    (_C.REFERS_TO, _C.REFERS_TO):       ESCALATE,
}


def compose(a: Connection, b: Connection) -> Composed:
    """Compose a two-step legal chain. Returns the resulting Connection, the
    ESCALATE sentinel for a contested chain, or None when nothing follows."""
    return _COMPOSE.get((a, b))


def compose_path(chain: list[Connection]) -> tuple[Composed, bool]:
    """Left-fold a path of connections into the relation it yields, plus a flag
    that is True if any step was contested (ESCALATE). A single connection folds
    to itself. A None at any step breaks the chain (returns (None, escalated)."""
    if not chain:
        return None, False
    acc: Composed = chain[0]
    escalated = False
    for nxt in chain[1:]:
        if acc is ESCALATE:
            escalated = True
            # once contested, keep folding optimistically from the next leg so
            # provenance is preserved, but the result stays ESCALATE
            acc = nxt
            continue
        if not isinstance(acc, Connection):
            return None, escalated
        acc = compose(acc, nxt)
        if acc is ESCALATE:
            escalated = True
    if acc is ESCALATE:
        escalated = True
    return acc, escalated


# ── Inverses (where a clean dual exists) ──────────────────────────────────────

_INVERSE: dict[Connection, Connection] = {
    Connection.PARENT_OF: Connection.SUBSIDIARY_OF,
    Connection.SUBSIDIARY_OF: Connection.PARENT_OF,
    Connection.CONTROLLER_OF: Connection.PROCESSOR_FOR,
    Connection.PROCESSOR_FOR: Connection.CONTROLLER_OF,
    Connection.EQUIVALENT_TO: Connection.EQUIVALENT_TO,   # symmetric
}


def inverse(c: Connection) -> Optional[Connection]:
    return _INVERSE.get(c)


# ── Connection → 5D dimension (for KG projection) ─────────────────────────────

_DIMENSION: dict[Connection, Dimension] = {
    # how the structure is built (containment, ownership, establishment)
    Connection.MEMBER_OF: Dimension.STRUCTURAL,
    Connection.HAS_PRIMACY_OVER: Dimension.STRUCTURAL,
    Connection.CONTROLS: Dimension.STRUCTURAL,
    Connection.PARENT_OF: Dimension.STRUCTURAL,
    Connection.SUBSIDIARY_OF: Dimension.STRUCTURAL,
    Connection.INCORPORATED_IN: Dimension.STRUCTURAL,
    Connection.ESTABLISHED_IN: Dimension.STRUCTURAL,
    Connection.RESIDENT_IN: Dimension.STRUCTURAL,
    Connection.NATIONAL_OF: Dimension.STRUCTURAL,
    # what brings the entity under the law / makes it bound (effect)
    Connection.SUBJECT_TO: Dimension.CAUSAL,
    Connection.BOUND_BY: Dimension.CAUSAL,
    Connection.PARTY_TO: Dimension.CAUSAL,
    Connection.TARGETS: Dimension.CAUSAL,
    Connection.REFERS_TO: Dimension.CAUSAL,
    # what it is linked to / horizontal association
    Connection.RECOGNISES: Dimension.RELATIONAL,
    Connection.EQUIVALENT_TO: Dimension.RELATIONAL,
    Connection.AGENT_OF: Dimension.RELATIONAL,
    Connection.PARTY_TO_CONTRACT: Dimension.RELATIONAL,
    Connection.CONTROLLER_OF: Dimension.RELATIONAL,
    Connection.PROCESSOR_FOR: Dimension.RELATIONAL,
    # deliberate carve-out (a purpose)
    Connection.RESERVES_AGAINST: Dimension.INTENTIONAL,
    # a time-directed trajectory toward membership
    Connection.CANDIDATE_OF: Dimension.TEMPORAL,
    # corpus / catalogue edges
    Connection.ENFORCES: Dimension.INTENTIONAL,    # the body's mandate / purpose
    Connection.SUPERVISES: Dimension.INTENTIONAL,
    Connection.APPLIES_IN: Dimension.CAUSAL,        # what brings the instrument to bear
    Connection.ESTABLISHED_BY: Dimension.STRUCTURAL,
    Connection.ADOPTED_BY: Dimension.STRUCTURAL,
    Connection.SUPERSEDES: Dimension.TEMPORAL,
    Connection.DESCENDS_FROM: Dimension.RELATIONAL,
    Connection.PRESUMES_CONFORMITY: Dimension.INTENTIONAL,
    Connection.DECIDES: Dimension.CAUSAL,           # the decision determines the rule's application
}


def dimension(c: Connection) -> Dimension:
    return _DIMENSION.get(c, Dimension.RELATIONAL)


# Relations that, when they are the *result* of a reach computation, mean a legal
# order actually governs the entity.
GOVERNING = frozenset({Connection.SUBJECT_TO, Connection.BOUND_BY})


ALGEBRA_LAWS: tuple[tuple[str, str], ...] = (
    ("LC-1", "Composition is partial: a chain yields a Connection, ESCALATE "
             "(contested), or None (nothing follows) — never a guessed relation."),
    ("LC-2", "Establishment under a state + that state's membership of a higher "
             "order ⇒ SUBJECT_TO the higher order (Art 3(1) GDPR logic)."),
    ("LC-3", "Mere party_to a treaty does not reach a private party without "
             "incorporation/self-execution → ESCALATE."),
    ("LC-4", "Corporate-group reach (controls/parent_of ∘ subjection) is "
             "contested → ESCALATE, never auto-attributed."),
    ("LC-5", "Mutual recognition, adequacy and renvoi are not transitive → "
             "ESCALATE on chaining."),
)
