# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Currency / validity-dating pipeline.

The one failure mode you cannot ship for law is a *guessed date*. So this
pipeline enforces the locked split (concept §5, decision #4):

    the LLM supplies the *relationship* (supersedes, co-applies, conflicts);
    the currency pipeline supplies the *date* — in_force_from,
    consolidation_version, superseded_by — keyed by the instrument's CELEX
    identifier, never inferred.

``attach_validity`` only ever writes date fields read from the registry. It
has no power to assert or change a relationship, and when a CELEX id is not in
the registry it records ``status: "unknown"`` rather than guessing.

A ``refresh`` run is a graph query, not a re-prompt: it returns the
obligation-pairs whose ``superseded_by`` has flipped (or that are not yet in
force as of a date), plus the edges resting on them — so a stale finding can
be re-opened without re-reading the source text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Iterable, Optional


_CELEX_RE = re.compile(r"\b(?:CELEX[:\s]*)?(\d{5}[A-Z]\d{3,4})\b")


def extract_celex(cited_sources: Iterable[str]) -> Optional[str]:
    """First CELEX number found in a pair's cited sources (e.g. 32024R1689)."""
    for s in cited_sources or ():
        m = _CELEX_RE.search(str(s))
        if m:
            return m.group(1)
    return None


@dataclass
class ValidityRecord:
    """Dated validity facts for one instrument — sourced, never inferred."""

    celex: str
    in_force_from: Optional[str] = None        # ISO date
    consolidation_version: Optional[str] = None
    superseded_by: Optional[str] = None        # CELEX of the superseding act
    superseded_from: Optional[str] = None       # ISO date the supersession bites
    source: str = "registry"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CurrencyRegistry:
    """Local, per-user store of dated validity facts keyed by CELEX.

    Public-law dates are re-verifiable, so this MAY ship pre-populated (the
    law is public); it is still a starter kit, kept honest by refresh. The
    registry never holds relationships — only dates."""

    def __init__(self, records: Optional[dict[str, ValidityRecord]] = None) -> None:
        self._by_celex: dict[str, ValidityRecord] = dict(records or {})

    @classmethod
    def from_rows(cls, rows: Iterable[dict[str, Any]]) -> "CurrencyRegistry":
        recs = {}
        for r in rows:
            celex = r["celex"]
            recs[celex] = ValidityRecord(
                celex=celex,
                in_force_from=r.get("in_force_from"),
                consolidation_version=r.get("consolidation_version"),
                superseded_by=r.get("superseded_by"),
                superseded_from=r.get("superseded_from"),
                source=r.get("source", "registry"))
        return cls(recs)

    def get(self, celex: str) -> Optional[ValidityRecord]:
        return self._by_celex.get(celex)

    def upsert(self, rec: ValidityRecord) -> None:
        self._by_celex[rec.celex] = rec


def _parse(d: Optional[str]) -> Optional[date]:
    if not d:
        return None
    try:
        return date.fromisoformat(d)
    except ValueError:
        return None


def validity_status(rec: Optional[ValidityRecord], as_of: date) -> str:
    """One of: ``in-force`` | ``not-yet-in-force`` | ``superseded`` | ``unknown``.

    ``unknown`` is returned whenever the registry has no dated record — the
    pipeline refuses to guess."""
    if rec is None:
        return "unknown"
    sup = _parse(rec.superseded_from)
    if rec.superseded_by and sup is not None and sup <= as_of:
        return "superseded"
    iff = _parse(rec.in_force_from)
    if iff is not None and iff > as_of:
        return "not-yet-in-force"
    if iff is None and rec.superseded_by is None and rec.consolidation_version is None:
        return "unknown"
    return "in-force"


def attach_validity(
    pair: dict[str, Any], registry: CurrencyRegistry, *, as_of: Optional[date] = None,
) -> dict[str, Any]:
    """Stamp a pair's solution with a ``validity`` facet from the registry.

    Mutates and returns the pair. Writes ONLY date fields + a status; never a
    relationship. A pair whose CELEX is absent gets ``status: "unknown"``."""
    as_of = as_of or date.today()
    sol = pair.setdefault("solution", {})
    celex = extract_celex(sol.get("cited_sources", []))
    rec = registry.get(celex) if celex else None
    status = validity_status(rec, as_of)
    sol["validity"] = {
        "celex": celex,
        "status": status,
        "in_force_from": rec.in_force_from if rec else None,
        "consolidation_version": rec.consolidation_version if rec else None,
        "superseded_by": rec.superseded_by if rec else None,
        "superseded_from": rec.superseded_from if rec else None,
        "as_of": as_of.isoformat(),
        "source": rec.source if rec else None,
    }
    return pair


@dataclass
class RefreshResult:
    superseded: list[str] = field(default_factory=list)       # pair ids now superseded
    not_yet_in_force: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    affected_edges: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def needs_review(self) -> bool:
        return bool(self.superseded or self.not_yet_in_force)


def refresh(
    pairs: list[dict[str, Any]], registry: CurrencyRegistry,
    *, as_of: Optional[date] = None, edges: Optional[Iterable[dict[str, Any]]] = None,
) -> RefreshResult:
    """A currency refresh as a graph query: re-stamp every pair, collect those
    whose validity changed to superseded / not-yet-in-force / unknown, and the
    edges that rest on them (so assessments built on stale law surface for
    re-opening). No source text is re-read; this is pure date arithmetic over
    the registry."""
    as_of = as_of or date.today()
    res = RefreshResult()
    flagged: set[str] = set()
    for pair in pairs:
        attach_validity(pair, registry, as_of=as_of)
        status = pair["solution"]["validity"]["status"]
        pid = pair.get("id")
        if status == "superseded":
            res.superseded.append(pid); flagged.add(pid)
        elif status == "not-yet-in-force":
            res.not_yet_in_force.append(pid); flagged.add(pid)
        elif status == "unknown":
            res.unknown.append(pid)
    for e in edges or ():
        if e.get("subject") in flagged or e.get("object") in flagged:
            res.affected_edges.append(e)
    return res
