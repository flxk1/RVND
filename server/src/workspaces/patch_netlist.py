# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""patch_netlist — the textual surface of a governance patch.

Three surfaces, one truth: a patch is a glyph canvas, a textual netlist, AND the
signed chain. This module is the netlist surface — the human-writable, diffable
serialization that round-trips with what ``governance_graph`` projects from the
chain. (Pure Data precedent: a visual patch and a textual .pd netlist are the
same program.)

The product name is still a placeholder; nothing here is named after it. The
file extension used in examples is ``.patch`` and the format is brand-neutral.

Grammar (line-oriented; ``#`` starts a comment; blank lines ignored):

    agent    <id>   [grade <G>] [name <text...>]
    human    <id>   [competence <c>] [name <text...>]
    use-case <id>   risk <low|medium|high|critical> [issue <type>] [allow <a> <b> ...]
    wire     <from> -> <to>          # authority (agent->use-case) | egress (use-case->master)

``allow`` on a use-case line and an authority ``wire`` are equivalent ways to
grant authority; both merge into the use case's allowed-agent set. ``allow`` must
be the last clause on its line (it consumes the rest of the tokens).

Typed cords (validated fail-closed BEFORE any write):
  * authority: source MUST be a declared agent, target MUST be a declared
    use-case. A human is never an authority source; a use-case is never one.
  * egress:    source MUST be a declared use-case, target MUST be ``master``.
  * any other shape is rejected with a reason.
"""
from __future__ import annotations

from typing import Any, Optional

from .step_contract import RISK_LEVELS

MASTER = "master"


# ------------------------------------------------------------ abstraction ----
def expand_racks(text: str) -> str:
    """Macro pre-pass: expand rack definitions + instantiations into plain
    netlist lines. A rack is Loomground's unit of ABSTRACTION (a saved sub-patch
    reused with parameters -- Pure Data abstractions / $args).

        rack <name>(<p1>, <p2>, ...):
            <body lines, using $p1 / $p2 ...>
        end

        rack-use <name>(p1=val1, p2=val2, ...)     # instantiate

    Each ``$param`` in the body is substituted with its bound argument. Unknown
    racks, missing args, extra args, undefined ``$names``, or a missing ``end``
    raise ValueError. Lines outside a rack pass through unchanged, so a netlist
    with no racks is byte-for-byte unaffected."""
    import re
    hdr = re.compile(r"^rack\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*:\s*$")
    use = re.compile(r"^rack-use\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*$")
    racks: dict[str, dict[str, Any]] = {}
    out: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.split("#", 1)[0].strip()
        m = hdr.match(stripped)
        if m:
            name, plist = m.group(1), m.group(2)
            params = [p.strip() for p in plist.split(",") if p.strip()]
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i].split("#", 1)[0].strip() != "end":
                body.append(lines[i])
                i += 1
            if i >= len(lines):
                raise ValueError(f"rack {name!r}: missing 'end'")
            racks[name] = {"params": params, "body": body}
            i += 1  # consume 'end'
            continue
        mu = use.match(stripped)
        if mu:
            name, alist = mu.group(1), mu.group(2)
            if name not in racks:
                raise ValueError(f"rack-use: unknown rack {name!r}")
            args: dict[str, str] = {}
            for pair in (p.strip() for p in alist.split(",")):
                if not pair:
                    continue
                if "=" not in pair:
                    raise ValueError(f"rack-use {name}: arg {pair!r} must be key=value")
                k, v = pair.split("=", 1)
                args[k.strip()] = v.strip()
            params = racks[name]["params"]
            missing = [p for p in params if p not in args]
            extra = [k for k in args if k not in params]
            if missing or extra:
                raise ValueError(
                    f"rack-use {name}: missing={missing} extra={extra}")

            def _sub(mt, _name=name, _args=args):
                key = mt.group(1)
                if key not in _args:
                    raise ValueError(f"rack {_name}: undefined ${key}")
                return _args[key]

            for bline in racks[name]["body"]:
                out.append(re.sub(r"\$([A-Za-z_]\w*)", _sub, bline))
            i += 1
            continue
        out.append(raw)
        i += 1
    return "\n".join(out) + ("\n" if out else "")


# ----------------------------------------------------------------- parse -----
def parse_netlist(text: str) -> dict[str, Any]:
    """Parse netlist text into a structured patch. Raises ValueError on a
    malformed line (unknown keyword, missing id, bad wire arrow). Rack
    definitions/instantiations are expanded first (see ``expand_racks``)."""
    text = expand_racks(text)
    parties: dict[str, dict[str, Any]] = {}
    use_cases: dict[str, dict[str, Any]] = {}
    wires: list[dict[str, Any]] = []

    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tok = line.split()
        kw = tok[0].lower()

        if kw in ("agent", "human"):
            if len(tok) < 2:
                raise ValueError(f"line {n}: {kw} needs an id")
            rec: dict[str, Any] = {"party_id": tok[1],
                                   "kind": "agent" if kw == "agent" else "human"}
            rest = tok[2:]
            i = 0
            while i < len(rest):
                key = rest[i].lower()
                if key == "grade" and i + 1 < len(rest):
                    rec["grade"] = rest[i + 1]; i += 2
                elif key == "competence" and i + 1 < len(rest):
                    rec["competence"] = rest[i + 1]; i += 2
                elif key == "name":
                    rec["name"] = " ".join(rest[i + 1:]); i = len(rest)
                else:
                    raise ValueError(f"line {n}: unexpected token {rest[i]!r}")
            parties[rec["party_id"]] = rec

        elif kw == "use-case":
            if len(tok) < 2:
                raise ValueError(f"line {n}: use-case needs an id")
            rec = {"use_case_id": tok[1], "risk": "", "issue_type": "",
                   "name": "", "allowed_agents": []}
            rest = tok[2:]
            i = 0
            while i < len(rest):
                key = rest[i].lower()
                if key == "risk" and i + 1 < len(rest):
                    rec["risk"] = rest[i + 1].lower(); i += 2
                elif key == "issue" and i + 1 < len(rest):
                    rec["issue_type"] = rest[i + 1]; i += 2
                elif key == "name" and i + 1 < len(rest):
                    # name takes one token here to keep 'allow' parseable after;
                    # multi-word names: use underscores.
                    rec["name"] = rest[i + 1]; i += 2
                elif key == "allow":
                    rec["allowed_agents"] = list(rest[i + 1:]); i = len(rest)
                else:
                    raise ValueError(f"line {n}: unexpected token {rest[i]!r}")
            use_cases[rec["use_case_id"]] = rec

        elif kw == "wire":
            # wire <from> -> <to>
            if "->" not in tok:
                raise ValueError(f"line {n}: wire needs '->' (got {line!r})")
            arrow = tok.index("->")
            src = " ".join(tok[1:arrow]).strip()
            dst = " ".join(tok[arrow + 1:]).strip()
            if not src or not dst:
                raise ValueError(f"line {n}: wire needs a source and a target")
            wires.append({"from": src, "to": dst})

        else:
            raise ValueError(f"line {n}: unknown keyword {kw!r}")

    return {"parties": list(parties.values()),
            "use_cases": list(use_cases.values()),
            "wires": wires}


# -------------------------------------------------------------- validate -----
def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Typed-cord + shape validation, fail-closed. Returns
    {ok, errors, wires} where each wire is classified authority|egress|invalid."""
    errors: list[str] = []
    agents = {p["party_id"] for p in patch.get("parties", [])
              if p.get("kind") == "agent"}
    humans = {p["party_id"] for p in patch.get("parties", [])
              if p.get("kind") == "human"}
    ucs = {u["use_case_id"] for u in patch.get("use_cases", [])}
    nodes = agents | humans | ucs | {MASTER}

    for u in patch.get("use_cases", []):
        if u.get("risk") not in RISK_LEVELS:
            errors.append(f"use-case {u.get('use_case_id')!r}: risk must be one "
                          f"of {list(RISK_LEVELS)}, got {u.get('risk')!r}")
        for a in u.get("allowed_agents", []):
            if a not in agents:
                errors.append(f"use-case {u.get('use_case_id')!r}: allow {a!r} "
                              f"is not a declared agent")

    classified: list[dict[str, Any]] = []
    for w in patch.get("wires", []):
        src, dst = w.get("from"), w.get("to")
        kind, reason = "invalid", ""
        if src not in nodes:
            reason = f"unknown source {src!r}"
        elif dst not in nodes:
            reason = f"unknown target {dst!r}"
        elif dst == MASTER:
            if src in ucs:
                kind = "egress"
            else:
                reason = f"egress cord must start at a use-case, not {src!r}"
        elif dst in ucs:
            if src in agents:
                kind = "authority"
            elif src in humans:
                reason = (f"a human ({src!r}) is not an authority source; "
                          f"humans decide via reserved acts, not cords")
            else:
                reason = f"authority cord must start at an agent, not {src!r}"
        else:
            reason = f"no legal cord from {src!r} to {dst!r}"
        if kind == "invalid":
            errors.append(f"wire {src} -> {dst}: {reason}")
        classified.append({"from": src, "to": dst, "kind": kind,
                           "reason": reason})

    return {"ok": not errors, "errors": errors, "wires": classified}


