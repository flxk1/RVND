# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Export Workspace knowledge graphs to visualiser-ready JSON.

The projections (`legal_kg.project`, `legal_world.WorldMap.project`) and the rule
registry already emit dimensioned pair/edge dicts. This module converts them into
the node/edge JSON the in-browser graph libraries consume — Cytoscape.js (focused,
feature-rich views) and Graphology (Sigma.js, large views) — preserving the **5D
dimension** on every edge as the styling/filter axis.

Pure stdlib. No rendering here; the HTML viewers embed the JSON this produces.
"""

from __future__ import annotations

from typing import Iterable

# 5D dimension → colour (shared by every viewer so the legend is stable)
DIMENSION_COLOR = {
    "structural":  "#2563eb",   # blue   — how it is built
    "causal":      "#dc2626",   # red    — what makes it bind
    "intentional": "#7c3aed",   # purple — what it is for
    "temporal":    "#16a34a",   # green  — when it governs
    "relational":  "#64748b",   # slate  — what it is linked to
}

# node kind → colour
KIND_COLOR = {
    "system": "#0f172a", "supranational": "#0f172a", "state": "#1e293b",
    "international_regime": "#334155", "regulator": "#b45309",
    "standards_body": "#0891b2", "instrument": "#a16207", "class": "#475569",
    "source": "#a16207", "legal_person": "#be185d", "rule": "#9333ea",
    "norm": "#9333ea", "kg-node": "#475569",
    # case-trace kinds (problem-solution graph): the walk through the nodes
    "question": "#0ea5e9", "fact": "#16a34a", "gap": "#dc2626",
    "schema_step": "#7c3aed", "reading": "#f59e0b", "resolution": "#0f172a",
    "subproblem": "#9333ea", "gap-bearing": "#dc2626",
}


# plain-language meaning of each reasoning dimension (for legends/tooltips)
DIMENSION_MEANING = {
    "structural":  "how the legal order is built — membership, hierarchy, composition",
    "causal":      "what brings the law to bear — application, transposition, citation",
    "intentional": "what a body or instrument is for — mandate, presumption of conformity",
    "temporal":    "when it governs — supersession, entry into force",
    "relational":  "what it is linked to — equivalence, correspondence",
}

# predicate slug → human-readable verb phrase
PRED_LABEL = {
    "member_of": "is a member of", "member-of": "is a member of",
    "has_primacy_over": "has primacy over", "outranks": "outranks",
    "applies_in": "applies in", "enforces": "enforces",
    "established_by": "established by", "established-by": "established by",
    "adopted_by": "adopted by", "party_to": "is party to", "bound_by": "is bound by",
    "equivalent_to": "adequacy / equivalent to", "supervises": "supervises",
    "supersedes": "supersedes (replaces)",
    "presumes_conformity": "raises a presumption of conformity for",
    "presumes-conformity": "raises a presumption of conformity for",
    "descends_from": "derives from", "descends-from": "derives from",
    "incorporates": "incorporates", "transposes": "transposes",
    "corresponds-to": "corresponds to", "corresponds_to": "corresponds to",
    "instance-of": "is a kind of", "instance_of": "is a kind of",
    "belongs-to": "belongs to", "belongs_to": "belongs to",
    "cites": "cites", "governed_by": "is governed by", "enforced_by": "is enforced by",
    "decomposes_to": "decomposes to",
    "feeds": "feeds", "conditions": "conditions", "requires": "requires",
}

# source-class slug → readable phrase
_CLASS_PRETTY = {
    "supranational_regulation": "EU Regulation (directly applicable)",
    "supranational_directive": "EU Directive (binds via transposition)",
    "supranational_primary": "EU primary law (Treaties / Charter)",
    "national_statute": "national statute", "national_regulation": "national regulation",
    "constitution": "constitution", "case_law": "case law",
    "international_treaty": "international treaty",
    "customary_international": "customary international law",
    "technical_standard": "technical standard", "soft_law": "soft law",
}


def _pretty(s: str) -> str:
    for k, v in _CLASS_PRETTY.items():
        s = s.replace(k, v)
    return s.replace("_", " ").replace(":", " — ")


def _humanize(nid: str) -> str:
    """Readable label for a node referenced only by id (e.g. 'entity:gdpr')."""
    return _pretty(nid.split(":", 1)[-1])


def _node(nid: str, label: str, kind: str, facets: dict | None = None) -> dict:
    return {"data": {"id": nid, "label": _pretty(label), "kind": kind,
                     "kind_label": kind.replace("_", " "),
                     "color": KIND_COLOR.get(kind, "#475569"),
                     "facets": facets or {}}}


def _edge(e: dict) -> dict:
    dim = e.get("dimension", "relational")
    pred = e["predicate"]
    return {"data": {"id": f"{e['subject']}|{pred}|{e['object']}",
                     "source": e["subject"], "target": e["object"],
                     "label": pred, "rel_label": PRED_LABEL.get(pred, pred.replace("_", " ")),
                     "dimension": dim, "dim_meaning": DIMENSION_MEANING.get(dim, ""),
                     "color": DIMENSION_COLOR.get(dim, "#64748b"),
                     "note": e.get("note", "")}}


def pairs_to_cytoscape(pairs: Iterable[dict]) -> dict:
    """Convert projection pairs (each with `problem`, `solution`, `edges`) to
    Cytoscape elements."""
    nodes, edges, seen = [], [], set()
    pairs = list(pairs)
    # nodes from the pairs themselves
    for p in pairs:
        nid = p["id"]
        if nid in seen:
            continue
        seen.add(nid)
        prob = p.get("problem", {})
        nodes.append(_node(nid, prob.get("summary", nid),
                           prob.get("type", "kg-node"), prob.get("facets")))
    # edges; create any endpoint nodes referenced but not emitted as a pair
    for p in pairs:
        for e in p.get("edges", []):
            for end in (e["subject"], e["object"]):
                if end not in seen:
                    seen.add(end)
                    label = end.split(":", 1)[-1]
                    nodes.append(_node(end, label, "kg-node"))
            edges.append(_edge(e))
    return {"nodes": nodes, "edges": edges}


def rule_items_to_cytoscape(items: Iterable[dict]) -> dict:
    """Convert rule-registry span-norms to a clause/norm → law anchor graph.
    Each norm becomes a node; each anchor becomes a dimensioned edge to its
    legal entity (cites→causal, governed_by→structural, enforced_by→intentional)."""
    rel_dim = {"cites": "causal", "governed_by": "structural",
               "enforced_by": "intentional", "corresponds_to": "relational"}
    nodes, edges, seen = [], [], set()
    for r in items:
        rid = r["id"]
        if rid not in seen:
            seen.add(rid)
            pin = r["span"].get("pinpoint") or ""
            modal = r["norm"].get("modal", "")
            label = f"{pin} [{modal}]" if pin else (r["span"]["text"][:40] + "…")
            nodes.append(_node(rid, label, "norm",
                               {"text": r["span"]["text"][:200], "modal": modal,
                                "pinpoint": pin}))
        for a in r.get("anchors", []):
            ent = a["entity"]
            if ent not in seen:
                seen.add(ent)
                nodes.append(_node(ent, ent, a.get("kind", "instrument")))
            dim = rel_dim.get(a["relation"], "relational")
            edges.append({"data": {
                "id": f"{rid}|{a['relation']}|{ent}", "source": rid, "target": ent,
                "label": a["relation"], "rel_label": PRED_LABEL.get(a["relation"], a["relation"].replace("_", " ")),
                "dimension": dim, "dim_meaning": DIMENSION_MEANING.get(dim, ""),
                "color": DIMENSION_COLOR.get(dim, "#64748b"),
                "note": a.get("basis", "")}})
    return {"nodes": nodes, "edges": edges}


def to_graphology(cyto: dict) -> dict:
    """Convert Cytoscape elements to a Graphology serialisation (Sigma.js)."""
    return {
        "nodes": [{"key": n["data"]["id"],
                   "attributes": {"label": n["data"]["label"],
                                  "color": n["data"]["color"], "size": 6}}
                  for n in cyto["nodes"]],
        "edges": [{"key": e["data"]["id"], "source": e["data"]["source"],
                   "target": e["data"]["target"],
                   "attributes": {"label": e["data"]["label"],
                                  "color": e["data"]["color"]}}
                  for e in cyto["edges"]],
    }


def legend() -> dict:
    """The shared 5D legend for any viewer."""
    return dict(DIMENSION_COLOR)


def _method_label(case: dict, inputs: dict) -> dict:
    """The Rule-ND method (FRMA / IRAC / Gutachten / generic-Toulmin) that
    governs a case's schema steps, read from its profile. Unknown or empty
    profile degrades to a plain 'generic' label — a labelling fact, never a
    crash."""
    from .reasoning_contract import PROFILES
    prof = (inputs.get("profile") or case.get("profile") or "").strip()
    spec = PROFILES.get(prof)
    if not spec:
        return {"profile": prof or "generic", "label": "generic"}
    return {"profile": prof, "label": spec["label"]}


def _subproblem_summary(payload: dict) -> dict:
    """Compact projection of one sub-case for a problem set: its rooms,
    whether it bears an open gap, and its resolution type."""
    case = payload.get("case") or {}
    if hasattr(case, "to_dict"):
        case = case.to_dict()
    inputs = payload.get("inputs") or {}
    receipted = {g.get("pinpoint", "") for g in (case.get("grounds") or [])}
    receipted.discard("")
    gaps = [g for g in (case.get("gaps") or []) if g]
    rooms = sorted({r for r in (inputs.get("rooms") or []) if r}
                   | receipted | set(gaps))
    return {
        "text": (case.get("problem") or {}).get("text", "sub-problem"),
        "rooms": rooms, "gaps": gaps,
        "receipted": sorted(receipted),
        "res_type": (case.get("resolution") or {}).get("type", "open"),
        "method": _method_label(case, inputs),
    }


def case_set_to_cytoscape(parent_payload: dict,
                          sub_payloads: list[dict]) -> dict:
    """Project a DECOMPOSED problem — a gate question and its sub-problems —
    as a set: the parent problem node fanning out (`decomposes_to`) to one
    node per sub-case. Each sub-problem carries its rooms, its method, and
    whether it bears an open gap (kind ``gap-bearing``) or is fully
    receipted (kind ``subproblem``). More nodes, the set structure explicit;
    the human still sees every gap. Pure projection, no store, no model."""
    parent = parent_payload.get("case") or {}
    if hasattr(parent, "to_dict"):
        parent = parent.to_dict()
    p_inputs = parent_payload.get("inputs") or {}
    p_text = (p_inputs.get("question")
              or (parent.get("problem") or {}).get("text") or "problem set")
    p_method = _method_label(parent, p_inputs)

    nodes = [_node("set:0", p_text[:80], "question",
                   {"role": "problem-set", "method": p_method["label"]})]
    edges: list[dict] = []

    for i, sp in enumerate(sub_payloads):
        s = _subproblem_summary(sp)
        nid = f"sub:{i}"
        kind = "gap-bearing" if s["gaps"] else "subproblem"
        nodes.append(_node(nid, s["text"][:60], kind,
                           {"rooms": s["rooms"], "gaps": s["gaps"],
                            "method": s["method"]["label"],
                            "resolution": s["res_type"]}))
        e = _edge({"subject": "set:0", "predicate": "decomposes_to",
                   "object": nid, "dimension": "structural",
                   "note": f"sub-problem #{i + 1} under {s['method']['label']}"
                           f" — {len(s['gaps'])} open gap(s)"})
        e["data"]["order"] = i
        edges.append(e)
    return {"nodes": nodes, "edges": edges}


def case_trace_to_cytoscape(payload: dict) -> dict:
    """Project ONE walker result as a trace graph — the task's actual path
    through the nodes: question → facts → norm rooms (receipted vs gap) →
    abstract schema → readings → the human boundary.

    Pure projection over the walker payload (case + inputs), no store, no
    model. Every edge carries its phase as the basis note and a strictly
    increasing ``order``, so a viewer can replay the walk step by step.
    Receipted rooms project as kind ``norm``; required rooms without a
    receipt as kind ``gap`` — the gap is part of the picture, never hidden.
    The resolution node names what the walk is WAITING for (ratify/decide/
    open); a trace never renders an answer the human has not closed."""
    case = payload.get("case") or {}
    if hasattr(case, "to_dict"):
        case = case.to_dict()
    inputs = payload.get("inputs") or {}

    question = ((inputs.get("question")
                 or (case.get("problem") or {}).get("text") or "?").strip())
    receipted = {g.get("pinpoint", "") for g in (case.get("grounds") or [])}
    receipted.discard("")
    gaps = [g for g in (case.get("gaps") or []) if g]
    rooms = sorted({r for r in (inputs.get("rooms") or []) if r}
                   | receipted | set(gaps))
    facts = case.get("facts") or []
    steps = case.get("chain") or []
    readings = inputs.get("readings") or []
    if not readings and (case.get("resolution") or {}).get("proposed"):
        readings = [case["resolution"]["proposed"]]
    res_type = (case.get("resolution") or {}).get("type", "open")
    res_label = {"residual": "awaiting human decision",
                 "determinate": "awaiting human ratification"}.get(
                     res_type, "OPEN — no reading closes the schema")

    nodes = [_node("q:0", question[:80], "question")]
    edges: list[dict] = []
    order = 0

    def _link(subject: str, predicate: str, obj: str, dim: str, note: str):
        nonlocal order
        e = _edge({"subject": subject, "predicate": predicate, "object": obj,
                   "dimension": dim, "note": note})
        e["data"]["order"] = order
        order += 1
        edges.append(e)

    for i, f in enumerate(facts):                                  # P2
        f = f if isinstance(f, dict) else f.to_dict()
        nid = f"fact:{i}"
        nodes.append(_node(nid, (f.get("text") or "fact")[:60], "fact",
                           {"source": f.get("source", "")}))
        _link("q:0", "evidenced_by", nid, "causal",
              "P2: evidenced fact with source receipt (R1)")
    for r in rooms:                                                # P3
        nid = f"room:{r}"
        if r in receipted:
            nodes.append(_node(nid, r, "norm"))
            _link("q:0", "examines", nid, "structural",
                  "P3: norm retrieved + receipted from the registry")
        else:
            nodes.append(_node(nid, r, "gap"))
            _link("q:0", "misses", nid, "structural",
                  "P3: required room WITHOUT receipt — honest gap")
    method = _method_label(case, inputs)
    prev = "q:0"
    for i, s in enumerate(steps):                                  # P4
        nid = f"step:{i}"
        label = f"{s.get('step', 'step')}: {s.get('text', '')}"[:70]
        nodes.append(_node(nid, label, "schema_step",
                           {"method": method["label"],
                            "profile": method["profile"]}))
        _link(prev, "frames" if prev == "q:0" else "then", nid,
              "intentional" if prev == "q:0" else "temporal",
              f"P4: abstract schema step under {method['label']} — "
              "the frame, never the outcome")
        prev = nid
    nodes.append(_node("res:0", res_label, "resolution",
                       {"type": res_type}))
    if readings:                                                   # P5
        for i, rd in enumerate(readings):
            rd = rd if isinstance(rd, dict) else {"label": str(rd)}
            nid = f"reading:{i}"
            nodes.append(_node(nid, (rd.get("label") or "reading")[:60],
                               "reading"))
            _link(prev, "lays_out", nid, "relational",
                  "P5: reading laid out, none preferred (R4)")
        for i in range(len(readings)):
            _link(f"reading:{i}", "awaits", "res:0", "temporal",
                  "human boundary: ratify/decide — the machine stops here")
    else:
        _link(prev, "stays", "res:0", "temporal",
              "no reading closes the schema — case stays OPEN (honest)")
    return {"nodes": nodes, "edges": edges}


def validate_graph(cyto: dict) -> dict:
    """Validate a graph for *structural* well-formedness and *provenance*
    completeness — the two things a human can check before trusting the picture.

    Structural: no dangling edge, every dimension known, every node labelled.
    Provenance: every edge carries a `basis` (a citable justification), and every
    corpus entity (instrument/regulator/standards body) carries a source URL.
    The substantive legal truth of each edge still needs a human — but this report
    tells you *which* claims are even checkable (have a basis) and which are bare."""
    ids = {n["data"]["id"] for n in cyto["nodes"]}
    findings: list[dict] = []
    for e in cyto["edges"]:
        d = e["data"]
        if d["source"] not in ids or d["target"] not in ids:
            findings.append({"kind": "dangling-edge", "id": d["id"]})
        if d.get("dimension") not in DIMENSION_COLOR:
            findings.append({"kind": "unknown-dimension", "id": d["id"], "value": d.get("dimension")})
        if not (d.get("note") or "").strip():
            findings.append({"kind": "edge-without-basis", "id": d["id"],
                             "rel": d.get("rel_label", d.get("label"))})
    for n in cyto["nodes"]:
        nd = n["data"]
        if not (nd.get("label") or "").strip():
            findings.append({"kind": "node-without-label", "id": nd["id"]})
        if nd["kind"] in ("instrument", "regulator", "standards_body") and not nd.get("facets", {}).get("url"):
            findings.append({"kind": "entity-without-source-url", "id": nd["id"]})
    ne = len(cyto["edges"]) or 1
    with_basis = ne - sum(1 for f in findings if f["kind"] == "edge-without-basis")
    return {
        "ok": not any(f["kind"] in ("dangling-edge", "unknown-dimension", "node-without-label")
                      for f in findings),
        "nodes": len(cyto["nodes"]), "edges": len(cyto["edges"]),
        "edges_with_basis_pct": round(100 * with_basis / ne, 1),
        "counts": {k: sum(1 for f in findings if f["kind"] == k)
                   for k in {f["kind"] for f in findings}},
        "findings": findings[:50],
    }
