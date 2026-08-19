# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Oversight ND — IN face: compile oversight requirements from normative text.

Rule ND answers *what does the norm say* (subject, modal, action, condition,
exception). Oversight ND answers *who must watch, at what level, observed
how* — and emits :class:`OversightFacet` pairs that are the fingerprint
fragments the gate and the policy layer consume (docs:
``OVERSIGHT-SUBSTRATE-ALGEBRA.md`` §8, ``OVERSIGHT-TRIGGER-TAXONOMY.md``).

Deterministic, regex-only, EN + DE — same Phase-1 posture as
:mod:`rvnd.rule_extractor`. Where extraction misses, the ND emits nothing:
the Rule NDs already preserve the audit floor with umbrella pairs, so
Oversight ND never duplicates content it cannot type.

Level semantics (total order):

    AUTONOMOUS < NOTIFY < REVIEW < APPROVE < SUPERVISED < MANUAL

``min_level`` is a floor on the oversight dial; ``grade_ceiling`` is a cap on
the autonomy grade (L0–L4) admissible under the source. Both compose by
lattice (floors join strictest-wins, ceilings meet lowest-wins); deontic
conflicts never lattice-compose — they route to ESCALATE (norm contract).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

OVERSIGHT_LEVELS = (
    "AUTONOMOUS", "NOTIFY", "REVIEW", "APPROVE", "SUPERVISED", "MANUAL")
_LEVEL_ORDER = {lvl: i for i, lvl in enumerate(OVERSIGHT_LEVELS)}

from .adapters.policy_languages import grade_index as _grade_index

_GRADE_ORDER = _grade_index()  # grade lattice consumed from governance's grammar


@dataclass
class OversightFacet:
    """One oversight requirement extracted from normative content."""

    overseer: str = ""
    """Role or human class entitled to the gate ("the controller",
    "der Verantwortliche"). Empty when the source names none."""

    trigger: str = ""
    """Condition clause that engages the requirement, when stated."""

    min_level: str = ""
    """Floor on the oversight dial (one of :data:`OVERSIGHT_LEVELS`)."""

    grade_ceiling: str = ""
    """Cap on the autonomy grade (L0–L4) admissible under the source.
    Empty = the source caps nothing."""

    measure: str = ""
    """What must be observable (logging / documentation duty), when stated."""

    cadence: str = ""
    """Review rhythm, when the source sets one ("quarterly", "regelmäßig")."""

    personal: bool = False
    """Non-delegable (höchstpersönlich) / right to a human decision."""

    raw_sentence: str = ""
    language: str = "en"
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)
    """Names of the patterns that fired — the extraction's own receipts."""


# ── pattern table ────────────────────────────────────────────────────────────
# Each entry: (signal name, compiled pattern, contribution dict).
# Contributions: level=<floor>, ceiling=<cap>, personal=True,
# overseer/measure from named groups.

_OVERSEER = r"(?P<overseer>[^,.;:\n]{3,60})"

