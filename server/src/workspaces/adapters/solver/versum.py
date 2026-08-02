# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Convert Versum's public dimensioned-subgraph read surface to Solver edges.

Internal by design: RVND exposes the resulting reasoning through its governed
tools; this module is the package-boundary adapter, not an additional surface.
"""
from __future__ import annotations

from pathlib import Path

from ..versum import load_dimensioned_subgraphs
from .dimensions import Dimension
from .reasoning import Edge


def dimensioned_edges(folder_context: str) -> list[Edge]:
    """Read workspace Versum relations as typed Solver edges."""
    root = Path(folder_context).expanduser().resolve() / ".versum"
    if not root.exists():
        return []
    edges = []
    for graph in load_dimensioned_subgraphs(root):
        for relation in graph["relations"]:
            edges.append(Edge(
                subject=str(relation["source"]["value"]),
                predicate=str(relation["relation_type"]),
                object=str(relation["target"]["value"]),
                dimension=Dimension(relation["dimension"]),
                source_pair=str(graph["source"]["source_id"]),
            ))
    return edges


__all__ = ["dimensioned_edges"]
