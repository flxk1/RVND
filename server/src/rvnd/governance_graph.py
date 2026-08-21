# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""governance_graph — project the governed-execution world as a patch.

The "patch" (Loomground): a read-only graph assembled from the signed chain so a
canvas can auto-build instead of re-deriving rules in the client. Three element
kinds:

  * nodes  — parties (agent/human), use cases, and the single master (the egress
    boundary, the one place the system touches the world);
  * edges  — authority cords (agent -> use case, from the use case's allowed
    list) and egress cords (use case -> master, carrying the run verdict);
  * verdicts — per use case, the strictest disposition of its latest run
    (reserved > refused > human > auto). Decided here, on the server, from the
    journal — never in the client.

Pure projection: no writes, no model calls. Everything it returns is already in
the chain (parties, use cases, runs). verify_chain proves the source intact.
"""
from __future__ import annotations

from typing import Any, Optional

from .parties import list_parties
from .use_case import list_use_cases
from .operations import runs_for
from .step_contract import risk_grade_cap

# Strictest-wins precedence: what actually governs the edge to the world.
from .adapters.policy_languages import verdict_order as _verdict_order

# restrictiveness rank consumed from governance's verdict grammar (adds
# 'prohibited' the local table had omitted — the worst verdict now ranks worst).
_RANK = {v: i + 1 for i, v in enumerate(_verdict_order())}
_BY_RANK = {v: k for k, v in _RANK.items()}


def _latest_run_by_use_case(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Last run wins per use case (runs_for is in chain order)."""
    out: dict[str, dict[str, Any]] = {}
    for run in runs:
        uid = run.get("use_case_id") or ""
        if uid:
            out[uid] = run
    return out


def _verdict_for_run(run: dict[str, Any]) -> dict[str, Any]:
    """Strictest disposition + a per-disposition tally for one run."""
    if run.get("final") == "refused":
        return {"verdict": "refused", "final": "refused",
                "reason": run.get("reason", ""), "run_id": run.get("run_id"),
                "dispositions": {}}
    tally: dict[str, int] = {}
    worst = 0
    for step in run.get("steps", []):
        d = step.get("disposition", "")
        if d:
            tally[d] = tally.get(d, 0) + 1
            worst = max(worst, _RANK.get(d, 0))
    verdict = _BY_RANK.get(worst, "auto") if worst else "auto"
    return {"verdict": verdict, "final": run.get("final", ""),
            "run_id": run.get("run_id"), "dispositions": tally}


