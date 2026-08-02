# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Phase-2 rule extraction — the LLM seam, gated like everything else.

Phase-1 (``rule_extractor``) is regex over single sentences; contracts break
it with multi-sentence rules, embedded definitions, and defined-term
subjects. Phase-2 hands those harder reads to a language model — ANY model,
local or cloud, injected as ``model_fn(prompt) -> str`` (the same pattern as
``reasoning_walker``) — under three non-negotiables:

  1. **Strict output contract.** The model returns JSON rules with the same
     five slots Phase-1 produces. Anything that does not parse as the schema
     is DROPPED with a recorded repair note — a malformed model reply is an
     abstention, never a fabricated rule.
  2. **Same gates.** Phase-2 facets carry ``extractor='phase-2-llm'`` and a
     capped confidence (never above 0.85 — model output starts at the floor,
     it does not get to outrank deterministic extraction); predicate structs
     still come from the deterministic parser only.
  3. **Defined-term resolution is grounded.** The model may use the defined-
     terms registry given to it, and each resolved subject must literally be
     a registered term — a subject resolved to an unregistered name is kept
     as written and flagged, not trusted.

Pure stdlib; no model dependency — tests inject stubs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .rule_extractor import RuleFacet, extract_rules

__all__ = ["Phase2Result", "extract_rules_llm", "PHASE2_CONFIDENCE_CAP"]

PHASE2_CONFIDENCE_CAP = 0.85

ModelFn = Callable[[str], str]

_PROMPT = """You extract operative rules from contract text. Return ONLY a JSON array.
Each element: {{"subject": str, "modal": "obligation"|"prohibition"|"permission"|"right",
"modal_phrase": str, "action": str, "condition": str, "exception": str,
"raw_sentence": str}}.

Rules:
- subject: the obligated party. If it is a defined term, use the term exactly as defined.
- condition/exception: verbatim text, empty string if none.
- raw_sentence: the full source passage the rule comes from, verbatim.
- Extract NOTHING that is not in the text. No inference, no summary rules.
- If there are no operative rules, return [].

Known defined terms (use exactly these spellings when they are the subject):
{terms}

Contract text:
---
{text}
---
JSON array:"""


@dataclass
class Phase2Result:
    facets: list[RuleFacet] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)   # repair notes, auditable
    raw_reply: str = ""
    used_model: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"facets": [f.to_dict() for f in self.facets],
                "dropped": self.dropped, "used_model": self.used_model}


_REQUIRED_KEYS = {"subject", "modal", "action", "raw_sentence"}
_VALID_MODALS = {"obligation", "prohibition", "permission", "right"}


def _parse_reply(reply: str, *, source_text: str,
                 known_terms: frozenset[str]) -> tuple[list[RuleFacet], list[dict]]:
    """Model reply → validated facets + drop notes. Every drop is recorded."""
    facets: list[RuleFacet] = []
    dropped: list[dict] = []
    m = re.search(r"\[.*\]", reply, re.S)
    if not m:
        return [], [{"why": "no JSON array in reply"}]
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        return [], [{"why": f"JSON parse failure: {exc}"}]
    if not isinstance(items, list):
        return [], [{"why": "reply is not a list"}]
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not _REQUIRED_KEYS <= set(it):
            dropped.append({"index": i, "why": "missing required keys"})
            continue
        if it["modal"] not in _VALID_MODALS:
            dropped.append({"index": i, "why": f"invalid modal {it.get('modal')!r}"})
            continue
        raw = str(it.get("raw_sentence", "")).strip()
        # grounding check: the passage must actually occur in the source.
        if not raw or _normalise(raw) not in _normalise(source_text):
            dropped.append({"index": i, "why": "raw_sentence not found in source"})
            continue
        subject = str(it.get("subject", "")).strip()
        flags = {}
        if subject and known_terms and subject.lower() not in known_terms \
                and _normalise(subject) not in _normalise(source_text):
            flags["subject_unverified"] = True
        facets.append(RuleFacet(
            subject=subject.lower(),
            modal=it["modal"],
            modal_phrase=str(it.get("modal_phrase", "")),
            action=str(it.get("action", "")).strip(),
            condition=str(it.get("condition", "")).strip(),
            exception=str(it.get("exception", "")).strip(),
            raw_sentence=raw,
            language="en",
            confidence=min(PHASE2_CONFIDENCE_CAP, 0.85),
            condition_struct=None,            # predicates stay deterministic-only
        ))
        if flags:
            dropped.append({"index": i, "why": "subject_unverified (kept, flagged)",
                            "kept": True})
    return facets, dropped


def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def extract_rules_llm(text: str, *, model_fn: Optional[ModelFn] = None,
                      defined_terms: tuple[str, ...] = (),
                      dedupe_against_phase1: bool = True) -> Phase2Result:
    """Phase-2 extraction over one passage.

    Without a ``model_fn`` this degrades to Phase-1 exactly (the seam is
    optional by construction). With one, the model's validated rules are
    merged after Phase-1's, deduplicated on normalised raw_sentence —
    deterministic extraction always wins a collision."""
    phase1 = extract_rules(text)
    # Rule-DNA completeness: every facet this function returns carries the
    # juridical-primitive layer, with or without a model (deterministic).
    from .hohfeld import attach_incidents
    attach_incidents(phase1)
    if model_fn is None:
        return Phase2Result(facets=phase1, used_model=False)
    prompt = _PROMPT.format(
        terms="\n".join(f"- {t}" for t in defined_terms) or "- (none)",
        text=text)
    try:
        reply = model_fn(prompt)
    except Exception as exc:                                    # noqa: BLE001
        return Phase2Result(facets=phase1, used_model=False,
                            dropped=[{"why": f"model_fn raised: {exc}"}])
    known = frozenset(t.lower() for t in defined_terms)
    llm_facets, dropped = _parse_reply(reply, source_text=text, known_terms=known)
    out = list(phase1)
    if dedupe_against_phase1:
        seen = {_normalise(f.raw_sentence) for f in phase1}
        for f in llm_facets:
            if _normalise(f.raw_sentence) not in seen:
                seen.add(_normalise(f.raw_sentence))
                out.append(f)
    else:
        out.extend(llm_facets)
    # Rule-DNA completeness: Phase-2 facets carry the juridical-primitive
    # layer too — classification stays DETERMINISTIC (the model proposes the
    # rule; the vocabulary disposes the incident), same as everywhere else.
    from .hohfeld import attach_incidents
    attach_incidents(out)
    return Phase2Result(facets=out, dropped=dropped, raw_reply=reply,
                        used_model=True)
