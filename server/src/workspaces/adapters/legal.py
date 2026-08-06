# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND adapter seam over loomground-legal — the legal-domain plane; internal by design.

The workspaces boundary rule confines every direct import of an upstream
Loomground package to the ``adapters/`` seam (see
``tests/test_adapter_boundary.py``). This module is that seam for the **legal
plane**. It has two layers:

1. **connection algebra** (the historical ``legal_connection`` cut) — re-exports
   loomground-legal's connection algebra (``connection_algebra`` — the solver
   ``RelationAlgebra`` built from the package's ``connections.json``), plus the
   solver's ``ESCALATE`` sentinel and 5D ``Dimension``, so
   ``workspaces.legal_connection`` (the historical shim) consumes them through
   here rather than reaching upstream directly.

2. **the world stack** (this cut) — re-exports the enriched legal-domain surface
   (``Entity`` / ``EntityKind`` / ``GovEntry`` / ``ReachResult`` / the world seed,
   the md-table corpus loader, the curated relational enrichment, the instrument
   registry, corpus validation, and the contract-instance model) and **wires the
   injected ports** with RVND's own providers — the legitimate second line that
   STAYS in RVND:

     * ``build_world`` ← RVND's ``world_corpus_loader._default_refdir()``;
     * ``load_instruments`` ← RVND's ``regulatory_population.default_csv()``;
     * ``enrich(world, instruments=…)`` ← wired to ``load_instruments(default_csv())``.

