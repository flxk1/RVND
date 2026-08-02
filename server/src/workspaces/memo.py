# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Regulatory-placement memo — the deliverable.

Ties the pieces together: ingest an instrument's text → extract typed atoms
(obligations, decisions, required artifacts, cross-references) → enrich the
obligations with applicability facets → match against a
:class:`~workspaces.subject_card.SubjectCard` → render a memo that says where the
subject sits and what binds it.

The memo is a *projection of matched pairs*, not generated prose: every line
resolves to a source pair (id + source). Its shape mirrors the canonical
regulatory output (placement / triggered / may-apply / artifacts / cross-refs /
decisions / open questions). Everything below the card is deterministic; the
only judgment-adjacent step (free-text → card facets) is upstream and gated.

Disclaimer is structural, not decorative: MAY_APPLY items and open questions are
the cases the system refuses to resolve — they route to the human. The memo is a
re-verifiable draft, never legal advice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .subject_card import SubjectCard, get_vocabulary
from .applicability import enrich_pairs
from .matcher import assess, AssessmentResult, Match


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

@dataclass
class MemoInputs:
    """The typed atoms a memo is built from (all from the NDs)."""
    obligations: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    cross_refs: list[dict[str, Any]] = field(default_factory=list)


def extract_inputs(instrument_text: str, domain: str) -> MemoInputs:
    """Run the domain NDs over the instrument text to produce the typed atoms.

    Uses the deontic, decision, required-artifact and cross-reference extractors
    directly (no folder/memory needed for a one-shot memo).
    """
    from .nd_routing import DefaultClassifier
    from .deontic import DeonticFormulaND
    from .decisions.extractor import DecisionExtractor
    from .instrument_obligation_extractor import RequiredArtifactExtractor
    from .crossref_extractor import extract_cross_references

    cls = DefaultClassifier().classify(instrument_text)
    src = domain
    obligations = DeonticFormulaND().extract(instrument_text, cls, source_document=src)
    decisions = DecisionExtractor().extract(instrument_text, cls, source_document=src)
    artifacts = RequiredArtifactExtractor().extract(instrument_text, cls, source_document=src)
    refs = [r.to_dict() for r in extract_cross_references(instrument_text, host_key=domain)]
    return MemoInputs(obligations=obligations, decisions=decisions,
                      artifacts=artifacts, cross_refs=refs)


def _placement(card: SubjectCard) -> dict[str, Any]:
    """Where the subject sits — read off the card (the user-asserted, then
    matcher-confirmable, classification)."""
    tier = card.get("risk_tier")
    areas = card.get("annex_iii_area")
    role = card.get("role")
    # If no explicit tier but an Annex III area is present, the system is
    # high-risk by subsumption — state it, flagged as derived.
    derived = None
    if tier is None and areas:
        derived = "high-risk"
    return {"role": role, "risk_tier": tier, "derived_tier": derived,
            "annex_iii_area": areas, "gpai": card.get("gpai")}


def build_memo(instrument_text: str, card: SubjectCard,
               *, title: str = "") -> dict[str, Any]:
    """Full pipeline → a structured memo dict (render to text with render_memo)."""
    inputs = extract_inputs(instrument_text, card.domain)
    enrich_pairs(inputs.obligations, card.domain)
    result = assess(inputs.obligations, card)
    return {
        "title": title or f"Regulatory placement — {card.domain}",
        "placement": _placement(card),
        "assessment": result,
        "inputs": inputs,
        "card": card,
    }


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _ob_line(m) -> str:
    op = {"O": "must", "F": "must not", "P": "may", "R": "has right to"}.get(m.operator, "—")
    act = (m.action or "").strip()
    why = "; ".join(v.reason for v in m.facet_verdicts) or "unconditional"
    cite = f"  [{m.pair_id[:18]}… · {m.source}]"
    miss = f"  (needs: {', '.join(m.missing_facets)})" if m.missing_facets else ""
    return f"  - {m.bearer or '—'} {op} {act[:90]}{miss}\n      ↳ {why}{cite}"


