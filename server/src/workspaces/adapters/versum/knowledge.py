# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Read-only RVND adapter over Versum's public knowledge-graph functions; internal by design.

RVND owns neither the graph model nor its persistence.  File names are contained in this
transitional adapter because Versum 0.1 does not yet expose a store facade; application
code receives typed snapshots and never reads ``.versum`` storage directly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from versum.store.graph import load_claims, load_concepts, load_edges
from versum.composition import load_compositions
from versum.nd import load_assignments, load_bindings
from versum.loomground import language_info


def versum_language_runtime() -> dict:
    """Return the Loomground language identity consumed by Versum."""
    return language_info()


@dataclass(frozen=True)
class VersumSnapshot:
    root: Path
    digest: str
    claims: tuple[dict, ...]
    concepts: tuple[dict, ...]
    edges: tuple[dict, ...]
    compositions: tuple[dict, ...] = ()
    assignments: tuple[dict, ...] = ()
    bindings: tuple[dict, ...] = ()


class VersumKnowledgeStore:
    """Stable RVND-facing view of one Versum folder index."""

    def __init__(self, folder) -> None:
        root = Path(folder).expanduser().resolve()
        self.root = root if root.name == ".versum" else root / ".versum"

    @property
    def available(self) -> bool:
        return (self.root / "claims.csv").is_file()

    @property
    def has_records(self) -> bool:
        """True if this folder holds ANY versum knowledge — curated claims OR
        runtime/file-ingest dimensioned-subgraph records.

        The ``available`` gate only sees the curated ``claims.csv`` projection.
        Runtime facts (``versum.append_fact``) and file ingests land in the
        dimensioned-subgraph transaction store instead, so the query/reason
        gates use this broader check to recognise a folder that knows things
        without a curated index.
        """
        if self.available:
            return True
        txns = self.root / "_dimensioned_subgraph_transactions"
        return txns.is_dir() and any(txns.glob("*.json"))

    def require(self) -> None:
        if not self.available:
            raise FileNotFoundError(
                f"Versum knowledge index not found at {self.root}; index the folder "
                "with loomground-versum before using knowledge operations"
            )

    @staticmethod
    def _load(path: Path, loader) -> list[dict]:
        return loader(path) if path.is_file() else []

    def claims(self, *, source_urn: Optional[str] = None) -> list[dict]:
        self.require()
        rows = self._load(self.root / "claims.csv", load_claims)
        if source_urn is not None:
            rows = [r for r in rows if r.get("source_urn") == source_urn or
                    r.get("canonical_urn") == source_urn]
        return rows

    def concepts(self, *, ids: Optional[Iterable[str]] = None) -> list[dict]:
        self.require()
        rows = self._load(self.root / "concepts.csv", load_concepts)
        if ids is not None:
            wanted = set(ids)
            rows = [r for r in rows if r.get("concept_id") in wanted]
        return rows

    def edges(self, *, node_ids: Optional[Iterable[str]] = None,
              dimensions: Optional[Iterable[str]] = None) -> list[dict]:
        self.require()
        rows = self._load(self.root / "semantic_edges.csv", load_edges)
        if node_ids is not None:
            wanted = set(node_ids)
            rows = [r for r in rows if r.get("src_id") in wanted or
                    r.get("dst_id") in wanted]
        if dimensions is not None:
            wanted_dims = set(dimensions)
            rows = [r for r in rows if (r.get("dimension") or "relational")
                    in wanted_dims]
        return rows

    def search(self, query, *, k: int = 10,
               filters: Optional[dict] = None) -> list[dict]:
        """Keyword/similarity search over this folder's ingested content, via the
        consumed versum search (``search_similar``) over the DimensionedSubgraph
        store the ingest writes. Additive read surface: the memory→versum read
        migration routes ``pairs_search`` here once the write paths populate
        versum for the remaining channels (until then memory stays the source for
        pair channels versum does not yet hold)."""
        if not self.root.is_dir():
            raise FileNotFoundError(
                f"Versum store not found at {self.root}; ingest the folder with "
                "loomground-ingest before searching")
        from versum.store.retrieve import from_dimensioned_store
        return from_dimensioned_store(self.root).search_similar(
            query, k=k, filters=filters)

    def snapshot(self) -> VersumSnapshot:
        claims = tuple(self.claims())
        concepts = tuple(self.concepts())
        edges = tuple(self.edges())
        compositions = tuple(row.to_dict() for row in load_compositions(
            self.root / "compositions.jsonl"))
        assignments = tuple(load_assignments(self.root / "nd" / "assignments.csv"))
        bindings = tuple(load_bindings(self.root / "nd" / "bindings.csv"))
        digest = hashlib.sha256()
        for name in ("claims.csv", "concepts.csv", "semantic_edges.csv",
                     "compositions.jsonl", "nd/assignments.csv", "nd/bindings.csv",
                     "fingerprints.json", "index.json"):
            path = self.root / name
            if path.is_file():
                digest.update(name.encode("utf-8"))
                digest.update(path.read_bytes())
        return VersumSnapshot(self.root, digest.hexdigest(), claims, concepts, edges,
                              compositions, assignments, bindings)

    def subgraph(self, focus: str, *, depth: int = 2,
                 dimensions: Optional[Iterable[str]] = None) -> dict:
        """Return a deterministic neighborhood without creating RVND graph state.

        Traversal is the solver's (:func:`neighborhood`); this adapter only maps
        the folder's dict-shaped edges into solver ``Edge``s and the reached node
        ids back to the folder's claim/concept records — RVND keeps no BFS."""
        from ..solver.reasoning import Edge, neighborhood
        from ..solver.dimensions import Dimension
        dict_edges = self.edges(dimensions=dimensions)
        edges = [
            Edge(subject=str(e.get("src_id") or ""),
                 predicate=str(e.get("predicate") or ""),
                 object=str(e.get("dst_id") or ""),
                 dimension=Dimension(e.get("dimension") or "relational"))
            for e in dict_edges
        ]
        reached = set(neighborhood(edges, focus, depth=depth)["nodes"])
        selected = [e for e in dict_edges
                    if e.get("src_id") in reached or e.get("dst_id") in reached]
        claims = [r for r in self.claims() if r.get("item_id") in reached]
        concepts = [r for r in self.concepts() if r.get("concept_id") in reached]
        return {
            "schema": "rvnd.versum.subgraph/v1",
            "focus": focus,
            "nodes": claims + concepts,
            "edges": selected,
            "snapshot": self.snapshot().digest,
        }
