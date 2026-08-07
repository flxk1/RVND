# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The problem-solving KG — case records projected from what folder memory holds.

The panel's verdict (work/panel-2026-06-03-kg-visualisation.md): problem-solving
relations are not a node-link graph but a **case file** — one problem at a time,
three columns mirroring legal method:

    PROBLEM (clause/question) → GROUNDS & CHAIN (anchored norm-spans with
    coverage receipts; the subsumption ladder) → RESOLUTION (a determinate
    answer, or the decision surface with the recorded, originated choice).

This module builds those case records as data, and projects them into the same
dimensioned pair/edge format as every other Workspace KG so they compose with the 5D
machinery. It is a **projection, not new storage**: problems come from the rule
registry's clause spans, grounds from their anchors plus the norm-spans of the
cited instruments, coverage from a required-rooms list (receipted / gap),
resolutions from a determinate answer or a `decision_surface.record_choice`
record. Pure stdlib.
"""

from __future__ import annotations

from typing import Optional

# The pure case-record subset — Ground/Fact/CaseRecord + the project_pairs
# projection + the _norm_spans_for helper — is owned by loomground-solver
# (solver.case) and consumed here through the solver seam. solver.case's own
# docstring prescribes the split: the pure subset lives in the package;
# corpus-coupled assembly (build_case & friends, below) stays in the host.
from .adapters.solver.case import (  # noqa: F401
    Ground, Fact, CaseRecord, project_pairs, _norm_spans_for,
)


def build_case(problem_text: str, *, registry, document: str = "",
               required_rooms: Optional[list[str]] = None,
               chain: Optional[list[dict]] = None,
               answer: Optional[str] = None,
               surface=None, choice: Optional[dict] = None,
               facts: Optional[list] = None,
               actions: Optional[list[dict]] = None,
               profile: str = "legal-de",
               oversight_level: str = "autonomous",
               oversight_active: bool = True,
               stake: bool = False, personal: bool = False,
               enforce: bool = True,
               cited_entities: Optional[set] = None) -> CaseRecord:
    """Assemble one case record.

    - Places/loads the problem span in the registry (anchors → cited instruments).
    - Grounds = the cited instruments' norm-spans held in the registry, keyed by
      pinpoint; ``required_rooms`` (pinpoints that MUST be checked) are receipted
      when a ground covers them, otherwise reported as gaps.
    - Resolution: ``answer`` (determinate) — or ``surface``+``choice`` (residual,
      from decision_surface). Exactly one must be provided; a residual without a
      recorded choice is presented as an OPEN decision, never as an answer.
    """
    placed = registry.place_span(problem_text, source_document=document, kind="clause")
    cited = {a["entity"] for a in placed["anchors"] if a["relation"] == "cites"}
    cited |= set(cited_entities or ())     # e.g. a gate question citing nothing itself
    spans = _norm_spans_for(registry, cited)
    by_pin: dict = {}
    for s in spans:                         # first norm per pinpoint wins — the
        by_pin.setdefault(s["span"].get("pinpoint", ""), s)   # primary sentence,
    # not a later sentence shadowing its exception

    grounds: list[Ground] = []
    gaps: list[str] = []
    full_texts: list[str] = []              # untruncated spans, for crossref scan
    rooms = list(required_rooms or sorted(p for p in by_pin if p))

    def _resolve(room: str) -> None:
        hit = by_pin.get(room) or next(
            (s for p, s in by_pin.items() if p and (p in room or room in p)), None)
        if hit:
            ent = next((a["entity"] for a in hit["anchors"]
                        if a["relation"] == "cites"), "")
            norm = hit.get("norm") or {}
            full_texts.append(hit["span"]["text"])
            grounds.append(Ground(pinpoint=room, text=hit["span"]["text"][:240],
                                  entity=ent, receipted=True,
                                  condition=(norm.get("condition") or "").strip(),
                                  consequence=(norm.get("action") or "").strip(),
                                  exception=(norm.get("exception") or "").strip()))
        else:
            gaps.append(room)

    for room in rooms:
        _resolve(room)

    # systematic canon — a norm is not read in isolation: cross-references in
    # the grounds' own (FULL) text ("in accordance with Article 55") become
    # required rooms too, receipted when held, honest gaps when not.
    import re as _re2
    seen_arts = {m.group(1) for r in rooms
                 for m in [_re2.search(r"(\d+[a-z]?)", r)] if m}
    i = 0
    while i < len(full_texts):              # fetched refs may chain one level
        for ref in _re2.findall(r"Art(?:icle|ikel)?\.?\s*(\d+[a-z]?)", full_texts[i]):
            if ref not in seen_arts:
                seen_arts.add(ref)
                room = f"Art. {ref}"
                rooms.append(room)
                _resolve(room)
        i += 1

    if answer is not None and (surface is not None or choice is not None):
        raise ValueError("a case is determinate OR residual, not both")
    if answer is not None:
        resolution = {"type": "determinate", "answer": answer}
    elif surface is not None:
        resolution = {"type": "residual",
                      "surface": surface.to_dict() if hasattr(surface, "to_dict") else surface,
                      "choice": choice}     # None ⇒ OPEN decision, shown as such
    else:
        resolution = {"type": "open",
                      "note": "no determinate answer derived and no decision recorded"}

    n_req = len(rooms) or 1
    case = CaseRecord(
        problem={"text": problem_text, "document": document,
                 "pinpoint": placed["span"].get("pinpoint", "")},
        grounds=grounds, chain=chain or [], gaps=gaps, resolution=resolution,
        coverage=round(len(grounds) / n_req, 3),
        facts=[f if isinstance(f, Fact) else Fact(**f) for f in (facts or [])],
        actions=actions or [], profile=profile)

    # the reasoning-contract gate sits here the way the norm-contract gate
    # sits in ND dispatch: malformed records never stand; escalations attach.
    # held_pinpoints = what the corpus can actually verify (R5 resolution).
    from . import reasoning_contract as rc
    held = {p for p in by_pin if p} | {g.pinpoint for g in grounds}
    kw = dict(oversight_level=oversight_level, oversight_active=oversight_active,
              stake=stake, personal=personal, held_pinpoints=held)
    report = rc.gate(case.to_dict(), **kw) if enforce else \
        rc.check_case(case.to_dict(), **kw)
    case.contract = report.to_dict()
    return case


def close_gap_by_fetch(case: CaseRecord, gap: str, *, registry,
                       fetch_fn, instrument: str = "") -> CaseRecord:
    """A gap has three futures, in this order of preference:

      1. **closed by fetch** (this function) — for provisions of PUBLIC law the
         corpus registry knows a canonical source for, the system does the
         legwork itself: fetch the provision text, ingest it per-article,
         mint the receipt, attach the source URL as provenance. The user is
         responsible for judgment, not for janitorial retrieval of EUR-Lex.
      2. closed by user ingest — private documents the system cannot fetch.
      3. waived by a signed human choice (:func:`waive_gap`).

    ``fetch_fn(instrument_code, citation) -> {"text":…, "url":…} | None`` is
    injected: the host supplies whatever fetcher its network policy allows, so
    the egress stays inside the folder's Shield/policy boundary and the fetch
    is attributable. Returns the case with the gap converted into a receipted
    ground; raises if the fetched text does not actually contain the cited
    provision (a fetch must prove itself — no receipt without the text)."""
    if gap not in case.gaps:
        raise ValueError(f"{gap!r} is not an open gap on this case (open: {case.gaps})")
    inst = instrument or next((g.entity for g in case.grounds if g.entity), "")
    got = fetch_fn(inst, gap)
    if not got or not (got.get("text") or "").strip():
        return case                                     # fetcher declined; gap stays
    res = registry.place_legal_text(got["text"], inst,
                                    source_document=got.get("url", "fetched"))
    by_pin = {r["span"].get("pinpoint", ""): r for r in registry.workspace_items()
              if r.get("kind") == "norm"}
    hit = by_pin.get(gap) or next(
        (v for p, v in by_pin.items() if p and (p in gap or gap in p)), None)
    if hit is None:
        raise ValueError(f"fetched text for {gap!r} does not contain that "
                         f"provision — no receipt without the text "
                         f"(source: {got.get('url', '?')})")
    norm = hit.get("norm") or {}
    case.gaps = [g for g in case.gaps if g != gap]
    case.grounds.append(Ground(
        pinpoint=gap, text=hit["span"]["text"][:240], entity=inst, receipted=True,
        condition=(norm.get("condition") or "").strip(),
        consequence=(norm.get("action") or "").strip()))
    n_req = len(case.grounds) + len(case.gaps) or 1
    case.coverage = round(len(case.grounds) / n_req, 3)
    case.contract.setdefault("fetched", []).append(
        {"gap": gap, "url": got.get("url", ""), "spans": len(res["placed"])})
    return case


def waive_gap(case: CaseRecord, gap: str, *, registry, actor: str,
              rationale: str) -> CaseRecord:
    """A gap has exactly two futures: closed by evidence (ingest the text) or
    OWNED by a human — this is the second. Waiving is a recorded choice, not a
    silent omission: it runs through the decision surface (two real options,
    actor + rationale mandatory, signed into the audit log) and the waiver
    stays visible on the case forever. Nothing rots in between."""
    from .decisions.surface import build_surface, record_choice
    if gap not in case.gaps:
        raise ValueError(f"{gap!r} is not an open gap on this case "
                         f"(open: {case.gaps})")
    surface = build_surface(
        f"Gap: {gap} — cited but not in the library. Dispose of it.",
        [{"id": "waive", "label": f"Waive {gap} — not applicable here",
          "conclusion": "waive",
          "consequences": ["the provision stays unchecked; the waiver and your "
                           "reasons appear on the record and the export"]},
         {"id": "keep-open", "label": f"Keep {gap} open until its text is added",
          "conclusion": "keep-open",
          "consequences": ["coverage stays reduced; the gap remains a listed "
                           "work item"]}],
        esc_reason="a gap is disposed of by a human, never by the system")
    choice = record_choice(surface, chosen_option_id="waive",
                           rationale=rationale, actor=actor,
                           folder=getattr(registry, "folder", None))
    if choice.get("error"):
        raise ValueError(f"waiver refused: {choice['error']}")
    case.gaps = [g for g in case.gaps if g != gap]
    case.waivers.append({"gap": gap, "actor": actor, "rationale": rationale,
                         "choice_id": choice.get("id", ""),
                         "decided_at": choice.get("decided_at", "")})
    return case


def derive_actions(registry, pinpoints: list[str]) -> list[dict]:
    """The deontic tail, DERIVED — never prose. For each held norm-span whose
    pinpoint is required: if its extracted modal is an obligation/prohibition,
    emit an action carrying the norm's own actor, deadline (parsed from the
    span text), source pinpoint and the regulators that enforce it (from the
    enforced_by anchors). Everything here is receipted by construction."""
    import re as _re
    items = {r["span"].get("pinpoint", ""): r
             for r in registry.workspace_items() if r.get("kind") == "norm"}
    out: list[dict] = []
    for pin in pinpoints:
        r = items.get(pin) or next(
            (v for p, v in items.items() if p and (p in pin or pin in p)), None)
        if not r:
            continue
        n = r.get("norm") or {}
        if n.get("modal") not in ("obligation", "prohibition"):
            continue
        text = r["span"]["text"]
        action = (n.get("action") or "").strip()
        if len(action) < 15 or action.endswith((" and", " or", ",")):
            action = text[:160].strip()          # extractor fragment → use the span
        m = _re.search(r"(\d+\s*(?:hours?|days?|months?|Stunden|Tagen?|Monaten?))",
                       text, _re.I)
        deadline = m.group(1) if m else \
            ("prior to the processing" if "prior to" in text.lower() else "")
        am = _re.search(r"\b(controller|processor|provider|deployer|manufacturer|"
                        r"importer|distributor|Verantwortliche[rn]?|"
                        r"Auftragsverarbeiter|Anbieter|Betreiber)\b",
                        (n.get("subject") or "") + " " + text, _re.I)
        ent = next((a["entity"] for a in r.get("anchors", [])
                    if a["relation"] == "cites"), "")
        out.append({
            "obligation": action, "actor": am.group(1).lower() if am else "",
            "deadline": deadline,
            "source_norm": f"{r['span'].get('pinpoint', pin)} {ent}".strip(),
            "modal": n["modal"], "condition": n.get("condition", ""),
            "enforced_by": [a["entity"] for a in r.get("anchors", [])
                            if a["relation"] == "enforced_by"],
            "derived": True})
    return out


def gate_case(question: str, sub_cases: list[CaseRecord], *, registry,
              document: str = "", profile: str = "legal-de",
              oversight_level: str = "approve", oversight_active: bool = True,
              stake: bool = True, personal: bool = False) -> dict:
    """A GATE question — "Can I ship this product?" — composed from per-regime
    sub-cases. The gate's facts are the sub-case records (each evidenced by its
    own case file); its gaps are every sub-case gap plus every unresolved
    sub-case; its readings are derived, never asserted:

      * everything closed, no gaps  → ONE reading (proceed) — human ratifies;
      * anything open               → TWO readings (proceed with the listed
        conditions / hold until closed) — human decides.

    Ship conditions live in the readings' consequences (derived gaps + derived
    deontic actions), NOT in case.actions — nothing is "done" before the human
    closes the gate (R5). Returns a walk-shaped result: continue with
    :func:`workspaces.reasoning_walker.ratify` or `.decide`. Stake defaults True —
    shipping bears on users and the market, so the §4a floor is ≥ APPROVE."""
    from .decisions.surface import build_surface

    facts, gaps, rooms, chain, conditions = [], [], [], [], []
    closed = 0
    for i, c in enumerate(sub_cases):
        state = c.resolution["type"]
        decided = (state == "determinate"
                   or (state == "residual" and c.resolution.get("choice")))
        closed += bool(decided)
        facts.append({"text": f"{c.problem['text']} — {state.upper()}"
                              + ("" if decided else " (unresolved)"),
                      "source": f"case record {document or c.problem.get('document','')}"
                                f" #case{i + 1}"})
        chain.append({"step": "Norm",
                      "text": f"Regime check: {c.problem['text'][:90]}",
                      "warrant": f"sub-case record #{i + 1} (coverage "
                                 f"{round(100 * c.coverage)}%)", "schema": True})
        rooms += [g.pinpoint for g in c.grounds if g.receipted]
        for g in c.gaps:
            gaps.append(g)
            conditions.append(f"close gap: {g} (sub-case #{i + 1})")
        if not decided:
            conditions.append(f"resolve sub-case #{i + 1}: {c.problem['text'][:70]}")
    for a in derive_actions(registry, sorted(set(rooms))):
        conditions.append(f"fulfil: {a['obligation'][:90]} [{a['source_norm']}]"
                          + (f" within {a['deadline']}" if a["deadline"] else ""))

    all_green = closed == len(sub_cases) and not gaps
    if all_green:
        readings = [{"id": "ship", "label": "Proceed — all governing regimes closed",
                     "grounds": sorted(set(rooms)),
                     "consequences": ["sign the case record; deontic obligations "
                                      "remain in force post-ship"]}]
        surface = None
    else:
        readings = [
            {"id": "ship-conditional", "label": "Proceed with conditions",
             "grounds": sorted(set(rooms)), "consequences": conditions},
            {"id": "hold", "label": "Hold until gaps are closed",
             "grounds": gaps,
             "consequences": ["no market exposure", "delay",
                              f"{len(conditions)} open condition(s) to clear"]}]
        surface = build_surface(question, [
            {"id": r["id"], "label": r["label"], "conclusion": r["label"],
             "supporting": [{"pinpoint": g} for g in r["grounds"]],
             "consequences": r["consequences"]} for r in readings],
            esc_reason=f"{len(gaps)} gap(s), {len(sub_cases) - closed} unresolved "
                       f"sub-case(s) — shipping is a residual, not a derivation")

    case = build_case(question, registry=registry, document=document,
                      required_rooms=sorted(set(rooms)) + gaps or None,
                      chain=chain, answer=None, surface=surface, choice=None,
                      facts=facts, actions=[], profile=profile,
                      oversight_level=oversight_level,
                      oversight_active=oversight_active,
                      stake=stake, personal=personal,
                      cited_entities={g.entity for c in sub_cases
                                      for g in c.grounds if g.entity})
    if all_green:
        case.resolution["proposed"] = readings[0]
        case.resolution["note"] = ("single compelled reading proposed — "
                                   "awaiting human ratification")
    inputs = {"question": question, "document": document,
              "rooms": sorted(set(rooms)) + gaps, "chain": chain, "facts": facts,
              "profile": profile, "oversight_level": oversight_level,
              "oversight_active": oversight_active, "stake": stake,
              "personal": personal, "readings": readings, "surface": surface,
              "cited_entities": {g.entity for c in sub_cases
                                 for g in c.grounds if g.entity}}
    return {"case": case, "inputs": inputs,
            "transcript": [{"phase": "gate",
                            "note": f"{len(sub_cases)} sub-case(s), {closed} closed, "
                                    f"{len(gaps)} gap(s) — "
                                    + ("ratification" if all_green else "decision")
                                    + " required"}]}


def cases_for_document(content: str, *, registry, document: str = "",
                       decisions: Optional[dict] = None,
                       max_rooms: int = 12) -> list[CaseRecord]:
    """End-to-end: a contract's text → one case record per clause.

    Each clause is placed as a span-norm; its **required rooms** are derived, not
    asserted: the articles/§§ the clause itself cites are matched against the
    norm-spans the registry actually holds — a held span is a receipted room, a
    cited provision whose text the corpus does NOT hold is a **gap** (reported,
    never hidden). A clause citing an instrument without a pinpoint requires every
    held span of that instrument (anchor-closure, corpus-relative, capped at
    ``max_rooms``). ``decisions`` maps clause index → {"answer": …} or
    {"surface": …, "choice": …}; clauses without one resolve as OPEN."""
    import re as _re
    decisions = decisions or {}
    res = registry.place_document(content, source_document=document, kind="clause")
    ids = {p["id"] for p in res["placed"]}
    clauses = [r for r in registry.workspace_items() if r["id"] in ids]
    out: list[CaseRecord] = []
    for i, cl in enumerate(clauses):
        text = cl["span"]["text"]
        cited = {a["entity"] for a in cl["anchors"] if a["relation"] == "cites"}
        spans = _norm_spans_for(registry, cited)
        art_refs = set(_re.findall(r"Art(?:icle|ikel)?\.?\s*(\d+[a-z]?)", text))
        para_refs = set(_re.findall(r"§\s*(\d+[a-z]?)", text))
        rooms: list[str] = []
        for s in spans:                       # held spans matching the cited articles
            pin = s["span"].get("pinpoint", "") or ""
            m = _re.search(r"(\d+[a-z]?)", pin)
            if not art_refs or (m and m.group(1) in art_refs):
                rooms.append(pin)
        rooms = sorted({r for r in rooms if r})[:max_rooms]
        held_arts = {_re.search(r"(\d+[a-z]?)", r).group(1) for r in rooms if _re.search(r"(\d+[a-z]?)", r)}
        for a in sorted(art_refs - held_arts):   # cited but text not held → gap room
            rooms.append(f"Art. {a}")
        for p in sorted(para_refs):              # national §§ cited; held only if ingested
            rooms.append(f"§ {p}")
        dec = decisions.get(i, {})
        out.append(build_case(text, registry=registry, document=document,
                              required_rooms=rooms or None,
                              chain=dec.get("chain"),
                              answer=dec.get("answer"),
                              surface=dec.get("surface"), choice=dec.get("choice"),
                              facts=dec.get("facts"), actions=dec.get("actions"),
                              profile=dec.get("profile", "legal-de"),
                              oversight_level=dec.get("oversight_level", "autonomous"),
                              stake=dec.get("stake", False),
                              personal=dec.get("personal", False)))
    return out


def render_case_record_html(cases: list[CaseRecord], *, document: str,
                            title: str = "Case record",
                            registry=None) -> str:
    """The printable, auditor-facing record: cover summary + one case per page
    (A4 print CSS), each with grounds/receipts, gaps, chain, resolution,
    rationale, actor, and a signature line. Print to PDF from the browser.
    With ``registry``, every citation is rendered human-readably (instrument
    titles, written-out articles, the provision's own words) and the cover
    carries the corpus-posture line — whose library this was checked against."""
    import json as _json
    total = len(cases)
    receipted = sum(1 for c in cases for g in c.grounds)
    gaps = sum(len(c.gaps) for c in cases)
    waived = sum(len(c.waivers) for c in cases)
    decided = sum(1 for c in cases if c.resolution["type"] == "determinate"
                  or (c.resolution["type"] == "residual" and c.resolution.get("choice")))
    cov = round(100 * (sum(c.coverage for c in cases) / total), 1) if total else 0.0
    posture = ""
    dicts = [c.to_dict() for c in cases]
    if registry is not None:
        from .humanize_legal import humanize_case, corpus_posture
        dicts = [humanize_case(d, registry) for d in dicts]
        posture = corpus_posture(registry)["line"]
    payload = _json.dumps(dicts)
    head = (f'<div class="cover"><h1>{title}</h1><div class="doc">{document}</div>'
            f'<table class="sum"><tr><td>Clauses examined</td><td>{total}</td></tr>'
            f'<tr><td>Rooms receipted</td><td>{receipted}</td></tr>'
            f'<tr><td>Open gaps</td><td class="{ "bad" if gaps else "ok" }">{gaps}</td></tr>'
            f'<tr><td>Resolved / decided</td><td>{decided} of {total}</td></tr>'
            f'<tr><td>Gaps waived (signed)</td><td>{waived}</td></tr>'
            f'<tr><td>Mean required-room coverage</td><td>{cov}%</td></tr></table>'
            + (f'<p class="note"><b>{posture}</b></p>' if posture else '')
            + f'<p class="note">Completeness is relative to the corpus held at generation '
            f'time. Every gap below is a cited provision whose text the corpus does not '
            f'hold or a required room without a read-receipt — listed, not hidden. '
            f'A waived gap shows who owned it and why.</p>'
            f'<div class="sig">Reviewed and signed: ______________________  date: ________</div></div>')
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>" + title + "</title><style>"
            "body{font-family:Georgia,serif;color:#111;margin:0}"
            ".cover,.case{padding:28mm 20mm;page-break-after:always}"
            "h1{font-size:22px;margin:0 0 4px}.doc{color:#555;margin-bottom:14px}"
            ".sum{border-collapse:collapse;margin:10px 0}.sum td{border:1px solid #999;padding:5px 12px;font-size:13px}"
            ".ok{color:#066}.bad{color:#a00;font-weight:bold}.note{font-size:11.5px;color:#444;max-width:150mm}"
            ".sig{margin-top:26mm;font-size:13px}"
            ".case h2{font-size:15px;margin:0 0 2px}.meta{color:#555;font-size:11px;margin-bottom:10px}"
            "h3{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#555;margin:14px 0 5px}"
            ".g{margin:4px 0;font-size:12.5px}.pin{font-weight:bold}.r{color:#066}.gap{color:#a00;font-weight:bold}"
            ".chain li{font-size:12.5px;margin:3px 0}.step{font-variant:small-caps;font-weight:bold}"
            ".res{border:1px solid #999;padding:8px 10px;font-size:12.5px;margin-top:6px}"
            ".rat{margin-top:6px;font-size:12px;border-left:3px solid #999;padding-left:8px}"
            "@media print{.case,.cover{padding:18mm 14mm}}"
            "</style></head><body>" + head + "<div id='cases'></div><script>"
            "const C=" + payload + ";const r=document.getElementById('cases');"
            "C.forEach((c,i)=>{const d=document.createElement('div');d.className='case';"
            "const facts=(c.facts||[]).length?'<h3>Facts</h3>'+c.facts.map(f=>`<div class='g'>${f.text} "
            "<span class='r'>[source: ${f.source}]</span></div>`).join(''):'';"
            "let g=c.grounds.map(x=>`<div class='g'><span class='pin'>${x.display||x.pinpoint}</span> "
            "<span class='r'>[receipt \\u2713]</span>${x.display?'':' '+(x.text||'')}</div>`).join('');"
            "g+=(c.gaps_display||c.gaps).map(x=>`<div class='g gap'>GAP — ${x}</div>`).join('');"
            "g+=(c.waivers||[]).map(w=>`<div class='g'><b>Waived:</b> ${w.gap} — owned by "
            "${w.actor}: ${w.rationale}</div>`).join('');"
            "const ch=c.chain.length?'<h3>Chain</h3><ol class=chain>'+c.chain.map(s=>`<li><span class='step'>${s.step}</span>: ${s.text}</li>`).join('')+'</ol>':'';"
            "let res='';if(c.resolution.type==='determinate'){res=`<div class='res'><b>Answer:</b> ${c.resolution.answer}</div>`;}"
            "else if(c.resolution.type==='residual'&&c.resolution.choice){const ch2=c.resolution.choice;"
            "res=`<div class='res'><b>Residual decision:</b> ${ch2.chosen_label}<div class='rat'><b>Rationale (${ch2.actor}):</b> ${ch2.rationale}</div></div>`;}"
            "else{res=`<div class='res gap'>OPEN — no decision recorded.</div>`;}"
            "const acts=(c.actions||[]).length?'<h3>Actions</h3>'+c.actions.map(a=>`<div class='g'>"
            "${a.display||(`<span class='pin'>${a.obligation}</span> — ${a.actor||''}"
            "${a.deadline?' by '+a.deadline:''} <span class='r'>[${a.source_norm}]</span>`)}</div>`).join(''):'';"
            "d.innerHTML=`<h2>Case ${i+1}: ${c.problem.text}</h2><div class='meta'>${c.problem.document||''} — coverage ${(100*c.coverage).toFixed(0)}% — profile ${c.profile||'legal-de'}</div>"
            "${facts}<h3>Grounds</h3>${g}${ch}<h3>Resolution</h3>${res}${acts}`;r.appendChild(d);});"
            "</script></body></html>")


# ── projection into the dimensioned pair/edge format ──────────────────────────
# project_pairs is the pure, corpus-free projection step — it belongs to
# loomground-solver (solver.case) and is consumed here through the solver seam,
# re-exported at the top of this module (see the import from adapters.solver.case).