# ----------------------------------------------------------------- apply -----
def apply_patch(
    folder_context: str,
    patch: dict[str, Any],
    *,
    actor: str,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Validate then write the patch to the signed chain. Fail-closed: on any
    validation error, NOTHING is written. Authority wires merge into each use
    case's allowed-agent set. Returns the resulting governance_graph so the
    caller sees exactly what landed (text -> chain -> graph round-trip)."""
    if not (actor or "").strip():
        return {"ok": False, "errors": ["apply needs a named actor"]}
    v = validate_patch(patch)
    if not v["ok"]:
        return {"ok": False, "errors": v["errors"]}

    from .parties import register_party
    from .use_case import register_use_case
    from .governance_graph import governance_graph

    # authority wires -> per-use-case allowed agents (merge with `allow`)
    extra_allow: dict[str, set[str]] = {}
    for w in v["wires"]:
        if w["kind"] == "authority":
            extra_allow.setdefault(w["to"], set()).add(w["from"])

    for p in patch.get("parties", []):
        register_party(folder_context, p["party_id"], p["kind"],
                       name=p.get("name", ""), grade=p.get("grade", ""),
                       competences=([p["competence"]] if p.get("competence") else None),
                       actor=actor, log_root=log_root)

    for u in patch.get("use_cases", []):
        uid = u["use_case_id"]
        allowed = list(dict.fromkeys(
            list(u.get("allowed_agents", [])) + sorted(extra_allow.get(uid, set()))))
        fingerprint = {"issue_type": u["issue_type"]} if u.get("issue_type") else {}
        register_use_case(folder_context, use_case_id=uid,
                          name=u.get("name") or uid, fingerprint=fingerprint,
                          risk=u["risk"], allowed_agents=allowed, actor=actor,
                          log_root=log_root)

    graph = governance_graph(folder_context, log_root=log_root)
    return {"ok": True, "applied": {
        "parties": len(patch.get("parties", [])),
        "use_cases": len(patch.get("use_cases", [])),
        "authority_cords": sum(1 for w in v["wires"] if w["kind"] == "authority"),
    }, "graph": graph}


# -------------------------------------------------------------- writer -------
def to_netlist(patch: dict[str, Any]) -> str:
    """Serialize a structured patch back to netlist text (canvas -> text)."""
    lines: list[str] = []
    for p in patch.get("parties", []):
        if p.get("kind") == "agent":
            extra = f" grade {p['grade']}" if p.get("grade") else ""
            lines.append(f"agent    {p['party_id']}{extra}")
        else:
            extra = f" competence {p['competence']}" if p.get("competence") else ""
            lines.append(f"human    {p['party_id']}{extra}")
    for u in patch.get("use_cases", []):
        issue = f" issue {u['issue_type']}" if u.get("issue_type") else ""
        allow = (" allow " + " ".join(u["allowed_agents"])) if u.get("allowed_agents") else ""
        lines.append(f"use-case {u['use_case_id']} risk {u['risk']}{issue}{allow}")
    for w in patch.get("wires", []):
        lines.append(f"wire {w['from']} -> {w['to']}")
    return "\n".join(lines) + "\n"