def render_memo(memo: dict[str, Any]) -> str:
    p = memo["placement"]
    r: AssessmentResult = memo["assessment"]
    inp: MemoInputs = memo["inputs"]
    card: SubjectCard = memo["card"]
    out: list[str] = []

    out.append("=" * 74)
    out.append(memo["title"].upper())
    out.append("=" * 74)
    out.append("DRAFT — automated, re-verifiable. Not legal advice. "
               "'May apply' items and open questions require human review.")
    out.append("")

    # [0] Subject — capture-first: show whatever the user gave us.
    out.append("[0] SUBJECT ASSESSED")
    if card.description:
        out.append(f"  {card.description}")
    if card.contact:
        out.append(f"  contact: {card.contact}")
    if card.attachments:
        out.append(f"  attachments: {', '.join(card.attachments)}")
    for k, v in card.facets.items():
        out.append(f"  · {k}: {v}")
    if card.notes:
        note_lines = card.notes.splitlines()
        out.append(f"  notes: {note_lines[0]}")
        out += [f"         {ln}" for ln in note_lines[1:]]
    out.append(f"  intake completeness: {int(card.completeness() * 100)}% "
               f"of facets provided")
    if card.is_empty():
        out.append("  ⚠ No structured facets yet — this memo is PROVISIONAL: "
                   "it lists what may apply and what is needed to narrow it.")
    out.append("")

    # [1] Placement
    out.append("[1] PLACEMENT IN THE REGULATORY LANDSCAPE")
    tier = p["risk_tier"] or (f"{p['derived_tier']} (derived from use-case)"
                              if p["derived_tier"] else "not yet determined — needs intake")
    out.append(f"  · Role: {p['role'] or 'not yet stated — needs intake'}")
    out.append(f"  · Risk tier: {tier}")
    if p["annex_iii_area"]:
        out.append(f"  · Annex III area(s): {', '.join(p['annex_iii_area'])}")
    if p["gpai"]:
        out.append(f"  · GPAI: {p['gpai']}")
    out.append("")

    # [2] Obligations that apply
    out.append(f"[2] OBLIGATIONS THAT APPLY ({len(r.applies)})")
    out += [_ob_line(m) for m in r.applies] or ["  (none positively triggered)"]
    out.append("")

    # [3] May apply
    out.append(f"[3] MAY APPLY — needs confirmation ({len(r.may_apply)})")
    out += [_ob_line(m) for m in r.may_apply] or ["  (none)"]
    out.append("")

    # [4] Required artifacts
    out.append(f"[4] ARTIFACTS YOU MUST PRODUCE ({len(inp.artifacts)})")
    for a in inp.artifacts:
        s = a.get("solution", {})
        out.append(f"  · [{s.get('category')}] {s.get('artifact_name')} "
                   f"(obligated={s.get('obligated')})")
    if not inp.artifacts:
        out.append("  (none detected)")
    out.append("")

    # [5] Cross-instrument pull-in
    out.append(f"[5] OTHER INSTRUMENTS PULLED IN ({len(inp.cross_refs)})")
    for c in inp.cross_refs:
        cel = f" · CELEX {c.get('target_celex')}" if c.get("target_celex") else ""
        out.append(f"  -> {c.get('target_canonical', c.get('raw',''))} "
                   f"[{c.get('relation','refers-to')}]{cel}")
    if not inp.cross_refs:
        out.append("  (none detected)")
    out.append("")

    # [6] Decisions
    out.append(f"[6] DECISIONS YOU FACE ({len(inp.decisions)})")
    for d in inp.decisions:
        s = d.get("solution", {})
        out.append(f"  · {s.get('question')} (kind={s.get('decision_kind')})")
    if not inp.decisions:
        out.append("  (none detected)")
    out.append("")

    # [7] Open questions → oversight
    open_q = [m for m in r.may_apply]
    out.append(f"[7] OPEN QUESTIONS → human / oversight ({len(open_q)})")
    for m in open_q:
        out.append(f"  · confirm {', '.join(m.missing_facets)} for: "
                   f"{m.bearer} {m.action[:60]}")
    if not open_q:
        out.append("  (none — assessment fully determined by the card)")
    out.append("")
    out.append("Every line above resolves to a source obligation pair "
               "(id · source) and is re-verifiable against the audit chain.")
    return "\n".join(out)
