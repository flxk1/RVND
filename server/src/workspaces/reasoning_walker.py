# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The reasoning walker — a reasoning machine, never a judge.

The contract (:mod:`workspaces.reasoning_contract`) says what a justified answer
must look like; the phases (:mod:`workspaces.reasoning_phases`) teach each step in
model-sized bites; this module *walks* the loop:

    question → facts → norms → abstract schema → readings → [HUMAN] → action

Division of labour, deliberately uneven:

  * **deterministic where possible** — norm retrieval comes from the rule
    registry (anchors + held norm-spans), never from a model's memory;
    assembly and gating go through :func:`workspaces.problem_kg.build_case`.
  * **model where useful** — fact extraction, schema building, laying out the
    readings go to an injected ``model_fn(prompt) -> str``. Any model fits
    (local Phi/Qwen via local_llm, or a hosted model); the walker hands it
    ONE phase brief + the phase inputs and expects strict JSON back.
  * **never the judge** — the walker performs NO automated decision making.
    The application phase emits an **abstract schema** (the frame any case of
    this kind must walk — criteria as questions, no subsumption, no
    conclusion). The resolution phase lays out the supported **readings**,
    none preferred. Every walk returns awaiting a human:

        - one reading   → :func:`ratify`  (human confirms, with rationale)
        - n ≥ 2 readings → :func:`decide` (human chooses, with rationale)
        - no reading    → stays OPEN

    Only after the human input does the action phase run. Repairs are
    deterministic and logged: an unanchored action is dropped, an unsourced
    fact quarantined — visibly, never silently.

Every phase (prompt, raw reply, parsed result, repairs) lands in the
transcript, so the walk itself is auditable. What the user must be SHOWN at
each oversight level is :data:`workspaces.reasoning_contract.INFORMATION_FORMS`.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from . import problem_kg, reasoning_phases
from .decisions.surface import build_surface, record_choice


ModelFn = Callable[[str], str]


def _parse_json(raw: str) -> dict:
    """Tolerant JSON extraction: models wrap JSON in prose/fences. Find the
    outermost object; on failure return {} (the walker treats it as a refusal)."""
    if not raw:
        return {}
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _ask(model_fn: Optional[ModelFn], phase: str, payload: dict, *,
         profile: str, transcript: list) -> dict:
    """Hand the model ONE phase: brief + inputs. Log everything."""
    if model_fn is None:
        transcript.append({"phase": phase, "skipped": "no model injected"})
        return {}
    prompt = (reasoning_phases.brief(phase, profile=profile)
              + "\n\nINPUT:\n" + json.dumps(payload, ensure_ascii=False, indent=1))
    raw = model_fn(prompt)
    parsed = _parse_json(raw)
    transcript.append({"phase": phase, "prompt_chars": len(prompt),
                       "raw": (raw or "")[:2000], "parsed": parsed})
    return parsed


def _norm_spans(registry, question: str, document: str,
                cited_entities: Optional[set] = None) -> list[dict]:
    """Deterministic norm retrieval: place the question, follow its cites.

    ``cited_entities`` unions extra anchor codes into the retrieval set — the
    hook a caller uses when the norms live at a pseudo-instrument the question's
    own prose does not cite (plain-language policy placed at ``policy:<slug>``)."""
    placed = registry.place_span(question, source_document=document, kind="clause")
    cited = {a["entity"] for a in placed["anchors"] if a["relation"] == "cites"}
    cited |= set(cited_entities or ())
    return problem_kg._norm_spans_for(registry, cited)


def _readings_to_surface(question: str, readings: list[dict], esc_reason: str):
    opts = [{"id": r.get("id") or f"r{i}", "label": r.get("label", ""),
             "conclusion": r.get("label", ""),
             "supporting": [{"pinpoint": g} for g in (r.get("grounds") or [])],
             "consequences": r.get("consequences") or []}
            for i, r in enumerate(readings)]
    return build_surface(question, opts, esc_reason=esc_reason)


