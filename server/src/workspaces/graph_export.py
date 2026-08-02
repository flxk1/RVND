# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Graph export — the legal world as a force-graph the eye can walk.

One JSON for the graph view (``plugin/assets/workspace-graph.html``):

  nodes — every corpus entity (jurisdictions, instruments, regulators,
          standards bodies, contracts) + every span-norm (the per-article /
          per-clause dots), grouped for colouring;
  links — every world edge (TYPED, carrying its legal ``basis`` — the thing
          a wiki-link graph cannot say) + every span anchor (cites /
          governed_by / enforced_by) + contract party edges.

Groups mirror the layers a lawyer thinks in: jurisdiction / instrument /
regulator / standards / contract / clause — "per standard" is simply the
standards group plus its ``presumes_conformity`` edges into the instruments
it serves. Pure stdlib; rendering is the HTML's job.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .legal_corpus import EntityRegistry
from .rule_registry import RuleRegistry

# article numbers a norm names when citing another norm, EN + DE + §
_ART_REF = re.compile(r"(?:Articles?|Artikel|Art\.)\s*(\d+[a-z]?)|§\s*(\d+[a-z]?)")
_PIN_NUM = re.compile(r"(?:Art\.|Artikel|§)\s*(\d+[a-z]?)")

__all__ = ["export_graph", "export_graph_file"]

_GROUP_BY_KIND = {
    "state": "jurisdiction", "supranational": "jurisdiction",
    "international_regime": "jurisdiction",
    "instrument": "instrument", "regulator": "regulator",
    "standards_body": "standards", "contract": "contract",
    "legal_person": "party", "natural_person": "party", "public_body": "party",
}


