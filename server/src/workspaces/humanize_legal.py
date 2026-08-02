# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Human-readable rendering of the legal record — names, not spans.

The user does not know what a "span", a "pinpoint" or an entity code is. Every
surface that informs them (notice, preview, decision surface, printable
record) speaks in the names and numbers people actually use: the instrument's
full title from the corpus registry, the citation written out (Article 33(1),
§ 147), and the provision's own opening words as the gist. The internal
identifiers stay underneath for the audit trail; they are never the headline.

This is also where the responsibility design becomes ambient instead of
preachy: the **corpus posture** line ("Your library: 2 documents — …") states
factually whose library this is, on every form, without a single warning.

Pure stdlib; reads the folder's entity registry for display names.
"""

from __future__ import annotations

import re
from typing import Optional

from .legal_corpus import EntityRegistry


# ── name resolution ───────────────────────────────────────────────────────────

def _names(folder) -> dict[str, dict]:
    """entity code → {name, url} across all kinds (instruments, regulators…)."""
    reg = EntityRegistry(folder)
    out: dict[str, dict] = {}
    for key, e in reg.entities.items():
        out[e.get("code", key.split(":", 1)[-1])] = {
            "name": e.get("name") or key, "url": e.get("url") or ""}
    return out


def expand_citation(pinpoint: str) -> str:
    """'Art. 33(1)' → 'Article 33(1)'. German '§ 147(1)' stays as cited —
    that IS the human form."""
    return re.sub(r"\bArt\.?\s*", "Article ", (pinpoint or "").strip())


def _gist(text: str, limit: int = 110) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + " …"


def describe_pinpoint(registry, pinpoint: str) -> dict:
    """One provision, in words: citation + instrument title + its own opening
    words + source link. ``held`` says whether the text is in the library."""
    names = _names(registry.folder)
    for r in registry.workspace_items():
        if r.get("kind") != "norm":
            continue
        pin = r["span"].get("pinpoint", "")
        if pin and (pin == pinpoint or pin in pinpoint or pinpoint in pin):
            ent = next((a["entity"] for a in r.get("anchors", [])
                        if a["relation"] == "cites"), "")
            ni = names.get(ent, {})
            return {"citation": expand_citation(pin),
                    "instrument": ni.get("name", ent), "url": ni.get("url", ""),
                    "gist": _gist(r["span"]["text"]), "held": True}
    return {"citation": expand_citation(pinpoint), "instrument": "",
            "url": "", "gist": "", "held": False}


def describe_gap(registry, gap: str, *, instrument_hint: str = "") -> str:
    names = _names(registry.folder)
    inst = names.get(instrument_hint, {}).get("name", "")
    cite = expand_citation(gap)
    return (f"{cite}" + (f" of the {inst}" if inst else "")
            + " — the text of this provision is not in your library; "
              "it was cited but could not be checked")


def display_action(registry, action: dict) -> str:
    names = _names(registry.folder)
    enforcers = [names.get(e, {}).get("name", e)
                 for e in action.get("enforced_by", [])][:3]
    parts = [action.get("obligation", "").rstrip(".")]
    if action.get("actor"):
        parts.append(f"(duty of the {action['actor']})")
    if action.get("deadline"):
        parts.append(f"— within {action['deadline']}")
    s = " ".join(p for p in parts if p)
    src = expand_citation(action.get("source_norm", ""))
    if src:
        s += f". Basis: {src}"
    if enforcers:
        s += f". Supervised by {', '.join(enforcers)}"
    return s


# ── the corpus posture: whose library this is, said factually ─────────────────

def corpus_posture(registry) -> dict:
    """'Your library: 2 documents — General Data Protection Regulation
    (4 provisions), … .' Counts what is actually held; names what counts."""
    names = _names(registry.folder)
    per_inst: dict[str, int] = {}
    for r in registry.workspace_items():
        if r.get("kind") != "norm":
            continue
        ent = next((a["entity"] for a in r.get("anchors", [])
                    if a["relation"] == "cites"), "")
        if ent:
            per_inst[ent] = per_inst.get(ent, 0) + 1
    docs = [{"name": names.get(c, {}).get("name", c), "provisions": n,
             "url": names.get(c, {}).get("url", "")}
            for c, n in sorted(per_inst.items(), key=lambda kv: -kv[1])]
    total = sum(per_inst.values())
    if docs:
        listing = ", ".join(f"{d['name']} ({d['provisions']} provisions)"
                            for d in docs[:4])
        if len(docs) > 4:
            listing += f", and {len(docs) - 4} more"
        line = (f"Your library: {len(docs)} document(s) — {listing}. "
                f"Everything below was checked against these {total} "
                f"provisions and nothing else.")
    else:
        line = ("Your library is empty — nothing could be checked. "
                "Add the documents that govern you to get receipts.")
    return {"documents": docs, "total_provisions": total, "line": line}