def walk(task: str, *, registry, document: str = "",
         model_fn: Optional[ModelFn] = None,
         fetch_fn=None,
         facts: Optional[list[dict]] = None,
         profile: str = "legal-de",
         oversight_level: str = "autonomous", oversight_active: bool = True,
         stake: bool = False, personal: bool = False,
         cited_entities: Optional[set] = None,
         max_rooms: int = 12) -> dict:
    """Run the loop up to the human boundary. Returns
    ``{"case": CaseRecord, "transcript": [...], "inputs": {...}}`` — the case
    is ALWAYS awaiting a human (residual with options, or open): the walker
    makes no decision. Continue with :func:`ratify` or :func:`decide`.

    ``facts`` short-circuits phase 2 (caller already has evidenced facts).
    ``cited_entities`` adds anchor codes to norm retrieval for norms the
    question does not cite in its own prose (policy placed at a pseudo-instrument).
    Without a ``model_fn`` the walk is purely deterministic: norms are
    retrieved and receipted, the case stays OPEN — honest, useless for
    answers, perfect for coverage audits."""
    transcript: list[dict] = []

    # P1 — question
    q = _ask(model_fn, "question", {"task": task}, profile=profile,
             transcript=transcript)
    if q.get("refuse"):
        return {"case": None, "transcript": transcript, "inputs": {},
                "refused": q["refuse"]}
    question = (q.get("question") or task).strip()

    # P2 — facts (caller-supplied wins; model extracts otherwise)
    quarantined: list[str] = []
    if facts is None:
        f = _ask(model_fn, "facts", {"question": question, "context": task},
                 profile=profile, transcript=transcript)
        facts = [x for x in (f.get("facts") or [])
                 if (x.get("text") or "").strip() and (x.get("source") or "").strip()]
        # R1 repair, visible: a model fact without a source is quarantined
        for x in (f.get("facts") or []):
            if (x.get("text") or "").strip() and not (x.get("source") or "").strip():
                quarantined.append(x["text"])
        quarantined += [t for t in (f.get("unsourced") or []) if t]
        if quarantined:
            transcript.append({"phase": "facts", "repair":
                               f"{len(quarantined)} unsourced fact(s) quarantined (R1)",
                               "quarantined": quarantined})

    # P3 — norms (retrieval is deterministic; the model only selects/flags gaps)
    spans = _norm_spans(registry, question, document, cited_entities)
    menu = [{"pinpoint": s["span"].get("pinpoint", ""),
             "text": s["span"]["text"][:200]} for s in spans][:max_rooms]
    n = _ask(model_fn, "norms", {"question": question, "facts": facts,
                                 "norm_spans": menu},
             profile=profile, transcript=transcript)
    held = {m["pinpoint"] for m in menu if m["pinpoint"]}
    selected = [p for p in (n.get("selected") or []) if p in held] or sorted(held)
    rooms = selected + [g for g in (n.get("gaps") or []) if g and g not in held]

    # gap closure by FETCH — public-law legwork is the system's job, not the
    # user's. For each cited-but-not-held provision, try the injected fetcher
    # (host-policy-bound); a successful fetch ingests per-article and the room
    # becomes receipted on assembly. Failures stay honest gaps.
    if fetch_fn is not None:
        placed_q = registry.place_span(question, source_document=document,
                                       kind="clause")
        inst = next((a["entity"] for a in placed_q["anchors"]
                     if a["relation"] == "cites"), "")
        for g in [r for r in rooms if r not in held]:
            got = fetch_fn(inst, g)
            if got and (got.get("text") or "").strip():
                registry.place_legal_text(got["text"], inst,
                                          source_document=got.get("url", "fetched"))
                transcript.append({"phase": "norms",
                                   "note": f"gap {g} closed by fetch — "
                                           f"{got.get('url', 'source')} ingested, "
                                           f"receipt minted"})
            else:
                transcript.append({"phase": "norms",
                                   "note": f"gap {g}: fetcher declined — stays "
                                           f"an open gap (close by ingest or "
                                           f"waive with reasons)"})

    # P4 — the ABSTRACT schema: the frame, never the outcome. The facts are
    # deliberately NOT handed to this phase — the schema must be case-free.
    a = _ask(model_fn, "application", {"question": question,
                                       "norms": [m for m in menu
                                                 if m["pinpoint"] in set(selected)]},
             profile=profile, transcript=transcript)
    chain = []
    for s in (a.get("chain") or []):
        if (s.get("step") or "").strip() and (s.get("text") or "").strip():
            s = dict(s)
            s["schema"] = True            # a frame, not a finding
            chain.append(s)

    # P5 — readings: laid out, never chosen. The machine stops here.
    r = _ask(model_fn, "resolution", {"question": question, "schema": chain,
                                      "facts": facts},
             profile=profile, transcript=transcript)
    readings = [x for x in (r.get("readings") or [])
                if (x.get("label") or "").strip()]
    surface = None
    if len(readings) >= 2:
        surface = _readings_to_surface(question, readings,
                                       r.get("esc_reason", "multiple supported readings"))
        transcript.append({"phase": "resolution",
                           "note": f"{len(readings)} readings — surface built, "
                                   "NO choice made; a human decides (R4)"})
    elif len(readings) == 1:
        transcript.append({"phase": "resolution",
                           "note": "single compelled reading — proposed, NOT "
                                   "emitted; awaiting human ratification (§4a.9)"})
    else:
        transcript.append({"phase": "resolution",
                           "note": "no reading closes the schema — OPEN: "
                                   + (r.get("why_open") or "unstated")})

    case = problem_kg.build_case(
        question, registry=registry, document=document,
        required_rooms=rooms or None, chain=chain,
        answer=None, surface=surface, choice=None,
        facts=facts, actions=[], profile=profile,
        cited_entities=cited_entities,
        oversight_level=oversight_level, oversight_active=oversight_active,
        stake=stake, personal=personal)
    if len(readings) == 1:
        case.resolution["proposed"] = readings[0]
        case.resolution["note"] = ("single compelled reading proposed — "
                                   "awaiting human ratification")
    if quarantined:
        case.contract.setdefault("quarantined_facts", quarantined)

    inputs = {"question": question, "document": document, "rooms": rooms,
              "chain": chain, "facts": facts, "profile": profile,
              "oversight_level": oversight_level,
              "oversight_active": oversight_active,
              "stake": stake, "personal": personal,
              "cited_entities": cited_entities,
              "readings": readings, "surface": surface}
    return {"case": case, "transcript": transcript, "inputs": inputs}


