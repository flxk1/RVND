# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND adapter seam over loomground-brief — the bounded supervisor brief;
internal by design.

The plane answers one question: *what is the minimum a supervisor must read?*
It selects over statused premises, keeps only what is unsettled, and reports
how many settled premises it left out. Its size is therefore a function of what
went unresolved, not of how much was examined — a coverage run over 500
furnished rooms with one gap yields one item to read and 500 omitted.

This module is the only place RVND names the plane. It takes plain lists rather
than RVND types so the seam stays thin and acyclic: the caller
(:mod:`rvnd.evidence_coverage`) owns the domain objects, this owns the
translation, and the plane owns the selection.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from loomground_brief import BriefItem, OversightBrief, oversight_brief
from loomground_solver.epistemic_status import EpistemicStatus, StatusedPremise

__all__ = [
    "BriefItem", "OversightBrief", "oversight_brief",
    "EpistemicStatus", "StatusedPremise",
    "premises_from_coverage", "brief_from_coverage",
]


def premises_from_coverage(
    *,
    furnished: Iterable[dict[str, Any]] = (),
    empty: Iterable[dict[str, Any]] = (),
    orphans: Iterable[dict[str, Any]] = (),
) -> tuple[StatusedPremise, ...]:
    """Map a coverage report onto statused premises.

    The mapping is the whole judgement, so it is stated once, here:

      * a FURNISHED room is **ASSERTED** — evidence was placed, the position is
        taken, and the supervisor is not asked to re-read it;
      * an EMPTY room is **UNKNOWN** — a requirement with no evidence. Not
        "false": nobody established anything either way, which is exactly the
        distinction the plane's `gap` kind carries;
      * an ORPHAN document is **CONTESTED** — it was offered as evidence and
        fits no room, so the placement is in dispute rather than absent. The
        coverage mapper already declines to file these silently; this keeps
        that refusal visible to the supervisor.
    """
    premises: list[StatusedPremise] = []
    for room in furnished:
        premises.append(StatusedPremise(f"room:{room['room_id']}", EpistemicStatus.ASSERTED))
    for room in empty:
        premises.append(StatusedPremise(f"room:{room['room_id']}", EpistemicStatus.UNKNOWN))
    for doc in orphans:
        premises.append(StatusedPremise(f"doc:{doc['doc_id']}", EpistemicStatus.CONTESTED))
    return tuple(premises)


def brief_from_coverage(
    *,
    furnished: Iterable[dict[str, Any]] = (),
    empty: Iterable[dict[str, Any]] = (),
    orphans: Iterable[dict[str, Any]] = (),
    divergences: Sequence[Any] = (),
) -> OversightBrief:
    """The bounded brief for one coverage run."""
    return oversight_brief(
        premises=premises_from_coverage(furnished=furnished, empty=empty, orphans=orphans),
        divergences=divergences,
    )
