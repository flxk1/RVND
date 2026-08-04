# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND governance overlay projected over upstream knowledge/reasoning contracts.

NOT instrument-specific: nodes are universal kinds (``instrument · role · room · rule · obligation
· gate · artifact``) and edges carry the five reasoning dimensions (``dimensions.Dimension``), so
the SAME graph shape fits any policy — GDPR, the AI Act, a national statute. Three semantic-zoom
levels let the (necessarily complex) view zoom OUT: ``overview`` (instruments × roles) → ``cluster``
(rooms + roles as super-nodes) → ``detail`` (individual rules + their edges, ego-focused). And
``path`` composes a REASONING path between two nodes (``reasoning``/`dimensions` compose), carrying
the ordered edges as provenance — the auditable "why".

This module owns no knowledge persistence and no 5D algebra. It serialises an ephemeral
RVND-specific view in the ``kg_export`` Cytoscape shape; the dimension vocabulary and path
composition are supplied by Solver, while durable knowledge is supplied by Versum.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Optional

from workspaces.adapters.solver.dimensions import Dimension
from workspaces.adapters.solver.reasoning import Edge, compose_paths

SCHEMA_VERSION = "governance_kg/v1"
KINDS = ("instrument", "role", "room", "rule", "obligation", "gate", "artifact")
LEVELS = ("overview", "cluster", "detail")

# the artifact a demand requires (what SATISFIES it) — the node the obligation points at, which is
# what evidence_coverage places documents into.
_ARTIFACT = {
    "disclosure": "disclosure notice", "record": "document / record",
    "management_system": "process + owner", "assessment": "assessment",
    "oversight": "reviewer + control form", "technical_measure": "configured measure",
    "appointment": "named party", "registration_notification": "registration record",
    "guard": "attestation of absence",
}


def _f(r: Any, name: str, default: Any = None) -> Any:
    return r.get(name, default) if isinstance(r, dict) else getattr(r, name, default)


def project(rules: Iterable[Any], *, level: str = "cluster", focus: Optional[str] = None,
            dimensions: Optional[Iterable[str]] = None, demand_as: str = "node") -> dict[str, Any]:
    """Project map rows into a universal KG at a zoom ``level``. ``focus`` = a rule_id to centre
    the ``detail`` view on (its neighbourhood). ``dimensions`` filters edges (else all five).

    ``demand_as`` is the semantic-zoom collapse for the 9 demand kinds: ``"node"`` (default,
    zoomed-in) REIFIES the demand as an ``obligation`` node — it carries CTA/overlay, so it needs a
    node — pointing at the required ``artifact`` it is *satisfied-by*; ``"edge"`` (zoomed-out)
    COLLAPSES it to the label on a single ``Rule →[demand]→ Artifact`` edge (no obligation node)."""
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}; one of {LEVELS}")
    dims = set(dimensions) if dimensions else None
    rows = list(rules)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node(nid: str, kind: str, label: str, **attrs: Any) -> None:
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, **attrs})

    def edge(src: str, dst: str, dim: Dimension, label: str) -> None:
        if dims is None or dim.value in dims:
            edges.append({"source": src, "target": dst, "dimension": dim.value, "label": label})

    if level == "overview":                                  # instruments × roles, weighted
        for (inst, role), n in Counter((_f(r, "instrument", "?"), _f(r, "role") or "(any)")
                                       for r in rows).items():
            node("inst:" + inst, "instrument", inst)
            node("role:" + role, "role", role)
            edge("inst:" + inst, "role:" + role, Dimension.RELATIONAL, f"{n} rules")
        return _wrap(level, nodes, edges, focus, dims)

    if level == "cluster":                                   # rooms + roles as super-nodes
        for room, n in Counter(_f(r, "room", "Unassigned") for r in rows).items():
            node("room:" + room, "room", room, count=n)
        for role, n in Counter(_f(r, "role") or "(any)" for r in rows).items():
            node("role:" + role, "role", role, count=n)
        seen: set = set()
        for r in rows:
            k = (_f(r, "room", "Unassigned"), _f(r, "role") or "(any)")
            if k not in seen:
                seen.add(k)
                edge("room:" + k[0], "role:" + k[1], Dimension.RELATIONAL, "")
        return _wrap(level, nodes, edges, focus, dims)

    # detail — individual rules + their neighbours (ego-filtered when focus is set)
    if focus:
        rows = [r for r in rows if _f(r, "rule_id") == focus] or rows
    for r in rows:
        rid = _f(r, "rule_id", "?")
        node(rid, "rule", (str(_f(r, "pinpoint", "")) + " " + str(_f(r, "duty", "") or "")).strip()[:44],
             risk=_f(r, "risk_tier"))
        inst = _f(r, "instrument", "?")
        node("inst:" + inst, "instrument", inst); edge(rid, "inst:" + inst, Dimension.STRUCTURAL, "part-of")
        if _f(r, "role"):
            node("role:" + _f(r, "role"), "role", _f(r, "role")); edge(rid, "role:" + _f(r, "role"), Dimension.RELATIONAL, "bearer")
        if _f(r, "room"):
            node("room:" + _f(r, "room"), "room", _f(r, "room")); edge(rid, "room:" + _f(r, "room"), Dimension.STRUCTURAL, "grouped")
        dem = _f(r, "demand_type")
        if dem:
            art_id, art_label = "artifact:" + dem, _ARTIFACT.get(dem, dem)
            node(art_id, "artifact", art_label)
            if demand_as == "edge":
                # collapsed (zoomed-out): the demand is the LABEL on the rule→artifact edge
                edge(rid, art_id, Dimension.INTENTIONAL, dem)
            else:
                # reified (zoomed-in): the demand is an obligation NODE (carries CTA/overlay),
                # satisfied-by the artifact it points at
                node("demand:" + dem, "obligation", dem)
                edge(rid, "demand:" + dem, Dimension.INTENTIONAL, "demands")
                edge("demand:" + dem, art_id, Dimension.CAUSAL, "satisfied-by")
        if _f(r, "gate_id"):
            node(_f(r, "gate_id"), "gate", _f(r, "gate_id")); edge(rid, _f(r, "gate_id"), Dimension.STRUCTURAL, "enforced-by")
    return _wrap(level, nodes, edges, focus, dims, demand_as=demand_as)