Two deliberate translations keep behavior byte-for-byte with the retired RVND
modules:

  * **enum edges.** loomground-legal's ``WorldEdge.connection`` is the relation
    *name* (a string); RVND's historical surface — and every consumer that reads
    ``ed.connection.value`` or tests ``ed.connection is Connection.APPLIES_IN`` —
    carries the ``legal_connection.Connection`` *enum*. The seam presents an
    enum-edged ``WorldMap`` (RVND's historical container) and translates to the
    package's string-edged map at the two boundaries where the package mechanism
    runs: :meth:`WorldMap.reach` (delegated whole to the package — composition is
    the solver algebra's, never re-grown here) and the corpus/validation loaders.
  * **the RVND-KG projection stays local.** ``WorldMap.project`` /
    ``dimensions_present`` emit RVND's 5D-KG pair-dict schema; they are not the
    package's concern and are attached to this container by
    ``workspaces.legal_world`` (the shim), not defined here.

Nothing here re-implements the connection algebra, the world seed data, the
md-table parser, the curated relations, the instrument catalogue, the validation
tiers, or the contract model — those are loomground-legal's, whole and entire.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from loomground_solver import ESCALATE, Dimension
from loomground_legal.connection import (
    GOVERNING,
    connection_algebra,
    is_connection,
    load_connections,
)

# -- the legal-domain surface: the plane's public model + data, unchanged -----
import loomground_legal as _L
from loomground_legal import (
    Entity, EntityKind, GovEntry, ReachResult, JURISDICTION_KINDS,
    PartyRef, ContractInstance, ContractError,
    CODE, DOMAIN, TRANCHES, EU27,
    Finding, PRIMARY_LAW, INSTITUTIONAL, SUPPORTING, SECONDARY, GENERAL,
)
from loomground_legal.contracts import _lei_checksum_ok

# -- anchoring (the legal-domain step; norm-independent) ----------------------
# Placing a norm onto the instruments/jurisdictions/regulators that govern it is
# a LEGAL-domain concern. It now lives in loomground-legal (lifted out of the
# norm plane), and RVND consumes it HERE — the legal seam — not from norm.
from loomground_legal import (
    Anchor, anchor, place_legal_text,
    ANCHOR_KINDS, ANCHOR_RELATIONS, TextProvision, segment_provisions,
)
# instrument cross-reference resolution + document summary — the legal-domain
# capabilities the crossref / legal doc-summary extractors consume (retiring the
# hand-rolled parallels those RVND modules used to carry).
from loomground_legal import (
    InstrumentRef, INSTRUMENTS, CrossReference,
    resolve_celex, resolve_citation_number, resolve_short_name,
    infer_host_instrument, extract_cross_references,
    DocumentSummary, summarize_document,
)
# applicable-law theory: the universal source-class map + the jurisdiction-family
# packs. Re-exported as the plane's own modules so the ``workspaces.source_classes``
# / ``workspaces.legal_systems`` shims consume them HERE — the single legal import
# site — rather than reaching upstream. The taxonomy, effect ceilings, relation
# vocabulary, pack registry and applicable-law resolver are the plane's, whole.
from loomground_legal import source_classes, legal_systems

__all__ = [
    # connection algebra (the legal_connection cut)
    "ESCALATE", "Dimension",
    "GOVERNING", "connection_algebra", "is_connection", "load_connections",
    # world model + seed
    "Entity", "EntityKind", "WorldEdge", "WorldMap", "GovEntry", "ReachResult",
    "JURISDICTION_KINDS", "seed_world", "reach", "as_package_world",
    # corpus loaders + enrichment
    "build_world", "enrich", "load_instruments",
    "CODE", "DOMAIN", "TRANCHES", "EU27",
    # validation
    "validate_corpus", "Finding",
    "PRIMARY_LAW", "INSTITUTIONAL", "SUPPORTING", "SECONDARY", "GENERAL",
    # contract model (registry stays in RVND)
    "PartyRef", "ContractInstance", "ContractError", "_lei_checksum_ok",
    # anchoring (the legal-domain placement step)
    "Anchor", "anchor", "place_legal_text",
    "ANCHOR_KINDS", "ANCHOR_RELATIONS", "TextProvision", "segment_provisions",
    # instrument cross-reference resolution + document summary
    "InstrumentRef", "INSTRUMENTS", "CrossReference",
    "resolve_celex", "resolve_citation_number", "resolve_short_name",
    "infer_host_instrument", "extract_cross_references",
    "DocumentSummary", "summarize_document",
    # applicable-law theory (plane modules the source_classes/legal_systems shims consume)
    "source_classes", "legal_systems",
]


# ════════════════════════════════════════════════════════════════════════════
# WorldEdge / WorldMap — RVND's enum-edged historical container over the plane
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class WorldEdge:
    """A directed connection edge — RVND's historical shape, carrying the
    ``legal_connection.Connection`` *enum* (not the package's relation string),
    so ``ed.connection.value`` and ``ed.connection is Connection.X`` keep working
    across every RVND consumer."""

    subject: str
    connection: Any                    # legal_connection.Connection at runtime
    object: str
    basis: str = ""
    url: str = ""
    source: str = "seed"

    def to_dict(self) -> dict:
        return {"subject": self.subject, "connection": self.connection.value,
                "object": self.object, "basis": self.basis, "url": self.url,
                "source": self.source}


class WorldMap:
    """RVND's historical world-map container — an enum-edged graph of
    :class:`Entity` nodes. Construction and the RVND-surface queries live here;
    every reach *decision* is delegated whole to loomground-legal's
    :meth:`WorldMap.reach` (which folds the chain through the solver algebra), so
    no composition table, GOVERNING test, or left-fold lives in RVND. The
    RVND-KG ``project`` / ``dimensions_present`` methods are attached by
    ``workspaces.legal_world`` (they emit RVND's 5D pair-dict schema, not the
    package's concern)."""

    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.edges: List[WorldEdge] = []
        self._adj: dict[str, List[WorldEdge]] = {}

    # ── construction ──────────────────────────────────────────────────────────
    def add(self, e: Entity) -> Entity:
        self.entities[e.code] = e
        return e

    def connect(self, subject: str, connection: Any, obj: str, *,
                basis: str = "", url: str = "", source: str = "seed") -> WorldEdge:
        edge = WorldEdge(subject, connection, obj, basis=basis, url=url, source=source)
        self.edges.append(edge)
        self._adj.setdefault(subject, []).append(edge)
        return edge

    # ── queries ───────────────────────────────────────────────────────────────
    def get(self, code: str) -> Optional[Entity]:
        return self.entities.get(code)

    def neighbours(self, code: str) -> List[WorldEdge]:
        return list(self._adj.get(code, ()))

    def search(self, *, kind: Optional[EntityKind] = None,
               domain: Optional[str] = None,
               jurisdiction: Optional[str] = None) -> List[Entity]:
        out = []
        for e in self.entities.values():
            if kind is not None and e.kind is not kind:
                continue
            if domain is not None and domain not in e.domains:
                continue
            if jurisdiction is not None and e.jurisdiction != jurisdiction:
                continue
            out.append(e)
        return out

    def instruments_in(self, jurisdiction: str) -> List[Entity]:
        """Instruments that ``applies_in`` the given jurisdiction (the
        governing-law set)."""
        out = []
        for ed in self.edges:
            if ed.connection.value == "applies_in" and ed.object == jurisdiction:
                inst = self.entities.get(ed.subject)
                if inst is not None:
                    out.append(inst)
        return out

    def urls(self) -> List[dict]:
        """The retrievable corpus: every entity that carries a URL."""
        return [{"code": e.code, "kind": e.kind.value, "name": e.name,
                 "url": e.url, "domains": list(e.domains)}
                for e in self.entities.values() if e.url]

    # ── reach: delegated whole to the plane (no local fold) ────────────────────
    def reach(self, person: str, *, max_depth: int = 6) -> ReachResult:
        """Which legal orders govern ``person`` — delegated entirely to
        loomground-legal's ``WorldMap.reach``. The graph walk and every
        composition decision (``scope_applies`` → the solver ``RelationAlgebra``)
        are the plane's; this only translates RVND's enum-edged map to the
        package's string-edged one first."""
        return _to_pkg_map(self).reach(person, max_depth=max_depth)


# ── enum ⇄ string edge translation at the plane boundary ─────────────────────

def _to_pkg_map(world: "WorldMap") -> "_L.WorldMap":
    """RVND enum-edged map → the package's string-edged ``WorldMap`` (its native
    shape), so the consumed reach / validate mechanisms run unchanged."""
    pm = _L.WorldMap()
    for e in world.entities.values():
        pm.add(e)                                  # Entity is the package's own class
    for ed in world.edges:
        conn = ed.connection.value if hasattr(ed.connection, "value") else ed.connection
        pm.connect(ed.subject, conn, ed.object,
                   basis=ed.basis, url=ed.url, source=ed.source)
    return pm


def as_package_world(world: "WorldMap") -> "_L.WorldMap":
    """RVND's enum-edged ``WorldMap`` → the package's native string-edged
    ``WorldMap``. Consumers that run a package mechanism against the world graph
    directly — anchoring (:func:`anchor` / :func:`place_legal_text`) — resolve
    against the package's own shape, so this is the seam's public bridge for
    them (``reach`` / ``validate_corpus`` translate internally and need it not)."""
    return _to_pkg_map(world)


def _to_rvnd_map(pm: "_L.WorldMap") -> "WorldMap":
    """The package's string-edged map → RVND's enum-edged ``WorldMap`` (the
    historical surface every consumer reads)."""
    from ..legal_connection import Connection
    w = WorldMap()
    for e in pm.entities.values():
        w.add(e)
    for ed in pm.edges:
        w.connect(ed.subject, Connection(ed.connection), ed.object,
                  basis=ed.basis, url=ed.url, source=ed.source)
    return w


# ════════════════════════════════════════════════════════════════════════════
# world seed / corpus loaders / enrichment — inject RVND's env-configured ports
# ════════════════════════════════════════════════════════════════════════════

def seed_world() -> "WorldMap":
    """The digital-law seed corpus — consumed from the plane's packaged
    ``world_seed.json`` (data lifted verbatim from RVND), returned as RVND's
    enum-edged ``WorldMap``."""
    return _to_rvnd_map(_L.seed_world())


def reach(person: str, world: "WorldMap", *, max_depth: int = 6) -> ReachResult:
    """Free-function form of :meth:`WorldMap.reach`, delegated to the plane."""
    return world.reach(person, max_depth=max_depth)


def build_world(refdir=None) -> "WorldMap":
    """Build the full reference world from the md-table corpus (the plane's
    parser). ``refdir`` defaults to RVND's env-configured
    ``world_corpus_loader._default_refdir()`` — the resolver is RVND's, injected
    here; the parsing + code maps are the plane's."""
    if refdir is None:
        from ..world_corpus_loader import _default_refdir
        refdir = _default_refdir()
    return _to_rvnd_map(_L.build_world(refdir))


def load_instruments(csv_path=None) -> dict:
    """Load the companion instrument registry (CELEX → row). ``csv_path``
    defaults to RVND's env-configured ``regulatory_population.default_csv()`` —
    the resolver is RVND's, injected here; the CSV parse is the plane's. Raises
    ``FileNotFoundError`` (RVND's historical contract) when no corpus is present."""
    from pathlib import Path
    if csv_path is None:
        from ..regulatory_population import default_csv
        csv_path = default_csv()
    if csv_path is None or not Path(csv_path).exists():
        raise FileNotFoundError("instruments.csv not found; pass csv_path explicitly")
    return _L.load_instruments(csv_path)


def enrich(world: "WorldMap", *, instruments: Optional[dict] = None) -> dict:
    """Apply the plane's curated relational pass to a loaded world map. When
    ``instruments`` is not supplied, wire RVND's env-configured EU acquis
    registry — ``load_instruments(default_csv())`` — exactly as the retired RVND
    ``enrich`` did (missing corpus → empty, additive). The memberships, treaty
    bindings, adequacy, lineage and conformity DATA are the plane's; mutates
    ``world`` in place (enum edges) and returns the stats dict."""
    if instruments is None:
        try:
            instruments = load_instruments()
        except FileNotFoundError:
            instruments = {}
    from ..legal_connection import Connection
    pm = _to_pkg_map(world)
    stats = _L.enrich(pm, instruments=instruments)
    # reflect the enriched graph back into `world` in place, re-typed to enum
    world.entities.clear()
    world.edges.clear()
    world._adj.clear()
    for e in pm.entities.values():
        world.add(e)
    for ed in pm.edges:
        world.connect(ed.subject, Connection(ed.connection), ed.object,
                      basis=ed.basis, url=ed.url, source=ed.source)
    return stats


def validate_corpus(world: "WorldMap", *,
                    probe: Optional[Callable[[str], bool]] = None) -> dict:
    """Validate every entity in ``world`` — reachability / authority tier /
    currency / provenance — delegated to the plane's validator (over the
    package's string-edged map)."""
    return _L.validate_corpus(_to_pkg_map(world), probe=probe)
