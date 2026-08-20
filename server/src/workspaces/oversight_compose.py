# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fingerprint composition + separation of duties.

When several normative sources govern one action, each compiles to a set of
:class:`OversightFacet`s (the fingerprint, algebra §6). This module composes
the applicable facets into one binding constraint by lattice rules, and
enforces the L4 separation-of-duties invariant from stress-test case 10.

Composition (the piano-piece rule, algebra §6):
  * floors **join** — the strictest min_level wins;
  * ceilings **meet** — the lowest grade_ceiling wins;
  * personal **OR** — any non-delegable source makes the whole composite
    non-delegable;
  * measures / cadences **union** — every observability duty survives.

Deontic conflict does NOT lattice-compose: that is handled upstream by the
norm contract (ESCALATE), never joined away here. This module composes only
the comparable dimensions (levels, grades, measures).

Separation of duties (L4, stress-test case 10):
  * No edge may approve its own widening. A control change (routing row,
    standing approval, grade promotion, learnable-scope widening) may not be
    approved by the agent that requested it; routing changes additionally
    require a human with a `controls` relation. ``check_separation`` returns
    the violation or None.

Pure stdlib, deterministic, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .oversight_extractor import OVERSIGHT_LEVELS, OversightFacet

_LEVEL_ORDER = {lvl: i for i, lvl in enumerate(OVERSIGHT_LEVELS)}
from .adapters.policy_languages import (grade_index as _grade_index,
                                         grade_levels as _grade_levels)

_GRADES = _grade_levels()          # grade lattice consumed from governance's grammar
_GRADE_ORDER = _grade_index()


@dataclass
class ComposedOversight:
    """The single constraint binding an action after all fingerprints merge."""
    min_level: str = ""                     # strictest floor (join)
    grade_ceiling: str = ""                 # lowest cap (meet); "" = uncapped
    personal: bool = False                  # any source non-delegable
    overseers: list[str] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)
    cadences: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)   # raw sentences merged
    contributing: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_level": self.min_level,
            "grade_ceiling": self.grade_ceiling,
            "personal": self.personal,
            "overseers": self.overseers,
            "measures": self.measures,
            "cadences": self.cadences,
            "contributing": self.contributing,
        }


def compose_facets(facets: Iterable[OversightFacet]) -> ComposedOversight:
    """Lattice-compose a set of OversightFacets into one constraint.

    Empty input → an empty ComposedOversight (no constraint). Order-independent
    and idempotent: composing the same set twice yields the same result, and a
    facet composed with itself is itself (lattice laws)."""
    out = ComposedOversight()
    overseers: list[str] = []
    measures: list[str] = []
    cadences: list[str] = []
    sources: list[str] = []
    for f in facets:
        out.contributing += 1
        # floor: strictest (highest order) wins
        if f.min_level and (not out.min_level
                            or _LEVEL_ORDER[f.min_level] > _LEVEL_ORDER[out.min_level]):
            out.min_level = f.min_level
        # ceiling: lowest grade wins
        if f.grade_ceiling and (not out.grade_ceiling
                                or _GRADE_ORDER[f.grade_ceiling] < _GRADE_ORDER[out.grade_ceiling]):
            out.grade_ceiling = f.grade_ceiling
        out.personal = out.personal or f.personal
        if f.overseer and f.overseer not in overseers:
            overseers.append(f.overseer)
        if f.measure and f.measure not in measures:
            measures.append(f.measure)
        if f.cadence and f.cadence not in cadences:
            cadences.append(f.cadence)
        if f.raw_sentence and f.raw_sentence not in sources:
            sources.append(f.raw_sentence)
    out.overseers = overseers
    out.measures = measures
    out.cadences = cadences
    out.sources = sources
    return out


def binds_grade(composed: ComposedOversight, requested_grade: str) -> str:
    """Apply the composite ceiling to a requested grade (meet). The floor
    (min_level) governs the oversight dial separately and is consumed by the
    gate/orchestrator, not here."""
    if not composed.grade_ceiling:
        return requested_grade
    ri = _GRADE_ORDER.get(requested_grade, 0)
    # UNRECOGNISED ceiling token → fail-safe to L0 (most restrictive), never the
    # old fail-OPEN default of L4/uncapped (M1; mirrors breaker.cap_grade).
    ci = _GRADE_ORDER.get(composed.grade_ceiling, 0)
    return _GRADES[min(ri, ci)]


# ── separation of duties (L4, stress-test case 10) ──────────────────────────

# Action classes that change who decides / what is permitted — control changes.
_CONTROL_CHANGES = frozenset({
    "routing-change", "standing-approval-grant", "standing-approval-widen",
    "grade-promotion", "learnable-scope-widen", "policy-change",
    "lease-grant", "quarantine-clear",
})
# The subset that additionally requires a human holding a `controls` relation.
_ROUTING_LIKE = frozenset({
    "routing-change", "grade-promotion", "lease-grant", "quarantine-clear",
})


@dataclass
class ControlChange:
    """A proposed change to the governance graph itself."""
    kind: str                               # one of _CONTROL_CHANGES
    requested_by: str                       # actor proposing the change
    target_agent: str = ""                  # whose permissions widen
    approver: str = ""                      # actor who would approve it
    approver_is_human_controller: bool = False


def check_separation(change: ControlChange) -> Optional[str]:
    """Return the separation-of-duties violation, or None if the change is
    clean. The invariant (L4): **no edge may approve its own widening.**

    Two rules:
      1. The approver may not be the requesting agent, and may not be the
         agent whose permissions the change widens (self-approval / self-
         widening — the 4×10 compound attack).
      2. A routing-like change additionally requires the approver to be a
         human holding a `controls` relation."""
    if change.kind not in _CONTROL_CHANGES:
        return None                          # not a control change; nothing to check
    if not change.approver.strip():
        return f"control change {change.kind!r} has no named approver"
    if change.approver == change.requested_by:
        return (f"separation of duties: {change.requested_by!r} cannot approve "
                f"its own {change.kind!r}")
    if change.target_agent and change.approver == change.target_agent:
        return (f"separation of duties: {change.approver!r} cannot approve a "
                f"change that widens its own permissions ({change.kind!r})")
    if change.kind in _ROUTING_LIKE and not change.approver_is_human_controller:
        return (f"{change.kind!r} requires a human approver holding a "
                f"`controls` relation; {change.approver!r} does not")
    return None


def approves_clean(change: ControlChange) -> bool:
    return check_separation(change) is None
