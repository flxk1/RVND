# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Decision extraction — the choices an instrument forces on its reader.

A regulation does not only impose duties; it also opens **choices** the
addressee has to make: a derogation a Member State may take up, an option to
appoint or not appoint a DPO, a legal-basis selection, a "may, by means of
delegated acts, …" discretion, an opt-out. NotebookLM-grade analysis surfaces
these as *decisions to be made* so a reader sees not just "what must I do" but
"what do I have to decide".

This is distinct from:

- :mod:`.action_gate` — that gates *agent runtime actions* (GO/CONDITIONAL/
  NO-GO). This module reads the *text* for *human/organisational* decision
  points.
- :mod:`.deontic` — a permission ``P(a)`` is a single liberty; a *decision* is
  a fork between two or more options, often "do X or do Y", "X unless you
  elect Y", a discretion granted to an authority, or a Member-State derogation.

Model (shape borrowed from The Federation ``cell_decision`` — Criterion /
Alternative — so the Workspace lineage stays 1:1, but the *extraction* is new):

    DecisionPoint
        decider     — who chooses (deployer / Member State / the Commission / …)
        question    — the choice, phrased as a question
        options[]   — the alternatives (Option.label + Option.consequence)
        trigger     — the condition under which the choice arises
        kind        — derogation | discretion | option | election | opt-out
        confidence

We extract the *fork*, never recommend an option — "ship the law, never the
resolution". Which option to take is the user's judgment, gated by oversight.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from workspaces.adapters.solver.dimensions import Dimension
from ..nd_routing import BaseNDDispatcher


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class Option:
    """One alternative in a decision (a Federation 'Alternative')."""
    label: str
    consequence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionPoint:
    decider: str
    question: str
    options: list[Option] = field(default_factory=list)
    trigger: str = ""
    kind: str = "option"          # derogation|discretion|option|election|opt-out
    raw_sentence: str = ""
    language: str = "en"
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["options"] = [o.to_dict() for o in self.options]
        return d


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Deciders we recognise as choice-holders, longest first.
_DECIDERS = (
    "member states", "member state", "the commission", "the council",
    "supervisory authority", "competent authority", "national authority",
    "the controller", "the processor", "the provider", "the deployer",
    "the operator", "the parties", "each party", "the licensee", "the licensor",
)

_DECIDER_RE = re.compile(
    r"\b(" + "|".join(re.escape(d) for d in _DECIDERS) + r")\b", re.IGNORECASE,
)

# Discretion / choice cues. Each maps to a decision kind.
_CHOICE_CUES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmay\s+derogat\w+\b", re.I), "derogation"),
    (re.compile(r"\bmay\s+provide\s+(?:for|that)\b", re.I), "derogation"),
    (re.compile(r"\bmay,?\s+by\s+(?:means\s+of\s+)?(?:delegated|implementing)\s+acts?\b", re.I), "discretion"),
    (re.compile(r"\bmay\s+(?:choose|elect|decide|opt)\b", re.I), "election"),
    (re.compile(r"\bmay\s+(?:adopt|maintain|introduce|lay\s+down)\b", re.I), "discretion"),
    (re.compile(r"\bmay\s+(?:require|allow|permit)\b", re.I), "discretion"),
    (re.compile(r"\bmay\b", re.I), "option"),
    (re.compile(r"\bmay\s+object\b", re.I), "opt-out"),
    (re.compile(r"\bopt[\s\-]?out\b", re.I), "opt-out"),
    (re.compile(r"\bat\s+(?:its|their|the)\s+discretion\b", re.I), "discretion"),
)

# Explicit "either … or …" / "X or Y" fork.
_EITHER_OR_RE = re.compile(
    r"\beither\s+(?P<a>[^,.;]{3,80}?)\s+or\s+(?P<b>[^,.;]{3,120}?)(?:[.;,]|\Z)",
    re.IGNORECASE,
)
# "shall choose between A and B"
_BETWEEN_RE = re.compile(
    r"\bchoos\w+\s+between\s+(?P<a>[^,.;]{3,80}?)\s+and\s+(?P<b>[^,.;]{3,120}?)(?:[.;]|\Z)",
    re.IGNORECASE,
)

# Trigger / condition connectives (shared with the rule extractor's spirit).
_TRIGGER_RE = re.compile(
    r"\b(?:where|if|when|in\s+the\s+case\s+(?:of|where)|provided\s+that|"
    r"sofern|soweit|wenn|falls)\s+(?P<cond>[^.;]{3,140})",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÄÖÜ])")


def _find_decider(sentence: str) -> str:
    m = _DECIDER_RE.search(sentence)
    return m.group(1).lower() if m else "the addressee"


def _find_trigger(sentence: str) -> str:
    m = _TRIGGER_RE.search(sentence)
    return m.group("cond").strip() if m else ""


def _options_from_sentence(sentence: str) -> list[Option]:
    """Pull explicit forks (either/or, choose-between). When none, the choice
    is binary act/abstain — represented as a single 'exercise the option'
    Option, because the abstain branch is implicit."""
    opts: list[Option] = []
    m = _EITHER_OR_RE.search(sentence)
    if m:
        opts = [Option(m.group("a").strip()), Option(m.group("b").strip())]
    if not opts:
        m = _BETWEEN_RE.search(sentence)
        if m:
            opts = [Option(m.group("a").strip()), Option(m.group("b").strip())]
    return opts


