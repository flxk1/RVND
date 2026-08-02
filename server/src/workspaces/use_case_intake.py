# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Use-case intake — the NEUTRAL facets that fit ANY policy, over the EXISTING card spine.

This is a thin helper, NOT a parallel structure: the card is a ``subject_card.SubjectCard`` on
the registered ``neutral`` vocabulary; persistence is ``card_store``; per-run answers merge via
``fact_intake``. This module only names the universal axes and marks which one escalates.

The universal axes (``subject_card.NEUTRAL_VOCAB``): ``role · domain · jurisdiction · category ·
scope``, plus the free-text ``description`` (the activity / purpose). Every axis is a DECLARED
fact except ``category`` — the risk/class determination Rvnd carries but never makes; an unset
axis is UNKNOWN → the rules keyed on it sit at MAY_APPLY (surfaced, never guessed).
"""
from __future__ import annotations

from typing import Any

from . import subject_card as _sc

DOMAIN = "neutral"

#: name → kind. ``declared`` = a fact the user states; ``escalated`` = a legal determination
#: Rvnd carries but routes to a human, never decides.
UNIVERSAL_FACETS: tuple[tuple[str, str], ...] = (
    ("role", "declared"),
    ("sector", "declared"),          # field of application (named 'sector' — 'domain' is taken by make_card)
    ("jurisdiction", "declared"),
    ("category", "escalated"),
    ("scope", "declared"),
)
ESCALATED: frozenset = frozenset(f for f, kind in UNIVERSAL_FACETS if kind == "escalated")


def facet_kind(name: str) -> str | None:
    """'declared' | 'escalated' for a universal facet, or None if not universal."""
    return dict(UNIVERSAL_FACETS).get(name)


def blank(*, description: str = "", **facets: Any) -> _sc.SubjectCard:
    """A capture-first neutral use-case card — a real ``SubjectCard`` (no parallel type). Valid
    with only a description; pass any universal facet to narrow it."""
    return _sc.make_card(DOMAIN, description=description, **facets)


def unknown_facets(card: _sc.SubjectCard) -> list[str]:
    """Universal facets not yet set on the card — the rules keyed on these will be MAY_APPLY."""
    return [f for f, _ in UNIVERSAL_FACETS if f not in card.facets]


def open_determinations() -> list[str]:
    """The escalated facets — flagged as a human's call, never banked as fact (``category``)."""
    return list(ESCALATED)


def save(card: _sc.SubjectCard, folder: str, *, actor: str = "user", log_root: Any = None) -> Any:
    """Persist through the existing card store (signed + auditable) — no new persistence path."""
    from . import card_store
    return card_store.save_card(card, folder, actor=actor, log_root=log_root)
