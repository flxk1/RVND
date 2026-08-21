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


def _node_term(node: dict) -> str:
    """The human term a node names, for term-level edge addressing.

    Runtime-capture nodes (``versum.append_fact`` / ``append_inference``) mint
    opaque ``entity:<slug>:<digest>`` ids and carry the term in ``properties``
    (``bearer`` / ``name``); grammar-ingested nodes use the term as the id
    itself. Prefer the property term so ``workspace_query(subject="Acme")``
    matches a runtime fact remembered as "Acme"; fall back to the id.
    """
    props = node.get("properties") or {}
    return str(props.get("bearer") or props.get("name") or node.get("node_id") or "")


def _endpoint_term(endpoint: dict, id_to_term: dict[str, str]) -> str:
    """Resolve a relation endpoint to its term (node -> its term; literal -> value)."""
    value = endpoint.get("value")
    if endpoint.get("kind") == "node":
        return id_to_term.get(str(value), str(value))
    return str(value)


def dimensioned_edges(folder_context: str) -> list[Edge]:
    """Read workspace Versum relations as typed Solver edges.

    Endpoints are resolved to their human term (see :func:`_node_term`) so
    runtime facts written via ``versum.append_fact`` — whose endpoints are
    opaque ``entity:*`` node ids — are addressable by the same term the caller
    remembered. Grammar-ingested edges (term == node id) are unchanged.
    """
    root = Path(folder_context).expanduser().resolve() / ".versum"
    if not root.exists():
        return []
    edges = []
    for graph in load_dimensioned_subgraphs(root):
        id_to_term = {str(n["node_id"]): _node_term(n) for n in graph["nodes"]}
        for relation in graph["relations"]:
            props = relation.get("properties") or {}
            predicate = props.get("predicate") or relation["relation_type"]
            edges.append(Edge(
                subject=_endpoint_term(relation["source"], id_to_term),
                predicate=str(predicate),
                object=_endpoint_term(relation["target"], id_to_term),
                dimension=Dimension(relation["dimension"]),
                source_pair=str(graph["source"]["source_id"]),
            ))
    return edges


__all__ = ["dimensioned_edges"]