_PATTERNS: list[tuple[str, re.Pattern[str], dict[str, Any]]] = [
    # ── APPROVE — prior human sign-off ──────────────────────────────────────
    ("approval_en", re.compile(
        r"(?:requires?|require|subject\s+to|conditional\s+(?:up)?on)\s+"
        r"(?:the\s+)?(?:prior\s+|express\s+|written\s+)*"
        r"(?:approval|authori[sz]ation|consent|sign-?off)"
        r"(?:\s+(?:of|by|from)\s+" + _OVERSEER + r")?",
        re.IGNORECASE), {"level": "APPROVE"}),
    ("approval_by_en", re.compile(
        r"shall\s+(?:first\s+)?be\s+(?:approved|authori[sz]ed|signed\s+off|"
        r"validated)\s+by\s+" + _OVERSEER,
        re.IGNORECASE), {"level": "APPROVE"}),
    ("approval_de", re.compile(
        r"bedarf\s+der\s+(?:vorherigen\s+|ausdrücklichen\s+|schriftlichen\s+)*"
        r"(?:zustimmung|genehmigung|einwilligung|freigabe)"
        r"(?:\s+(?:des|der|durch\s+den|durch\s+die|durch)\s+" + _OVERSEER + r")?",
        re.IGNORECASE), {"level": "APPROVE"}),
    ("approval_de_2", re.compile(
        r"(?:ist|sind)\s+(?:vorab\s+|zuvor\s+)?(?:durch|von)\s+" + _OVERSEER +
        r"\s+(?:zu\s+genehmigen|freizugeben|zu\s+bestätigen)",
        re.IGNORECASE), {"level": "APPROVE"}),

    # ── REVIEW — human oversight / monitoring ───────────────────────────────
    ("oversight_en", re.compile(
        r"(?:effective(?:ly)?\s+)?(?:human\s+oversight|overseen\s+by\s+natural"
        r"\s+persons|effectively\s+overseen)",
        re.IGNORECASE), {"level": "REVIEW"}),
    ("review_en", re.compile(
        r"shall\s+be\s+(?:reviewed|monitored|supervised|checked)"
        r"(?:\s+by\s+" + _OVERSEER + r")?",
        re.IGNORECASE), {"level": "REVIEW"}),
    ("oversight_de", re.compile(
        r"(?:menschliche\s+aufsicht|wirksam\s+beaufsichtig\w*|"
        r"(?:ist|sind)\s+(?:regelmäßig\s+)?zu\s+überprüfen|"
        r"(?:wird|werden)\s+(?:laufend\s+)?überwacht)",
        re.IGNORECASE), {"level": "REVIEW"}),

    # ── NOTIFY — information duties toward an overseer ──────────────────────
    ("notify_en", re.compile(
        r"shall\s+(?:notify|inform|report\s+to)\s+" + _OVERSEER,
        re.IGNORECASE), {"level": "NOTIFY"}),
    ("notify_de", re.compile(
        r"(?:ist|sind)\s+" + _OVERSEER +
        r"\s+(?:zu\s+melden|anzuzeigen|mitzuteilen|zu\s+unterrichten)",
        re.IGNORECASE), {"level": "NOTIFY"}),

    # ── MANUAL — human decision / intervention / discretion ────────────────
    ("human_decision_en", re.compile(
        r"right\s+to\s+(?:obtain\s+)?human\s+(?:intervention|review|decision)|"
        r"decided\s+by\s+a\s+(?:natural\s+person|human)",
        re.IGNORECASE), {"level": "MANUAL", "personal": True}),
    ("discretion_en", re.compile(
        r"\bat\s+(?:its|his|her|their)\s+(?:sole\s+)?discretion\b",
        re.IGNORECASE), {"level": "MANUAL"}),
    ("discretion_de", re.compile(
        r"\b(?:nach\s+)?(?:pflichtgemäße[mn]?\s+)?ermessen\b",
        re.IGNORECASE), {"level": "MANUAL"}),

    # ── personal / non-delegable ─────────────────────────────────────────────
    ("personal_de", re.compile(
        r"höchstpersönlich\w*", re.IGNORECASE),
     {"level": "MANUAL", "personal": True}),
    ("personal_en", re.compile(
        r"(?:non-?delegable|may\s+not\s+be\s+delegated|must\s+act\s+personally)",
        re.IGNORECASE), {"level": "MANUAL", "personal": True}),

    # ── grade ceiling — solely-automated prohibition (GDPR Art. 22 family) ──
    ("solely_automated_en", re.compile(
        r"(?:not\s+(?:be\s+)?(?:subject(?:ed)?\s+to\s+)?(?:a\s+)?decision\s+"
        r"based\s+solely\s+on\s+automated|"
        r"decision\s+based\s+solely\s+on\s+automated\s+processing)",
        re.IGNORECASE), {"level": "APPROVE", "ceiling": "L2", "personal": True}),
    ("solely_automated_de", re.compile(
        r"ausschließlich\s+auf\s+einer?\s+automatisierte\w*\s+verarbeitung\s+"
        r"beruhende\w*\s+entscheidung",
        re.IGNORECASE), {"level": "APPROVE", "ceiling": "L2", "personal": True}),

    # ── measures — logging / documentation duties ────────────────────────────
    ("measure_en", re.compile(
        r"shall\s+(?:log|record|document|keep\s+records?\s+of)\s+"
        r"(?P<measure>[^,.;:\n]{3,80})",
        re.IGNORECASE), {"level": "NOTIFY"}),
    ("measure_de", re.compile(
        r"(?P<measure>[^,.;:\n]{3,80}?)\s+(?:ist|sind)\s+zu\s+"
        r"(?:dokumentieren|protokollieren)",
        re.IGNORECASE), {"level": "NOTIFY"}),
]