def _question_from(decider: str, sentence: str, kind: str) -> str:
    verb = {
        "derogation": "take up the derogation",
        "discretion": "exercise the discretion",
        "election": "make the election",
        "opt-out": "opt out",
        "option": "exercise the option",
    }.get(kind, "decide")
    return f"Should {decider} {verb}?"


def extract_decisions(content: str) -> list[DecisionPoint]:
    """Extract decision points an instrument forces on its reader.

    Sentence-segmenting; a sentence is a decision candidate when it pairs a
    recognised decider with a choice cue (or contains an explicit either/or
    fork). One :class:`DecisionPoint` per qualifying sentence.
    """
    out: list[DecisionPoint] = []
    seen: set[str] = set()
    for sentence in _SENTENCE_SPLIT.split(content.strip()):
        s = sentence.strip()
        if len(s) < 12:
            continue

        kind: Optional[str] = None
        for pat, k in _CHOICE_CUES:
            if pat.search(s):
                kind = k
                break

        options = _options_from_sentence(s)
        # Qualify: need either a choice cue OR an explicit fork.
        if kind is None and not options:
            continue
        if kind is None:
            kind = "option"

        decider = _find_decider(s)
        trigger = _find_trigger(s)

        key = (decider + "::" + s[:60]).lower()
        if key in seen:
            continue
        seen.add(key)

        # Confidence: a recognised decider + a strong choice cue + a fork all
        # raise it. Bare "may" with no decider is weak.
        conf = 0.5
        if _DECIDER_RE.search(s):
            conf += 0.2
        if kind in ("derogation", "discretion", "election", "opt-out"):
            conf += 0.15
        if options:
            conf += 0.15
        conf = round(min(1.0, conf), 3)

        out.append(DecisionPoint(
            decider=decider,
            question=_question_from(decider, s, kind),
            options=options,
            trigger=trigger,
            kind=kind,
            raw_sentence=s,
            confidence=conf,
        ))
    return out


# ---------------------------------------------------------------------------
# ND dispatcher
# ---------------------------------------------------------------------------

def _hash_pair(content: str, nd_id: str, source: str | None) -> str:
    h = hashlib.sha256()
    h.update(nd_id.encode("utf-8")); h.update(b"|")
    h.update((source or "inline").encode("utf-8")); h.update(b"|")
    h.update(content.encode("utf-8"))
    return "sha256:" + h.hexdigest()[:32]


def _edge(subject: str, predicate: str, obj: str, dimension: Dimension) -> dict[str, Any]:
    return {"subject": subject, "predicate": predicate, "object": obj,
            "dimension": dimension.value}


class DecisionExtractor(BaseNDDispatcher):
    """ND that surfaces the decisions an instrument forces on its reader.

    Produces ``kind=decision-point`` pairs. Each carries the decider, the
    question, any explicit options, and the trigger — plus an INTENTIONAL edge
    (a decision exists *for* its decider) and, when triggered, a CAUSAL edge
    (the trigger *raises* the decision).
    """

    nd_id = "nd-decision"
    handles_types = ["normative", "document"]
    handles_facets: list[str] = []
    confidence_floor = 0.45

    def extract(self, content, classification, *, source_document=None):
        decisions = extract_decisions(content)
        base = _hash_pair(content, self.nd_id, source_document)
        out: list[dict[str, Any]] = []
        for idx, d in enumerate(decisions):
            pid = f"{base}-dec{idx}"
            dd = d.to_dict()
            edges = [_edge(pid, "decided-by", d.decider, Dimension.INTENTIONAL)]
            if d.trigger:
                edges.append(_edge(pid, "raised-when", d.trigger, Dimension.CAUSAL))
            for o in d.options:
                edges.append(_edge(pid, "has-option", o.label, Dimension.RELATIONAL))
            out.append({
                "id": pid,
                "problem": {
                    "id": f"{pid}-p",
                    "kind": "decision-point",
                    "scope": "decision",
                    "type": "mental-model",
                    "summary": d.question,
                    "facets": {
                        "decider": d.decider,
                        "decision_kind": d.kind,
                        "option_count": len(d.options),
                        "has_trigger": bool(d.trigger),
                        "language": d.language,
                    },
                    "context": {"kind_of_model": "decision-point"},
                },
                "solution": {
                    "id": pid,
                    "problem_id": f"{pid}-p",
                    "decider": d.decider,
                    "question": d.question,
                    "decision_kind": d.kind,
                    "options": dd["options"],
                    "trigger": d.trigger,
                    "body": _render_decision(d),
                    "body_format": "structured-decision",
                    "authority_tier": 1,
                    "confidence": d.confidence,
                },
                "edges": edges,
            })
        return out


def _render_decision(d: DecisionPoint) -> str:
    lines = [f"DECISION ({d.kind})", f"decider: {d.decider}", f"question: {d.question}"]
    if d.trigger:
        lines.append(f"trigger: {d.trigger}")
    if d.options:
        lines.append("options:")
        for o in d.options:
            lines.append(f"  - {o.label}" + (f" → {o.consequence}" if o.consequence else ""))
    return "\n".join(lines)


def register_decision_nd(router) -> None:
    """Register the decision-point ND on a router."""
    router.register(DecisionExtractor())