def _wrap(level: str, nodes: dict, edges: list, focus: Optional[str], dims: Optional[set],
          demand_as: Optional[str] = None) -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "level": level, "focus": focus, "demand_as": demand_as,
            "dimensions": sorted(dims) if dims else "all",
            "kinds": sorted({n["kind"] for n in nodes.values()}),
            "nodes": list(nodes.values()), "edges": edges}


def path(rules: Iterable[Any], src: str, dst: str) -> dict[str, Any]:
    """A reasoning path from ``src`` to ``dst`` over the detail graph — the ordered
    edges (provenance) + the composed dimension. Composition is delegated to the
    consumed solver (:func:`compose_paths`); RVND keeps no path-finder of its own.
    A direct edge is a fact, not a derivation, so it is returned as a single hop;
    a longer connection is composed by the solver over both edge directions (the
    governance graph reads undirected for this query)."""
    g = project(rules, level="detail")
    for e in g["edges"]:                                     # a direct edge is a fact
        if {e["source"], e["target"]} == {src, dst}:
            return {"version": SCHEMA_VERSION, "from": src, "to": dst, "hops": 1,
                    "edges": [e], "dimension_chain": [e["dimension"]],
                    "overall_dimension": e["dimension"]}
    edges: list[Edge] = []
    for e in g["edges"]:
        dim = Dimension(e["dimension"])
        pred = e.get("kind") or e.get("predicate") or "linked"
        edges.append(Edge(subject=e["source"], predicate=pred, object=e["target"], dimension=dim))
        edges.append(Edge(subject=e["target"], predicate=pred, object=e["source"], dimension=dim))
    for inf in compose_paths(edges, start=src, max_depth=8):   # best-confidence first
        if inf.object == dst:
            return {"version": SCHEMA_VERSION, "from": src, "to": dst, "hops": inf.hops,
                    "edges": inf.path, "dimension_chain": inf.dimension_chain,
                    "overall_dimension": inf.dimension.value}
    return {"version": SCHEMA_VERSION, "from": src, "to": dst, "hops": 0, "edges": [],
            "reason": "no path"}
