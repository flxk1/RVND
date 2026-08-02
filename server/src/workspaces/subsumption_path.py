# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Subsumption path — multi-hop made explicit, with the five gaps surfaced.

`reasoning.compose_paths` is edge composition: necessary, not sufficient. The
essay wants *more* — the legal-shaped chain rendered as an artifact:

    Norm → Tatbestand → Ausnahme → Auslegung → Subsumtion → Ergebnis

and, at each hop, the five failure modes made visible instead of smoothed:

    retrieval gap  — a required link in the chain is missing entirely;
    context  gap  — a link is present only partially (excerpt, not full);
    reasoning gap  — present but no edge connects it into the chain;
    conflict gap  — two sources disagree (older vs newer; O vs F);
    authority gap  — links of mixed authority with no precedence resolved.

This module assembles the chain from typed atoms (+ optional reasoning edges) and
returns the steps plus an explicit gap list. It never invents a missing link and
never resolves a conflict — a chain with gaps is returned *with its gaps shown*,
which is the point: the reader sees exactly where the inference is load-bearing
and where it is uncertain.

Pure stdlib; consumes atoms the NDs produce.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# The ordered legal roles a complete subsumption walks through.
ROLES = ("norm", "tatbestand", "ausnahme", "auslegung", "subsumtion", "ergebnis")
# Roles a *bound* decision cannot omit (Ausnahme/Auslegung may legitimately be absent).
REQUIRED_ROLES = ("norm", "tatbestand", "subsumtion", "ergebnis")


@dataclass
class Step:
    role: str
    ref: str                 # the atom/provision id or label
    source: str = ""         # citation (provenance)
    authority_tier: int = 3
    partial: bool = False    # only an excerpt was available (context gap)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "ref": self.ref, "source": self.source,
                "authority_tier": self.authority_tier, "partial": self.partial,
                "note": self.note}


@dataclass
class Gap:
    kind: str                # retrieval | context | reasoning | conflict | authority
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail}


@dataclass
class Subsumption:
    steps: list[Step] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """All required roles present AND no retrieval/conflict gap. Context and
        authority gaps are surfaced but do not by themselves void the chain."""
        have = {s.role for s in self.steps}
        blocking = any(g.kind in ("retrieval", "conflict") for g in self.gaps)
        return set(REQUIRED_ROLES) <= have and not blocking

    def render(self) -> str:
        chain = " → ".join(f"{s.role}:{s.ref}" for s in self.steps)
        gap = ("; ".join(f"[{g.kind}] {g.detail}" for g in self.gaps)
               if self.gaps else "no gaps")
        return f"{chain}    ⟂ {gap}"

    def to_dict(self) -> dict[str, Any]:
        return {"steps": [s.to_dict() for s in self.steps],
                "gaps": [g.to_dict() for g in self.gaps],
                "complete": self.complete, "render": self.render()}


def build(atoms: Iterable[dict], *,
          edges: Optional[Iterable[dict]] = None,
          conflicts: Optional[Iterable[dict]] = None) -> Subsumption:
    """Assemble the subsumption chain.

    atoms — ``{"role": one-of-ROLES, "ref": id, "source": cite, "authority_tier":
        int, "partial": bool}``. At most the first atom per role is used (single
        home per role; overlaps live as edges/conflicts).
    edges — optional reasoning edges ``{"subject","object"}`` used to detect a
        *reasoning gap* (a role present but not linked into the chain).
    conflicts — optional ``[{"a","b","detail"}]`` unresolved collisions.
    """
    by_role: dict[str, Step] = {}
    for a in atoms:
        role = (a.get("role") or "").lower()
        if role in ROLES and role not in by_role:
            by_role[role] = Step(role=role, ref=str(a.get("ref", "")),
                                  source=str(a.get("source", "")),
                                  authority_tier=int(a.get("authority_tier", 3)),
                                  partial=bool(a.get("partial", False)),
                                  note=str(a.get("note", "")))
    steps = [by_role[r] for r in ROLES if r in by_role]
    sub = Subsumption(steps=steps)

    # retrieval gap: a required role is missing.
    for r in REQUIRED_ROLES:
        if r not in by_role:
            sub.gaps.append(Gap("retrieval", f"required role '{r}' not retrieved"))
    # context gap: a present link is only partial.
    for s in steps:
        if s.partial:
            sub.gaps.append(Gap("context", f"'{s.role}' present only as excerpt"))
    # reasoning gap: a non-norm role present but no edge links it in.
    if edges is not None:
        linked = set()
        for e in edges:
            linked.add(str(e.get("subject", "")))
            linked.add(str(e.get("object", "")))
        for s in steps:
            if s.role != "norm" and s.ref and s.ref not in linked:
                sub.gaps.append(Gap("reasoning", f"'{s.role}:{s.ref}' not connected by any edge"))
    # conflict gap: unresolved collisions surfaced, never smoothed.
    for c in (conflicts or []):
        sub.gaps.append(Gap("conflict",
                            f"{c.get('a')} vs {c.get('b')}: {c.get('detail', 'unresolved')}"))
    # authority gap: mixed authority among the chain with no top authority.
    tiers = {s.authority_tier for s in steps}
    if len(tiers) > 1 and min(tiers) >= 3:
        sub.gaps.append(Gap("authority", f"chain rests on mixed low authority tiers {sorted(tiers)}"))
    return sub