def export_graph(folder, *, log_root: Optional[Path] = None,
                 max_clauses: int = 600,
                 focus: Optional[str] = None) -> dict[str, Any]:
    """``focus``: an entity code (e.g. ``gdpr``) → subgraph of that node, its
    direct neighbours, and every clause/article anchored to it — the
    per-statute view (one act exploded into its provisions)."""
    ents = EntityRegistry(folder, log_root=log_root)
    spans = RuleRegistry(folder, log_root=log_root)

    nodes: list[dict] = []
    links: list[dict] = []
    have: set[str] = set()

    for r in ents.entities.values():
        nid = r["code"]
        if nid in have:
            continue
        have.add(nid)
        nodes.append({
            "id": nid, "canonical_urn": r.get("canonical_urn"),
            "label": r.get("name") or nid,
            "group": _GROUP_BY_KIND.get(r["kind"], r["kind"]),
            "kind": r["kind"], "url": r.get("url"),
            "jurisdiction": r.get("jurisdiction"),
            "domains": r.get("domains", []),
            "source": r.get("source", "seed"),
        })
    for e in ents.edges.values():
        if e["subject"] in have and e["object"] in have:
            links.append({"source": e["subject"], "target": e["object"],
                          "kind": e["connection"], "basis": e.get("basis", "")})

    # span-norms: the per-article / per-clause dots, anchored into the map
    for i, rec in enumerate(spans.items.values()):
        if i >= max_clauses:
            break
        sid = rec["id"]
        norm = rec.get("norm") or {}
        span = rec.get("span") or {}
        doc = span.get("document", "")
        nodes.append({
            "id": sid,
            "label": (span.get("pinpoint") or sid[:12]),
            "group": "clause", "kind": rec.get("kind", "rule"),
            "document": doc,
            "modal": norm.get("modal", ""),
            "incident": norm.get("incident", ""),
            "text": (span.get("text") or "")[:200],
        })
        have.add(sid)
        # cluster clauses around their source contract when it is on the map
        doc_code = doc.rsplit(".", 1)[0].replace("_", "-") if doc else ""
        if doc_code in have:
            links.append({"source": sid, "target": doc_code,
                          "kind": "clause_of", "basis": doc})
        for a in rec.get("anchors", []):
            if a.get("entity") in have:
                links.append({"source": sid, "target": a["entity"],
                              "kind": a.get("relation", "cites"),
                              "basis": a.get("basis", "")})

    # ── norm-to-norm resolution (expressis-verbis cross-references) ──────────
    # A span citing "Article 33 of Regulation (EU) 2016/679" already carries a
    # cites-anchor to the INSTRUMENT (recognition is done at placement). When
    # the cited article is itself placed as a span, the reference resolves one
    # hop further: clause → clause, across acts. Deterministic and text-
    # grounded — the article number must literally occur in the citing span.
    art_index: dict[tuple, list[str]] = {}
    for rec in spans.items.values():
        for a in rec.get("anchors", []):
            if a.get("relation") != "cites":
                continue
            m = _PIN_NUM.match(a.get("basis") or "")
            if m:
                art_index.setdefault((a["entity"], m.group(1)),
                                     []).append(rec["id"])
    for rec in spans.items.values():
        if rec["id"] not in have:
            continue
        text = (rec.get("span") or {}).get("text") or ""
        nums = {g1 or g2 for g1, g2 in _ART_REF.findall(text)}
        if not nums:
            continue
        own = rec["id"]
        for a in rec.get("anchors", []):
            if a.get("relation") != "cites":
                continue
            ent = a["entity"]
            for num in nums:
                for target in art_index.get((ent, num), []):
                    if target != own and target in have:
                        links.append({"source": own, "target": target,
                                      "kind": "refers_to",
                                      "basis": f"expressis verbis: "
                                               f"Art. {num} ({ent})"})

    # ── layers: the USER'S data is the figure, the reference corpus is the
    # ground (principal's ruling 2026-06-05). user = what they ingested
    # (clauses, contracts, parties, user/ingest-sourced entities); context =
    # seeded entities their data directly touches (the GDPR node appears
    # because THEIR clause cites it); world = the rest of the map, faint and
    # off by default. Empty folder → everything is context, never a blank map.
    user_ids = {n["id"] for n in nodes
                if n["group"] in ("clause", "contract", "party")
                or n.get("source") in ("user", "ingest")}
    if user_ids:
        context_ids = set()
        for l in links:
            if l["source"] in user_ids and l["target"] not in user_ids:
                context_ids.add(l["target"])
            elif l["target"] in user_ids and l["source"] not in user_ids:
                context_ids.add(l["source"])
        # BRIDGES: the background's purpose is to CONNECT the user's norms —
        # legal hierarchy, shared orders, enforcement, lineage. A background
        # node tied to >= 2 distinct anchored entities is part of the
        # connective tissue between the user's norms and joins the context
        # (e.g. the EU node between GDPR and AI Act; the EDPB enforcing both).
        anchored = user_ids | context_ids
        touch: dict[str, set] = {}
        for l in links:
            s, t = l["source"], l["target"]
            if s not in anchored and t in anchored:
                touch.setdefault(s, set()).add(t)
            elif t not in anchored and s in anchored:
                touch.setdefault(t, set()).add(s)
        context_ids |= {nid for nid, hits in touch.items() if len(hits) >= 2}
        for n in nodes:
            n["layer"] = ("user" if n["id"] in user_ids
                          else "context" if n["id"] in context_ids else "world")
    else:
        for n in nodes:
            n["layer"] = "context"
    by_id = {n["id"]: n["layer"] for n in nodes}
    for l in links:
        a, b = by_id.get(l["source"], "world"), by_id.get(l["target"], "world")
        l["layer"] = ("user" if "user" in (a, b)
                      else "context" if "context" in (a, b) else "world")

    if focus:
        keep = {focus}
        for l in links:                       # 1-hop neighbours of the focus
            if l["source"] == focus:
                keep.add(l["target"])
            elif l["target"] == focus:
                keep.add(l["source"])
        # clauses anchored to the focus pull their own neighbours (regulator,
        # jurisdiction) in too, so the act keeps its enforcement context
        clause_ids = {l["source"] for l in links
                      if l["target"] == focus and l["source"].startswith("rule:")}
        for l in links:
            if l["source"] in clause_ids:
                keep.add(l["target"])
        nodes = [n for n in nodes if n["id"] in keep]
        links = [l for l in links if l["source"] in keep and l["target"] in keep]

    groups = sorted({n["group"] for n in nodes})
    return {"schema": "graph-1", "folder": str(folder),
            "focus": focus,
            "counts": {"nodes": len(nodes), "links": len(links)},
            "groups": groups, "nodes": nodes, "links": links}


def export_graph_file(folder, out_path, **kw) -> Path:
    p = Path(out_path)
    p.write_text(json.dumps(export_graph(folder, **kw), ensure_ascii=False),
                 encoding="utf-8")
    return p


def main() -> int:                                              # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description="export the legal graph view")
    ap.add_argument("folder")
    ap.add_argument("--out", default="graph-state.json")
    ap.add_argument("--seed", action="store_true",
                    help="seed the folder with the enriched world corpus first")
    ap.add_argument("--statute", help="path to a statute text file to ingest "
                                      "article-by-article (place_legal_text)")
    ap.add_argument("--code", help="instrument code for --statute (e.g. gdpr, ai-act)")
    ap.add_argument("--focus", help="export only this entity's subgraph "
                                    "(the per-statute view)")
    args = ap.parse_args()
    if args.seed:
        from .legal_corpus import seed_registry
        seed_registry(args.folder)
    if args.statute:
        if not args.code:
            ap.error("--statute requires --code")
        from .rule_registry import RuleRegistry
        text = Path(args.statute).read_text(encoding="utf-8")
        reg = RuleRegistry(args.folder)
        out = reg.place_legal_text(text, args.code,
                                   source_document=Path(args.statute).name)
        print(f"placed {out['count']} norms across {out['provisions']} provisions of {args.code}")
    out = export_graph_file(args.folder, args.out, focus=args.focus)
    print(f"graph → {out}")
    return 0


if __name__ == "__main__":                                      # pragma: no cover
    import sys
    sys.exit(main())