# ── the human boundary: ratify / decide, then (and only then) actions ─────────

def _actions_phase(model_fn: Optional[ModelFn], *, registry, question: str,
                   answer: str, rooms: list, profile: str,
                   transcript: list) -> list[dict]:
    # derived-first: the deontic tail comes from the held norm-spans
    # (modal/actor/deadline/enforced_by), never from prose, whenever it can.
    derived = problem_kg.derive_actions(registry, rooms)
    if derived:
        transcript.append({"phase": "action",
                           "note": f"{len(derived)} action(s) DERIVED from the "
                                   "deontic structure of the held norm-spans — "
                                   "model not asked"})
        return derived
    act = _ask(model_fn, "action", {"question": question, "answer": answer,
                                    "norms": rooms},
               profile=profile, transcript=transcript)
    actions: list[dict] = []
    for x in (act.get("actions") or []):
        if (x.get("obligation") or "").strip() and (x.get("source_norm") or "").strip():
            actions.append({"obligation": x["obligation"],
                            "actor": x.get("actor", ""),
                            "deadline": x.get("deadline", ""),
                            "source_norm": x["source_norm"]})
        elif (x.get("obligation") or "").strip():
            transcript.append({"phase": "action", "repair":
                               f"dropped unanchored action (R5): {x['obligation'][:80]}"})
    return actions


def _rebuild(result: dict, *, registry, answer=None, surface=None, choice=None,
             actions=None, extra_resolution: Optional[dict] = None):
    i = result["inputs"]
    case = problem_kg.build_case(
        i["question"], registry=registry, document=i["document"],
        required_rooms=i["rooms"] or None, chain=i["chain"],
        answer=answer, surface=surface, choice=choice,
        facts=i["facts"], actions=actions or [], profile=i["profile"],
        cited_entities=i.get("cited_entities"),
        oversight_level=i["oversight_level"], oversight_active=i["oversight_active"],
        stake=i["stake"], personal=i["personal"])
    if extra_resolution:
        case.resolution.update(extra_resolution)
    return case


def ratify(result: dict, *, registry, actor: str, rationale: str,
           model_fn: Optional[ModelFn] = None) -> dict:
    """A human ratifies the single proposed reading (§4a.9: ratification —
    intelligible grounds confirmed). Requires an actor and a non-empty
    rationale; then, and only then, the action phase runs."""
    readings = result["inputs"]["readings"]
    if len(readings) != 1:
        raise ValueError(f"ratify needs exactly one proposed reading, "
                         f"found {len(readings)} — use decide()")
    if not (actor or "").strip() or not (rationale or "").strip():
        raise ValueError("ratification requires actor and rationale (no rubber stamp)")
    reading = readings[0]
    answer = reading.get("label", "").strip()
    transcript = result["transcript"]
    transcript.append({"phase": "ratification", "actor": actor,
                       "rationale": rationale, "reading": reading})
    actions = _actions_phase(model_fn, registry=registry,
                             question=result["inputs"]["question"],
                             answer=answer, rooms=result["inputs"]["rooms"],
                             profile=result["inputs"]["profile"],
                             transcript=transcript)
    case = _rebuild(result, registry=registry, answer=answer, actions=actions,
                    extra_resolution={"ratified_by": actor,
                                      "rationale": rationale,
                                      "grounds": reading.get("grounds", [])})
    return {"case": case, "transcript": transcript, "inputs": result["inputs"]}


def decide(result: dict, *, registry, chosen_option_id: str, actor: str,
           rationale: str, model_fn: Optional[ModelFn] = None) -> dict:
    """A human chooses among the readings (§4a.9: origination). Routed through
    decision_surface.record_choice — option must be real, rationale non-empty,
    the choice is signed. Then the action phase runs for the chosen reading."""
    surface = result["inputs"]["surface"]
    if surface is None:
        raise ValueError("no decision surface on this walk — use ratify() "
                         "for a single reading, or the case is OPEN")
    choice = record_choice(surface, chosen_option_id=chosen_option_id,
                           rationale=rationale, actor=actor)
    transcript = result["transcript"]
    transcript.append({"phase": "decision", "choice": choice})
    chosen = next(r for r in result["inputs"]["readings"]
                  if (r.get("id") or "") == chosen_option_id
                  or r.get("label") == choice.get("chosen_label"))
    actions = _actions_phase(model_fn, registry=registry,
                             question=result["inputs"]["question"],
                             answer=chosen.get("label", ""),
                             rooms=result["inputs"]["rooms"],
                             profile=result["inputs"]["profile"],
                             transcript=transcript)
    case = _rebuild(result, registry=registry, surface=surface, choice=choice,
                    actions=actions)
    return {"case": case, "transcript": transcript, "inputs": result["inputs"]}
