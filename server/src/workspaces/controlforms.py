# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Control-form algebra — § 1.5's vocabulary with defined semantics.

A control form is the SPECIFIC shape of human control over an action:
not a traffic light but a set of guarantees. The algebra:

- Each form is a frozenset of GUARANTEES. The partial order is subset
  inclusion: A ≤ B iff guarantees(A) ⊆ guarantees(B) ("B is at least as
  strict"). Four-eyes and expert-review are INCOMPARABLE by design —
  quantity vs competence.
- Composition is conjunction (set union): composing policies can only
  ADD guarantees. Monotonicity, associativity, commutativity and
  idempotence hold by construction; the property tests prove it over the
  whole named vocabulary.
- BLOCK is the absorbing top (nothing executes); AUTO is the identity
  bottom (no human guarantee). "Strictest-wins" pack composition is
  ``compose_all``.

The legacy traffic light maps in: go→AUTO, ask→SINGLE_APPROVER,
block→BLOCK. The matrix keeps its grid; this module gives "ask" a
vocabulary.
"""
from __future__ import annotations

from typing import FrozenSet, Iterable

Guarantees = FrozenSet[str]

# The guarantee dimensions. Adding a dimension is a reviewed change —
# it widens the algebra for every pack.
G_NOTIFY        = "notify_human"          # human informed (after or during)
G_PRE_APPROVAL  = "pre_approval"          # a human approves before execution
G_TWO_APPROVERS = "two_approvers"         # at least two distinct approvers
G_COMPETENCE    = "competent_approver"    # approver matched on domain
G_POST_SAMPLING = "post_hoc_sampling"     # calibration sampling of outcomes
G_VETO_DENY     = "veto_expires_to_deny"  # silence means deny, not consent
G_BLOCKED       = "blocked"               # absorbing: never executes

FORMS: dict[str, Guarantees] = {
    "auto":            frozenset(),
    "notify":          frozenset({G_NOTIFY}),
    "spot_check":      frozenset({G_NOTIFY, G_POST_SAMPLING}),
    "veto_window":     frozenset({G_NOTIFY, G_VETO_DENY}),
    "single_approver": frozenset({G_NOTIFY, G_PRE_APPROVAL}),
    "four_eyes":       frozenset({G_NOTIFY, G_PRE_APPROVAL, G_TWO_APPROVERS}),
    "expert_review":   frozenset({G_NOTIFY, G_PRE_APPROVAL, G_COMPETENCE}),
    "block":           frozenset({G_BLOCKED}),
}

_TRAFFIC = {"go": "auto", "ask": "single_approver", "block": "block"}


def guarantees(form: str | Guarantees) -> Guarantees:
    """Resolve a named form (or a raw guarantee-set) to its guarantee-set.
    Lists/tuples are accepted as raw sets too — composite forms serialize
    as sorted lists in chain events and resolve back here."""
    if isinstance(form, frozenset):
        return form
    if isinstance(form, (list, tuple, set)):
        return frozenset(form)
    try:
        return FORMS[form]
    except KeyError:
        raise ValueError(
            f"unknown control form {form!r}; known: {sorted(FORMS)}"
        ) from None


def leq(a: str | Guarantees, b: str | Guarantees) -> bool:
    """A ≤ B — "B is at least as strict as A". BLOCK is top."""
    ga, gb = guarantees(a), guarantees(b)
    if G_BLOCKED in gb:
        return True
    if G_BLOCKED in ga:
        return False
    return ga <= gb


def comparable(a: str | Guarantees, b: str | Guarantees) -> bool:
    return leq(a, b) or leq(b, a)


def compose(a: str | Guarantees, b: str | Guarantees) -> Guarantees:
    """Conjunction: require BOTH forms' guarantees. BLOCK absorbs."""
    ga, gb = guarantees(a), guarantees(b)
    if G_BLOCKED in ga or G_BLOCKED in gb:
        return FORMS["block"]
    return ga | gb


def compose_all(forms: Iterable[str | Guarantees]) -> Guarantees:
    """Strictest-wins over a pack stack: conjunction of every layer."""
    acc: Guarantees = frozenset()
    for f in forms:
        acc = compose(acc, f)
    return acc


def name_of(g: Guarantees) -> str:
    """The canonical name when the set matches a named form, else a
    deterministic composite label (composition can exceed the names)."""
    for name, gs in FORMS.items():
        if gs == g:
            return name
    return "composite(" + "+".join(sorted(g)) + ")"


def from_traffic_light(light: str) -> Guarantees:
    """Map the legacy matrix output into the algebra."""
    try:
        return FORMS[_TRAFFIC[light]]
    except KeyError:
        raise ValueError(
            f"unknown traffic light {light!r}; known: {sorted(_TRAFFIC)}"
        ) from None