def governance_graph(
    folder_context: str,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the patch: {nodes, edges, verdicts, summary}. Read-only."""
    parties = list_parties(folder_context, log_root=log_root)
    if isinstance(parties, dict):                 # list_parties returns {parties:[...]}
        parties = parties.get("parties", parties.get("items", []))
    use_cases = list_use_cases(folder_context, log_root=log_root)
    runs = runs_for(folder_context, log_root=log_root)
    latest = _latest_run_by_use_case(runs)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    verdicts: dict[str, Any] = {}

    n_agents = n_humans = 0
    for pt in parties:
        pid = pt.get("party_id") or pt.get("id") or ""
        if not pid:
            continue
        kind = pt.get("party_kind") or pt.get("kind") or "agent"
        if kind == "human":
            n_humans += 1
        else:
            n_agents += 1
        nodes.append({
            "id": f"party:{pid}", "kind": kind,
            "agent_uid": pt.get("agent_uid", "") if kind == "agent" else "",
            "label": pt.get("name") or pid,
            "status": pt.get("status", "active"),
            "grade": pt.get("grade", ""),
            "role": pt.get("role", ""),
        })

    # The one world-touch. Always present: the patch always has a boundary.
    nodes.append({"id": "master", "kind": "master",
                  "label": "egress boundary",
                  "note": "the only node that touches the world"})

    # Data-lineage tags per use case = connector-DERIVED (channels linked to the uc
    # stamp their declared tags) ∪ user-AUTHORED (read below). Built once up front so
    # each use_case node can carry the union the run-path's tag data-lens sees. Neutral
    # facts; the guards that act on them are authored/ingested policy. Fail toward the
    # graph if the connector store is unreadable — tags are additive, never a gate here.
    _conn_tags: dict[str, set] = {}
    try:
        from .connectors import list_connectors as _lc
        for _c in _lc(folder_context, log_root=log_root):
            for _u in (_c.get("use_cases") or []):
                _conn_tags.setdefault(_u, set()).update(_c.get("tags") or [])
    except (OSError, ValueError):
        pass

    n_reserved_uc = 0
    for uc in use_cases:
        uid = uc.get("use_case_id") or ""
        if not uid:
            continue
        contract = uc.get("contract") or {}
        reserved = uc.get("reserved_acts") or []
        prohibited = bool(uc.get("prohibited"))
        if reserved:
            n_reserved_uc += 1
        # The SERVER-composed autonomy ceiling for this risk (0..4), so the UI
        # renders it instead of recomputing a risk→cap map client-side. A
        # reserved act caps autonomy at human-in-the-loop (L2 — a person must sign);
        # a prohibited act caps at L0 (severed). The strictest of these wins.
        ceiling = risk_grade_cap(uc.get("risk", ""))
        if reserved:
            ceiling = min(ceiling, 2)
        if prohibited:
            ceiling = 0
        # Oversight mode — the human-involvement the policy ALREADY bound, synthesized
        # from the same server-decided data this node carries (reservations + composed
        # ceiling + prohibited). NOT recomputed via a model call (the projection
        # contract forbids that): who must act (reserved_to), the dial (review), and the
        # autonomy cap (grade_ceiling). The console renders this badge; it composes nothing.
        _overseers = sorted({a.get("reserved_to", "") for a in reserved if a.get("reserved_to")})
        if prohibited:
            _mode = "severed"
        elif reserved:
            _mode = "human decision"      # a named role must act (review) before egress
        elif ceiling <= 2:
            _mode = "human-in-the-loop"   # a person must sign (L2 cap)
        elif ceiling == 3:
            _mode = "on-the-loop"
        else:
            _mode = "autonomous"
        oversight = {"mode": _mode, "level": "REVIEW" if reserved else "",
                     "overseers": _overseers, "grade_ceiling": ceiling}
        nodes.append({
            "id": f"uc:{uid}", "kind": "use_case",
            "label": uc.get("name") or uid,
            "risk": uc.get("risk", ""),
            "issue_type": (uc.get("fingerprint") or {}).get("issue_type", ""),
            "grade": contract.get("grade", 0),
            "grade_ceiling": ceiling,
            "oversight": oversight,
            "prohibited": prohibited,
            "contract_id": uc.get("contract_id", ""),
            "reserved": [a.get("act_type", "") for a in reserved],
            # Provenance, attributed — the client must NEVER print "by law" as
            # Rvnd's own finding. Each reservation carries WHERE it comes from:
            # basis_kind law|professional (a shipped reference) or policy (the
            # user's own clause), plus the human-curated source string. The flat
            # `reserved` above stays for count/boolean consumers; this is additive.
            "reservations": [
                {k: a.get(k, "") for k in
                 ("act_type", "reserved_to", "basis_kind", "source", "trigger", "when",
                  "duration", "on_elapse")}
                for a in reserved
            ],
            # declared duties (obligation) + remedies (redress) that ride with the
            # gate — persisted on the chain, projected so the UI can show them.
            "obligations": list(uc.get("obligations") or []),
            "redress": list(uc.get("redress") or []),
            # data-lineage tags the run-path's lens sees: authored ∪ connector-derived.
            # The Inspector shows them, and which connectors contribute, so a reviewer
            # can read why a `when tags contains <t>` guard would (not) fire.
            "tags": sorted(set(uc.get("tags") or []) | _conn_tags.get(uid, set())),
            "tags_authored": sorted(set(uc.get("tags") or [])),
            "tags_connector": sorted(_conn_tags.get(uid, set())),
        })
        # Authority cords: each permitted agent -> the use case.
        for agent_id in (uc.get("allowed_agents") or []):
            edges.append({
                "from": f"party:{agent_id}", "to": f"uc:{uid}",
                "kind": "authority", "verdict": "permitted",
            })
        # Egress cord: use case -> master, carrying the latest run's verdict.
        run = latest.get(uid)
        if prohibited:
            # Severed by law: the boundary never releases it, run or no run.
            verdicts[f"uc:{uid}"] = {"verdict": "prohibited", "run_id": None}
            edges.append({
                "from": f"uc:{uid}", "to": "master",
                "kind": "egress", "verdict": "prohibited",
            })
        elif run is not None:
            v = _verdict_for_run(run)
            verdicts[f"uc:{uid}"] = v
            edges.append({
                "from": f"uc:{uid}", "to": "master",
                "kind": "egress", "verdict": v["verdict"],
                "run_id": v.get("run_id"),
            })
        else:
            # No run yet: the cord exists but is unfired (no self-start).
            edges.append({
                "from": f"uc:{uid}", "to": "master",
                "kind": "egress", "verdict": "unfired",
            })

    # Connectors — the boundary ports (task spine): ingress feeds use-cases,
    # oversight reaches a human for them, egress is the return off master.
    try:
        from .connectors import list_connectors
        conns = list_connectors(folder_context, log_root=log_root)
    except Exception:
        conns = []
    n_conn = 0
    for c in conns:
        cid = c.get("connector_id") or ""
        if not cid:
            continue
        n_conn += 1
        role = c.get("role", "ingress")
        nodes.append({
            "id": f"conn:{cid}", "kind": "connector", "role": role,
            "channel": c.get("channel", ""), "label": c.get("name") or cid,
            # C1: an EGRESS connector is a boundary in its own right — carry the
            # gate it enforces so the UI can render N boundaries (one per
            # destination-class) instead of only the single master. floor = the
            # channel's self-governance minimum; group = the group-bus it belongs
            # to (that floor binds every member, strictest-wins); destination_class
            # = the axis egress is worded by (llm|tool_api|message|file). Additive.
            **({"floor": c.get("floor", ""), "group": c.get("group", ""),
                "destination_class": c.get("destination_class", ""),
                "is_boundary": True} if role == "egress" else {}),
        })
        targets = [f"uc:{u}" for u in (c.get("use_cases") or [])]
        if role == "ingress":
            for t in targets:
                edges.append({"from": f"conn:{cid}", "to": t, "kind": "ingress"})
        elif role == "oversight":
            for t in targets:
                edges.append({"from": t, "to": f"conn:{cid}", "kind": "notify"})
        elif role == "egress":
            edges.append({"from": "master", "to": f"conn:{cid}", "kind": "deliver"})

    # C1: the egress boundaries the policies group into — one per destination-class
    # (the axis egress is worded by), each listing the channels that reach it with
    # their per-channel floor and the group-bus they belong to (the group floor is
    # the DEFAULT gate binding every member strictest-wins; a per-channel floor is
    # a SPECIALISED gate). Additive — the single `master` stays as the undeclared
    # world-touch, so every existing reader keeps working.
    _egress = [c for c in conns if c.get("role") == "egress"]
    _by_class: dict[str, list] = {}
    for c in _egress:
        _by_class.setdefault(c.get("destination_class") or "undeclared", []).append(
            {"connector_id": c.get("connector_id"), "floor": c.get("floor", ""),
             "group": c.get("group", "")})
    egress_boundaries = [{"destination_class": k, "channels": v}
                         for k, v in sorted(_by_class.items())]

    summary = {
        "agents": n_agents, "humans": n_humans,
        "use_cases": len([u for u in use_cases if u.get("use_case_id")]),
        "runs": len(runs), "reserved_use_cases": n_reserved_uc,
        "connectors": n_conn, "egress_boundaries": len(egress_boundaries),
        "nodes": len(nodes), "edges": len(edges),
    }
    return {"folder_context": folder_context, "nodes": nodes,
            "edges": edges, "verdicts": verdicts,
            "egress_boundaries": egress_boundaries, "summary": summary}


# --------------------------------------------------------- v0.5 projection ----
# Re-project the same chain into the published Loomground v0.5 vocabulary
# kinds -> classes (agent->actor, use_case->gate), typed cords, and
# verdicts in the 5-symbol alphabet. A PURE transform over governance_graph()
# output — the legacy projection is untouched, so the current app keeps working.
_V05_CLASS = {"agent": "actor", "human": "human", "use_case": "gate", "master": "master"}


def _bare(node_id: str) -> str:
    """Strip the host id prefix (party:/uc:) to the bare language id."""
    return node_id.split(":", 1)[1] if ":" in node_id else node_id


def governance_graph_v05(folder_context: str, log_root: Optional[str] = None) -> dict[str, Any]:
    """The v0.5 projection: {nodes, cords, verdicts, connectors, summary,
    warnings}. Server-decided; a pure transform over governance_graph()."""
    return _project_v05(governance_graph(folder_context, log_root=log_root))


def _project_v05(g: dict[str, Any]) -> dict[str, Any]:
    """Pure transform of a legacy governance_graph dict into v0.5 vocabulary.

    Nodes carry a v0.5 `class`; cords are typed authority|egress|pipe; verdicts
    use the alphabet auto|human|refused|reserved|prohibited (loomground_lang),
    server-decided. Pipe cords project when the chain carries them (pipe
    persistence is a separate write path). Fail-safe: an unrecognised verdict clamps
    to the MOST restrictive symbol (never to the releasing `auto`)."""
    from rvnd.adapters.solver.loomground import VERDICTS as _ALPHABET
    _MOST_RESTRICTIVE = _ALPHABET[-1]  # "prohibited"

    warnings: list[str] = []
    # The party:/uc: prefix is load-bearing for uniqueness; bare it only when
    # that does not collide. A collision (same bare id, different host id) keeps
    # the prefixed id and is surfaced as a warning rather than silently merged.
    projectable = [n for n in g["nodes"] if n["kind"] in _V05_CLASS]
    bare_count: dict[str, int] = {}
    for n in projectable:
        bare_count[_bare(n["id"])] = bare_count.get(_bare(n["id"]), 0) + 1
    idmap: dict[str, str] = {}
    for n in projectable:
        b = _bare(n["id"])
        if bare_count[b] > 1:
            idmap[n["id"]] = n["id"]
            warnings.append(f"id collision on {b!r}: kept prefixed id {n['id']!r}")
        else:
            idmap[n["id"]] = b

    def disp(host_id: str) -> str:
        return idmap.get(host_id, _bare(host_id))

    nodes: list[dict[str, Any]] = []
    master_node: Optional[dict[str, Any]] = None
    for n in projectable:
        cls = _V05_CLASS[n["kind"]]
        node: dict[str, Any] = {"id": disp(n["id"]), "class": cls}
        if cls == "human" and n.get("role"):
            node["role"] = n["role"]
        if cls == "gate" and n.get("risk"):
            node["risk_floor"] = n["risk"]
        if cls == "master":
            master_node = node
        else:
            nodes.append(node)
    if master_node is not None:        # the single master is projected last
        nodes.append(master_node)

    cords: list[dict[str, Any]] = []
    verdicts: dict[str, Any] = {}
    for e in g["edges"]:
        k = e.get("kind")
        if k == "authority":
            cords.append({"from": disp(e["from"]), "to": disp(e["to"]), "type": "authority"})
        elif k == "pipe":
            cords.append({"from": disp(e["from"]), "to": disp(e["to"]), "type": "pipe"})
        elif k == "egress":
            gate = disp(e["from"])
            c: dict[str, Any] = {"from": gate, "to": "master", "type": "egress"}
            v = e.get("verdict")
            if v and v != "unfired":
                vv = v if v in _ALPHABET else _MOST_RESTRICTIVE
                if vv != v:
                    warnings.append(f"unknown verdict {v!r} on {gate!r} clamped to {vv!r}")
                c["verdict"] = vv
                verdicts[gate] = {"verdict": vv}
            cords.append(c)

    connectors = [n for n in g["nodes"] if n["kind"] == "connector"]
    out = {"folder_context": g.get("folder_context", ""), "dialect": "v05",
           "nodes": nodes, "cords": cords, "verdicts": verdicts,
           "connectors": connectors, "summary": g.get("summary", {})}
    if warnings:
        out["warnings"] = warnings
    return out


# ---------------------------------------------------------- register / inventory
# A standing register of agents + use-cases. Per folder it is a tabular
# projection of governance_graph; `scope='all'` fans out over the known folders.
# Status is CATEGORICAL or declare-the-gap — never a % or score; this is a
# per-folder + all-folders view, NOT multi-tenant (that's the parked Gate layer).
def governance_register(folder_context: str, log_root: Optional[str] = None) -> dict[str, Any]:
    g = governance_graph(folder_context, log_root=log_root)
    egress = {e["from"]: e for e in g["edges"] if e["kind"] == "egress"}
    auth_count: dict[str, int] = {}
    for e in g["edges"]:
        if e["kind"] == "authority":
            auth_count[e["from"]] = auth_count.get(e["from"], 0) + 1
    rows: list[dict[str, Any]] = []
    for n in g["nodes"]:
        if n["kind"] == "agent":
            rows.append({"type": "agent", "id": n["id"], "label": n["label"],
                         "status": n.get("status", "active"),
                         "authority_over": auth_count.get(n["id"], 0)})
        elif n["kind"] == "use_case":
            v = (egress.get(n["id"]) or {}).get("verdict", "unfired")
            reserved = bool(n.get("reserved"))
            # Attributed basis, not a flat "by law" claim — a reservation may be
            # required by law/professional duty OR the user's own policy. Carry the
            # distinct basis_kinds so the register can name the source honestly.
            bases = sorted({r.get("basis_kind", "") for r in (n.get("reservations") or [])
                            if r.get("basis_kind")})
            rows.append({"type": "use_case", "id": n["id"], "label": n["label"],
                         "risk": n.get("risk", ""), "level": n.get("grade", 0),
                         "reserved": reserved,
                         "reserved_bases": bases,
                         "verdict": "reserved" if reserved else v,
                         "wired": n["id"] in auth_count_targets(g)})
    return {"folder_context": folder_context, "rows": rows, "summary": g["summary"]}


def auth_count_targets(g: dict[str, Any]) -> set:
    return {e["to"] for e in g["edges"] if e["kind"] == "authority"}


def governance_netlist(folder_context: str, log_root: Optional[str] = None) -> dict[str, Any]:
    """Render the current chain as a v0.5 .lg netlist (the editor's third
    surface). Structure round-trips (authority cords carry the grants);
    reservations are not yet emitted by this projection."""
    from . import loomground_lang as _L
    v = governance_graph_v05(folder_context, log_root=log_root)
    patch = {
        "nodes": [dict(n) for n in v["nodes"] if n.get("class") != "master"],
        "cords": [{"from": c["from"], "to": c["to"]} for c in v["cords"]],
    }
    return {"folder_context": folder_context, "netlist": _L.to_netlist(patch)}


def governance_register_all(log_root: Optional[str] = None) -> dict[str, Any]:
    """Aggregate the register across every known workspace (read-only compose)."""
    try:
        from .workspace_registry import list_known_workspaces
        ws = list_known_workspaces(log_root=log_root)
    except Exception:
        ws = []
    folders: list[dict[str, Any]] = []
    skipped = 0
    for w in ws:
        path = w.get("path") or w.get("folder") or w.get("root")
        if not path:
            skipped += 1; continue
        try:
            reg = governance_register(path, log_root=log_root)
        except Exception:
            skipped += 1; continue
        folders.append({"folder": path, "summary": reg["summary"], "rows": reg["rows"]})
    # honest scope: this is the REGISTERED workspaces, not filesystem discovery
    return {"folders": folders, "count": len(folders),
            "scanned": len(ws), "skipped": skipped}


# A small, fixed query contract over the patch — the governance questions a
# spreadsheet can't answer. Read-only; computed from governance_graph. Named
# queries, not a general language, so the set is portable and auditable.
QUERIES = {
    "needs_human_no_human": "use-cases that need a human (reserved/needs-review) "
                            "while no human party is registered",
    "auto_high_risk": "high/critical use-cases whose verdict is auto (a law-floor "
                      "violation smell — should be empty)",
    "unfired": "use-cases wired but never run (nothing self-starts)",
    "unwired_use_cases": "use-cases with no agent authority — cannot run",
    "agent_reach": "per agent, how many use-cases it has authority over (blast radius)",
}


def governance_query(folder_context, query, log_root=None):
    """Answer one named query over the patch. Returns {query, rows, count}."""
    if query not in QUERIES:
        return {"error": f"unknown query {query!r}", "valid": sorted(QUERIES)}
    g = governance_graph(folder_context, log_root=log_root)
    nodes, edges = g["nodes"], g["edges"]
    humans = [n for n in nodes if n["kind"] == "human"]
    agents = [n for n in nodes if n["kind"] == "agent"]
    ucs = [n for n in nodes if n["kind"] == "use_case"]
    egress = {e["from"]: e for e in edges if e["kind"] == "egress"}
    auth = [e for e in edges if e["kind"] == "authority"]
    rows = []

    if query == "needs_human_no_human":
        no_human = len(humans) == 0
        for u in ucs:
            v = egress.get(u["id"], {}).get("verdict")
            needs = bool(u.get("reserved")) or v in ("reserved", "human")
            if needs and no_human:
                rows.append({"use_case": u["label"], "verdict": v or "unfired",
                             "why": "needs a human but no human party is registered"})
    elif query == "auto_high_risk":
        for u in ucs:
            v = egress.get(u["id"], {}).get("verdict")
            if u.get("risk") in ("high", "critical") and v == "auto":
                rows.append({"use_case": u["label"], "risk": u.get("risk"), "verdict": v})
    elif query == "unfired":
        for u in ucs:
            if egress.get(u["id"], {}).get("verdict") == "unfired":
                rows.append({"use_case": u["label"], "risk": u.get("risk")})
    elif query == "unwired_use_cases":
        wired = {e["to"] for e in auth}
        for u in ucs:
            if u["id"] not in wired:
                rows.append({"use_case": u["label"], "why": "no agent authority — cannot run"})
    elif query == "agent_reach":
        counts = {}
        for e in auth:
            counts[e["from"]] = counts.get(e["from"], 0) + 1
        for a in agents:
            rows.append({"agent": a["label"], "use_cases": counts.get(a["id"], 0)})

    return {"query": query, "rows": rows, "count": len(rows)}
