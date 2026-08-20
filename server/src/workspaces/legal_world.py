# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-legal's world map — RVND owns neither.

RVND's parallel world-map stack is RETIRED. The entity model
(``Entity`` / ``EntityKind`` / ``WorldEdge``), the ``WorldMap`` graph container,
the multi-hop ``reach`` computation, and the digital-law seed corpus now live in
``loomground-legal`` and are consumed through the ``adapters/legal`` seam (the
workspaces boundary rule confines every upstream import there).

This module is a thin compatibility seam re-exporting that surface behind the
historical import names, so existing callers are unchanged. It carries **no
world model of its own** — no algebra, no seed data, no reach fold. The one
thing that stays RVND's is the **5D-KG projection**: ``WorldMap.project`` emits
RVND's pair-dict schema (nodes carrying URLs, edges carrying their connection's
5D dimension) that ``reasoning.py`` and the audit log consume — not the
package's concern. It is defined here and attached to the consumed ``WorldMap``
class, so a ``seed_world()`` map projects exactly as it always did.
"""
from __future__ import annotations

from .adapters.legal import (
    Entity,
    EntityKind,
    WorldEdge,
    WorldMap,
    GovEntry,
    ReachResult,
    JURISDICTION_KINDS,
    seed_world,
    reach,
)
from .legal_connection import dimension

__all__ = [
    "Entity", "EntityKind", "WorldEdge", "WorldMap", "GovEntry", "ReachResult",
    "JURISDICTION_KINDS", "seed_world", "reach",
]


# ── the RVND-KG projection — RVND's 5D pair-dict schema, kept local ───────────
# The world model, seed and reach are the package's; the projection into RVND's
# 5D knowledge-graph schema is RVND's alone. Defined here and attached to the
# consumed WorldMap so every map (incl. seed_world()) projects as it always did.

def _project(self) -> list[dict]:
    """Emit the map as dimensioned pair dicts (consumed by reasoning.py and the
    audit log). Each entity becomes a node carrying its URL; each edge carries
    its connection's 5D dimension."""
    adj_by: dict[str, list[dict]] = {}
    for ed in self.edges:
        adj_by.setdefault(ed.subject, []).append({
            "subject": f"entity:{ed.subject}", "predicate": ed.connection.value,
            "object": f"entity:{ed.object}", "dimension": dimension(ed.connection).value,
            "note": ed.basis})
    pairs = []
    for code, e in self.entities.items():
        nid = f"entity:{code}"
        pairs.append({
            "id": nid,
            "problem": {"id": f"{nid}-p", "scope": "legal-world",
                        "type": e.kind.value, "summary": e.name,
                        "facets": {"url": e.url, "domains": list(e.domains),
                                   "jurisdiction": e.jurisdiction,
                                   "region": e.region, "source": e.source,
                                   **(e.facets or {})}},
            "solution": {"id": nid, "problem_id": f"{nid}-p", "body": e.name,
                         "body_format": "kg-node", "authority_tier": 1,
                         "confidence": 1.0, "url": e.url},
            "edges": adj_by.get(code, []),
        })
    return pairs


def _dimensions_present(self) -> set[str]:
    return {dimension(ed.connection).value for ed in self.edges}


WorldMap.project = _project
WorldMap.dimensions_present = _dimensions_present