_CADENCE = re.compile(
    r"\b(?P<cadence>quarterly|annually|monthly|weekly|"
    r"every\s+\d+\s+(?:days?|weeks?|months?|years?)|"
    r"regular(?:ly)?|periodic(?:ally)?|"
    r"vierteljährlich|jährlich|monatlich|wöchentlich|regelmäßig|"
    r"alle\s+\d+\s+(?:tage|wochen|monate|jahre))\b",
    re.IGNORECASE)

_TRIGGER = re.compile(
    r"^(?:where|when|if|in\s+the\s+event\s+(?:of|that)|"
    r"sofern|wenn|soweit|bei|im\s+fall(?:e)?\s+(?:von|des|der))\b"
    r"(?P<trigger>[^,;:]{3,100})",
    re.IGNORECASE)

_DE_MARKERS = re.compile(
    r"\b(?:bedarf|aufsicht|genehmigung|zustimmung|ermessen|überprüfen|"
    r"überwacht|verantwortliche|dokumentieren|höchstpersönlich|"
    r"ausschließlich|regelmäßig)\b|§", re.IGNORECASE)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÄÖÜ(§\d])")

_STRONG = {"approval_de", "approval_en", "approval_by_en",
           "solely_automated_en", "solely_automated_de", "human_decision_en"}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" \t.,;:")


def extract_oversight(content: str) -> list[OversightFacet]:
    """Extract oversight requirements from normative text.

    One facet per sentence that carries at least one oversight signal;
    multiple signals in a sentence merge (floors join strictest-wins,
    ceilings meet lowest-wins, personal ORs).
    """
    out: list[OversightFacet] = []
    for raw in _SENTENCE_SPLIT.split(content or ""):
        sentence = raw.strip()
        if not sentence or len(sentence) < 15:
            continue
        fired: list[str] = []
        level = ""
        ceiling = ""
        personal = False
        overseer = ""
        measure = ""
        for name, pattern, contrib in _PATTERNS:
            m = pattern.search(sentence)
            if not m:
                continue
            fired.append(name)
            lvl = contrib.get("level", "")
            if lvl and (not level or _LEVEL_ORDER[lvl] > _LEVEL_ORDER[level]):
                level = lvl
            cap = contrib.get("ceiling", "")
            if cap and (not ceiling
                        or _GRADE_ORDER[cap] < _GRADE_ORDER[ceiling]):
                ceiling = cap
            personal = personal or bool(contrib.get("personal"))
            groups = m.groupdict()
            if not overseer and groups.get("overseer"):
                overseer = _clean(groups["overseer"])
            if not measure and groups.get("measure"):
                measure = _clean(groups["measure"])
        if not fired:
            continue
        cad = _CADENCE.search(sentence)
        trig = _TRIGGER.search(sentence)
        language = "de" if _DE_MARKERS.search(sentence) else "en"
        confidence = 0.6
        if overseer:
            confidence += 0.15
        if any(s in _STRONG for s in fired):
            confidence += 0.15
        if cad or measure:
            confidence += 0.05
        out.append(OversightFacet(
            overseer=overseer,
            trigger=_clean(trig.group("trigger")) if trig else "",
            min_level=level,
            grade_ceiling=ceiling,
            measure=measure,
            cadence=_clean(cad.group("cadence")) if cad else "",
            personal=personal,
            raw_sentence=sentence,
            language=language,
            confidence=round(min(confidence, 0.95), 2),
            signals=fired,
        ))
    return out


def render_oversight(facet: OversightFacet) -> str:
    """Human-readable rendering, parallel to the rule renderer."""
    parts = [f"OVERSEER: {facet.overseer or '(unspecified)'}",
             f"FLOOR:    {facet.min_level}"]
    if facet.grade_ceiling:
        parts.append(f"CEILING:  {facet.grade_ceiling}")
    if facet.trigger:
        parts.append(f"WHEN:     {facet.trigger}")
    if facet.measure:
        parts.append(f"MEASURE:  {facet.measure}")
    if facet.cadence:
        parts.append(f"CADENCE:  {facet.cadence}")
    if facet.personal:
        parts.append("PERSONAL: non-delegable")
    parts.append(f"LANG:     {facet.language}")
    return "\n".join(parts)


def facet_to_dict(facet: OversightFacet) -> dict[str, Any]:
    return asdict(facet)