# ── humanized case + the information form actually shown ─────────────────────

def humanize_case(case_dict: dict, registry) -> dict:
    """Attach display fields next to the technical ones (never replacing them —
    the audit trail keeps the raw identifiers)."""
    c = dict(case_dict)
    cited = {g.get("entity", "") for g in c.get("grounds", []) if g.get("entity")}
    hint = next(iter(cited), "")
    for g in c.get("grounds", []):
        d = describe_pinpoint(registry, g.get("pinpoint", ""))
        g["display"] = (f"{d['citation']}"
                        + (f", {d['instrument']}" if d["instrument"] else "")
                        + (f' — "{d["gist"]}"' if d["gist"] else ""))
        exc = (g.get("exception") or "").strip()
        if exc:                              # the carve-out is part of the norm —
            g["display"] += f" — UNLESS {_gist(exc, 90)}"     # shown, not buried
        if d.get("url"):
            g["display_url"] = d["url"]
    c["gaps_display"] = [describe_gap(registry, g, instrument_hint=hint)
                         for g in c.get("gaps", [])]
    for a in c.get("actions", []):
        a["display"] = display_action(registry, a)
    c["posture"] = corpus_posture(registry)["line"]
    return c


def render_information_form(level: str, case_dict: dict, registry) -> dict:
    """The user-facing payload for this oversight level (the §4a.5 ladder as
    content, not just policy): headline, human lines, and the posture line on
    every form. Raw identifiers ride along for click-through, never lead."""
    from .reasoning_contract import oversight_form
    form = oversight_form(level)
    c = humanize_case(case_dict, registry)
    res = c.get("resolution", {})
    lines: list[str] = []
    if form["form"] in ("preview", "decision-surface", "transcript", "schema-only"):
        lines += [g["display"] for g in c.get("grounds", [])]
        lines += c.get("gaps_display", [])
    if form["form"] == "decision-surface":
        for o in (res.get("surface") or {}).get("options", []):
            lines.append(f"Option — {o.get('label', '')}: "
                         + "; ".join(o.get("consequences", []) or ["no consequences stated"]))
        if res.get("proposed"):
            lines.append(f"Proposed (awaiting your confirmation): "
                         f"{res['proposed'].get('label', '')}")
    headline = {"record": "Recorded for audit.",
                "notice": f"Checked: {c.get('problem', {}).get('text', '')[:90]} "
                          f"— status: {res.get('type', 'open').upper()}.",
                "preview": "What was checked, and what was not:",
                "decision-surface": "Your decision — the grounds and the "
                                    "consequences of each option:",
                "transcript": "Full walk transcript (every step, every repair):",
                "schema-only": "The reasoning frame and the evidence — the "
                               "conclusion is yours to write:",
                }[form["form"]]
    return {"level": level, "form": form["form"], "headline": headline,
            "lines": lines, "posture": c["posture"],
            "coverage_pct": round(100 * c.get("coverage", 0)),
            "gaps_open": len(c.get("gaps", [])),
            "waivers": c.get("waivers", [])}
