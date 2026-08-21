# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Identify policy DUTIES (role · risk · operator · action) from instrument text — and triage
what the deterministic read resolved from what the INTERPRETER layer must read.

End-to-end front of the navigator's allocation path:

    instrument text → rule_extractor (subject/modal/action/condition)
                    → deontic operator (O/P/F; a right is P + a Hohfeld incident)
                    → applicability facets (role / risk_tier / area)
                    → IdentifiedDuty + a TRIAGE flag

Triage separates the two: the deterministic extractor reads the surface; the
AI Act writes many duties in **agentless passive** ("a risk management system shall be
established", "the technical documentation … shall be drawn up"), where the grammatical
subject is the PATIENT, not the addressee — so the responsible ROLE is not on the surface
(`RuleFacet.addressee_resolved=False`). Some duties don't parse at all ("… shall be
prohibited"). Those are not failures to paper over: they are routed to the interpreter layer
(`reasoning_walker` + human ratify), which identifies the role/risk/duty the surface withholds
— never guessed here (guessing the addressee would be judging, not transcribing).

Deterministic where the prose allows; interpreter where it does not. Nothing silently dropped:
a passage that yields no rule still produces one IdentifiedDuty flagged for the interpreter
(audit floor). Pure stdlib + reuse; no model in the loop here (the interpreter owns that).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from . import rule_extractor as _re
from . import deontic_facets as _de
from . import applicability as _ap


@dataclass
class IdentifiedDuty:
    """One duty read (or flagged-for-reading) from an instrument passage."""
    source: str                       # caller-supplied pinpoint ("Art. 9"), or ""
    operator: str                     # O | F | P | R | "" (unextracted)
    role: Optional[str]               # the responsible role, or None → interpreter must identify
    risk_tier: Optional[str]          # high-risk | gpai | … or None
    areas: list[str] = field(default_factory=list)
    action: str = ""
    bearer: str = ""
    condition: str = ""
    addressee_resolved: bool = True   # False = agentless passive (subject is the patient)
    confidence: float = 0.0
    needs_interpreter: bool = False   # True → role/risk/duty must be read by the interpreter
    interpreter_reason: str = ""      # why it escalated (auditable)
    tier_unresolved: bool = False     # role known but tier off-surface (verify, don't invent)
    origin: str = "deterministic"     # deterministic | interpreter-ratified
    raw: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_pair(self) -> dict[str, Any]:
        """Project into the obligation-pair shape the matcher / applicability consume."""
        appl: dict[str, Any] = {}
        if self.role:
            appl["role"] = self.role
        if self.risk_tier:
            appl["risk_tier"] = self.risk_tier
        if self.areas:
            appl["annex_iii_area"] = list(self.areas)
        return {
            "id": self.source or self.action[:24],
            "problem": {"source_document": self.source, "summary": f"{self.bearer} {self.action}"},
            "solution": {"bearer": self.bearer, "condition": self.condition,
                         "action": self.action, "operator": self.operator,
                         "confidence": self.confidence or 0.7},
            "applicability": appl,
            "origin": self.origin,
        }


def identify_duties(text: str, *, source: str = "",
                    domain: Optional[str] = None) -> list[IdentifiedDuty]:
    """Read every duty in ``text``. A passage that yields no operative rule still returns one
    duty flagged for the interpreter (nothing silently dropped).

    Role/risk allocation is INSTRUMENT-NEUTRAL: it goes through ``applicability.read_trigger``
    over the registered trigger-reader packs (``domain`` pins one; default = all registered).
    Which instruments can be read is a fact about the packs, not this engine."""
    facets = _re.extract_rules(text)
    if not facets:
        return [IdentifiedDuty(
            source=source, operator="", role=None, risk_tier=None,
            needs_interpreter=True, raw=text.strip(),
            interpreter_reason="no operative rule on the surface — interpreter must read "
                               "the role / risk / duty (e.g. a passive prohibition)")]
    out: list[IdentifiedDuty] = []
    for f in facets:
        op = _de.formula_from_rule(f).operator
        appl = _ap.read_trigger(f.subject, f.condition, domain=domain)
        role = appl.get("role")
        # the surface mentions a tier in the action but not the bearer/condition → role known,
        # tier off-surface: allocate to the role, but flag the tier for verification.
        tier_off_surface = (role is not None and "risk_tier" not in appl
                            and ("high-risk" in (f.action or "").lower()
                                 or "general-purpose" in (f.action or "").lower()))
        needs = (role is None) or (not f.addressee_resolved)
        reason = ""
        if needs:
            reason = ("agentless passive — the subject is the patient, not the addressee; "
                      "interpreter must identify the responsible role"
                      if not f.addressee_resolved else
                      "no role on the surface — interpreter must identify the responsible role")
        out.append(IdentifiedDuty(
            source=source, operator=op, role=role, risk_tier=appl.get("risk_tier"),
            areas=list(appl.get("annex_iii_area", [])), action=f.action, bearer=f.subject,
            condition=f.condition, addressee_resolved=f.addressee_resolved,
            confidence=f.confidence, needs_interpreter=needs, interpreter_reason=reason,
            tier_unresolved=tier_off_surface, raw=f.raw_sentence or text.strip()))
    return out


@dataclass
class Triage:
    """The split the hybrid boundary produces over a set of duties."""
    resolved: list[IdentifiedDuty] = field(default_factory=list)       # role identified deterministically
    interpreter_queue: list[IdentifiedDuty] = field(default_factory=list)  # role to be read by interpreter

    def summary(self) -> dict[str, Any]:
        return {"resolved": len(self.resolved), "interpreter_queue": len(self.interpreter_queue),
                "queue_sources": [d.source for d in self.interpreter_queue]}


def triage(duties: list[IdentifiedDuty]) -> Triage:
    t = Triage()
    for d in duties:
        (t.interpreter_queue if d.needs_interpreter else t.resolved).append(d)
    return t


def ratify(duty: IdentifiedDuty, *, role: Optional[str] = None, operator: Optional[str] = None,
           risk_tier: Optional[str] = None, rationale: str = "") -> IdentifiedDuty:
    """Apply an INTERPRETER reading to a queued duty — whatever the human/LLM identified and
    ratified: a responsible ``role`` (or, for a role-agnostic prohibition, leave role None and
    supply ``operator='F'``), optionally the ``risk_tier``. Marked
    ``origin='interpreter-ratified'`` so it never merges silently with the deterministic set —
    the audit trail shows what the surface gave vs what the interpreter supplied. Requires a
    non-empty rationale (origination, like the walker / decision_surface)."""
    if not rationale.strip():
        raise ValueError("interpreter ratification requires a rationale (no silent resolution)")
    if role is not None:
        duty.role = role
    if operator is not None:
        duty.operator = operator
    if risk_tier is not None:
        duty.risk_tier = risk_tier
    duty.needs_interpreter = False
    duty.origin = "interpreter-ratified"
    duty.interpreter_reason = f"ratified: {rationale}"
    return duty
