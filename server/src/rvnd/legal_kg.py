# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Project the legal-system meta-layer into a 5D / ND knowledge graph.

The meta-layer (`legal_systems.py` packs + `source_classes.py` universal map)
holds the *facts* about a legal order: its source classes, their ranking, the
membership and incorporation relations. This module **projects** those facts
into the Workspace's own knowledge-graph form — the dimensioned pair/edge schema that
`reasoning.py` traverses and the mutation log records — so the legal system is a
first-class object in folder memory, reasoned over with the same 5D machinery as
any other domain, not a special case bolted on the side.

The projection is the bridge between two things that already exist:

  meta-layer relation   →   5D reasoning dimension
  ───────────────────       ─────────────────────
  member_of, outranks       STRUCTURAL    (how the legal order is built)
  incorporates, transposes  CAUSAL        (what makes a source bind the facts)
  presumes_conformity       INTENTIONAL   (what a standard is *for*)
  supersedes                TEMPORAL      (which instrument governs when)
  corresponds-to            RELATIONAL    (the same kind of source across systems)

All five dimensions appear, so the result is a genuine 5D graph (traversable by
`reasoning.compose_paths`), not a one-dimensional hierarchy. TEMPORAL edges are
instrument-level and therefore only emitted when real instrument supersession
data is supplied (kept honest: the meta-layer alone knows class structure, not
which instrument repealed which — that is the vertical's `instruments.csv`).

Output is a list of pair dicts identical in shape to `domain_nds._build_pairs`,
so it flows into `reasoning.extract_edges`, the audit log, and retrieval with no
adapter. Pure: building the graph touches no storage; ingesting the pairs into a
folder is a separate, logged step.

Internal by design: world-building machinery consumed by validators and routers; not operator-queryable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from rvnd.adapters.solver.dimensions import Dimension
from .source_classes import Effect, Relation, SourceClass, max_effect
from . import legal_systems as ls


# ── relation → dimension (the projection rule) ────────────────────────────────

RELATION_DIMENSION: dict[Relation, Dimension] = {
    Relation.MEMBER_OF:           Dimension.STRUCTURAL,
    Relation.OUTRANKS:            Dimension.STRUCTURAL,
    Relation.INCORPORATES:        Dimension.CAUSAL,
    Relation.TRANSPOSES:          Dimension.CAUSAL,
    Relation.PRESUMES_CONFORMITY: Dimension.INTENTIONAL,
    Relation.SUPERSEDES:          Dimension.TEMPORAL,
}

# Effect → audit authority tier (1 = strongest).
_TIER = {Effect.BINDING: 1, Effect.PRESUMPTION: 3,
         Effect.INTERPRETIVE: 4, Effect.PERSUASIVE: 5}


def _sys_id(code: str) -> str:        return f"legal-system:{code}"
def _src_id(code: str, c: SourceClass) -> str:  return f"source:{code}:{c.value}"
def _cls_id(c: SourceClass) -> str:   return f"class:{c.value}"
def _inst_id(celex: str) -> str:      return f"instrument:{celex}"


@dataclass
class LegalKG:
    """A projected legal-system graph: nodes + reasoning-ready pairs."""
    systems: tuple[str, ...]
    nodes: list[dict] = field(default_factory=list)
    pairs: list[dict] = field(default_factory=list)

    def edges(self) -> list[dict]:
        out: list[dict] = []
        for p in self.pairs:
            out.extend(p.get("edges", []))
        return out

    def dimensions_present(self) -> set[str]:
        return {e["dimension"] for e in self.edges()}

    def to_dict(self) -> dict:
        return {"systems": list(self.systems), "nodes": self.nodes,
                "pairs": self.pairs, "edge_count": len(self.edges()),
                "dimensions": sorted(self.dimensions_present())}


class _Builder:
    def __init__(self) -> None:
        self._nodes: dict[str, dict] = {}
        self._edges: dict[str, list[dict]] = {}   # subject id → outgoing edges
        self._tier: dict[str, int] = {}

    def node(self, nid: str, kind: str, label: str, facets: dict,
             tier: int = 1) -> str:
        if nid not in self._nodes:
            self._nodes[nid] = {"id": nid, "kind": kind, "label": label,
                                "facets": facets}
            self._tier[nid] = tier
        return nid

    def edge(self, subj: str, predicate: str, obj: str, dim: Dimension,
             note: str = "") -> None:
        e = {"subject": subj, "predicate": predicate, "object": obj,
             "dimension": dim.value}
        if note:
            e["note"] = note
        self._edges.setdefault(subj, []).append(e)

    def build(self, systems: tuple[str, ...]) -> LegalKG:
        pairs: list[dict] = []
        for nid, n in self._nodes.items():
            pairs.append({
                "id": nid,
                "problem": {"id": f"{nid}-p", "scope": "legal-system",
                            "type": n["kind"], "summary": n["label"],
                            "facets": n["facets"]},
                "solution": {"id": nid, "problem_id": f"{nid}-p",
                             "body": n["label"], "body_format": "kg-node",
                             "authority_tier": self._tier[nid], "confidence": 1.0},
                "edges": self._edges.get(nid, []),
            })
        return LegalKG(systems=systems, nodes=list(self._nodes.values()),
                       pairs=pairs)


def project(code: Optional[str] = None, *,
            instruments: Optional[list[dict]] = None,
            include_overlay: bool = True) -> LegalKG:
    """Project the legal system selected by ``code`` (default DE) — and, unless
    ``include_overlay=False``, the supranational orders it belongs to — into a
    5D/ND knowledge graph.

    ``instruments`` (optional) supplies instrument-level temporal facts so the
    graph carries TEMPORAL supersedes edges. Each row:
        {"celex": str, "label": str, "source_class": "supranational_regulation",
         "supersedes": "<celex>"|None, "system": "EU"|None}
    """
    code = (code or ls.DEFAULT).upper()
    systems = tuple(ls.applicable_systems(code)) if include_overlay else (code,)
    b = _Builder()

    # 1. system nodes
    for s in systems:
        sys = ls.get(s)
        b.node(_sys_id(s), "system", sys.name,
               {"code": s, "family": sys.family, "version_label": sys.version_label},
               tier=1)

    # 2. per-system source nodes + their structural edges
    seen_classes: dict[SourceClass, list[str]] = {}
    for s in systems:
        sys = ls.get(s)
        ranked = list(sys.class_rank)
        extra = [c for c, _ in sys.incorporation if c not in ranked]
        for cls in ranked + extra:
            sid = _src_id(s, cls)
            eff = max_effect(cls)
            b.node(sid, "source", f"{sys.name}: {cls.value}",
                   {"system": s, "source_class": cls.value, "effect": eff.name,
                    "self_executing": ls.self_executes(cls, sys.self_executing_extra),
                    "incorporation_rule": sys.incorporation_rule(cls)},
                   tier=_TIER[eff])
            # universal taxonomy concept + instance-of (STRUCTURAL)
            b.node(_cls_id(cls), "class", cls.value,
                   {"ceiling": eff.name}, tier=_TIER[eff])
            b.edge(sid, "instance-of", _cls_id(cls), Dimension.STRUCTURAL)
            # belongs-to its system (STRUCTURAL)
            b.edge(sid, "belongs-to", _sys_id(s), Dimension.STRUCTURAL)
            seen_classes.setdefault(cls, []).append(s)

        # within-system hierarchy: rank_i outranks rank_{i+1} (STRUCTURAL)
        for a, bb in zip(ranked, ranked[1:]):
            b.edge(_src_id(s, a), "outranks", _src_id(s, bb), Dimension.STRUCTURAL,
                   note="hierarchy within " + s)

        # incorporation / presumption edges (CAUSAL / INTENTIONAL)
        for cls, rule in sys.incorporation:
            sid = _src_id(s, cls)
            if cls in (SourceClass.INTERNATIONAL_TREATY,
                       SourceClass.CUSTOMARY_INTERNATIONAL):
                b.edge(_sys_id(s), "incorporates", sid, Dimension.CAUSAL, note=rule)
            elif cls is SourceClass.TECHNICAL_STANDARD:
                b.edge(sid, "presumes-conformity", _sys_id(s),
                       Dimension.INTENTIONAL, note=rule)
            # directive transposition handled cross-system below

    # 3. cross-system relations from the resolver (member_of, primacy/outranks)
    al = ls.applicable_law(code) if include_overlay else ls.applicable_law(code, in_scope=set())
    for r in al.relations:
        b.edge(_sys_id(r.subject), r.relation.value.replace("_", "-"),
               _sys_id(r.object), RELATION_DIMENSION[r.relation], note=r.note)

    # 4. directive transposition (CAUSAL): a member's national statute transposes
    #    the overlay's directive.
    for s in systems:
        sys = ls.get(s)
        if SourceClass.SUPRANATIONAL_DIRECTIVE not in sys.class_rank:
            continue
        directive = _src_id(s, SourceClass.SUPRANATIONAL_DIRECTIVE)
        for m in systems:
            if s in ls.get(m).supranational_overlay:   # m is a member of s
                b.edge(_src_id(m, SourceClass.NATIONAL_STATUTE), "transposes",
                       directive, Dimension.CAUSAL,
                       note="national statute transposes the directive (Art. 288 TFEU)")

    # 5. cross-system correspondence (RELATIONAL): same class in >1 system
    for cls, where in seen_classes.items():
        for x, y in zip(sorted(where), sorted(where)[1:]):
            b.edge(_src_id(x, cls), "corresponds-to", _src_id(y, cls),
                   Dimension.RELATIONAL, note="same source kind across systems")

    # 6. optional instrument-level TEMPORAL supersedes
    for row in (instruments or []):
        celex = row.get("celex")
        if not celex:
            continue
        iid = _inst_id(celex)
        b.node(iid, "instrument", row.get("label", celex),
               {"celex": celex, "source_class": row.get("source_class"),
                "in_force_from": row.get("in_force_from")},
               tier=1)
        sc = row.get("source_class")
        if sc:
            try:
                b.edge(iid, "instance-of", _cls_id(SourceClass(sc)),
                       Dimension.STRUCTURAL)
            except ValueError:
                pass
        sup = row.get("supersedes")
        if sup:
            b.node(_inst_id(sup), "instrument", sup, {"celex": sup}, tier=1)
            b.edge(iid, "supersedes", _inst_id(sup), Dimension.TEMPORAL,
                   note="later instrument governs from its application date")

    return b.build(systems)
