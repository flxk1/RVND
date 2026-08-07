# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Project Versum knowledge into Solver without coupling either upstream package; internal by design."""

from __future__ import annotations

from loomground_solver.dimensions import Dimension
from loomground_solver.reasoning import Edge, compose_paths

from .knowledge import VersumKnowledgeStore


class VersumSolverSource:
    def __init__(self, store: VersumKnowledgeStore) -> None:
        self.store = store

    def edges(self) -> list[Edge]:
        out = []
        # Runtime facts / file-ingest relations live in the dimensioned-subgraph
        # store (written by versum.append_fact / ingest_into_versum); curated
        # relations live in semantic_edges.csv. Union both so reasoning sees the
        # whole knowledge plane. Terms are already resolved by dimensioned_edges.
        from ..solver.versum import dimensioned_edges
        out.extend(dimensioned_edges(str(self.store.root.parent)))
        # Curated semantic edges are optional: a runtime-only folder has the
        # dimensioned store but no claims.csv, and store.edges() would raise.
        semantic_rows = self.store.edges() if self.store.available else []
        for row in semantic_rows:
            dimension = row.get("dimension") or "relational"
            try:
                dim = Dimension(dimension)
            except ValueError as exc:
                raise ValueError(
                    f"Versum edge {row.get('edge_id')!r} has unsupported 5D "
                    f"dimension {dimension!r}"
                ) from exc
            try:
                weight = float(row.get("confidence") or 1.0)
            except (TypeError, ValueError):
                weight = 1.0
            out.append(Edge(
                subject=str(row.get("src_id") or ""),
                predicate=str(row.get("edge_type") or "related"),
                object=str(row.get("dst_id") or ""),
                dimension=dim,
                weight=max(0.0, min(1.0, weight)),
                source_pair=str(row.get("edge_id") or ""),
            ))
        return out

    def paths(self, *, start: str | None = None, max_depth: int = 3,
              min_confidence: float = 0.0, max_results: int = 200):
        return compose_paths(
            self.edges(), start=start, max_depth=max_depth,
            min_confidence=min_confidence, max_results=max_results,
        )

    def observation(self) -> dict:
        snap = self.store.snapshot()
        return {
            "schema": "reasoning.interop.versum/v1",
            "producer": "loomground-versum",
            "snapshot": snap.digest,
            "claims": list(snap.claims),
            "concepts": list(snap.concepts),
            "edges": list(snap.edges),
            "compositions": list(snap.compositions),
            "assignments": list(snap.assignments),
            "bindings": list(snap.bindings),
        }
