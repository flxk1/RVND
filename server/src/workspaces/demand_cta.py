# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Demand type → CTA, plus the autonomy/oversight overlay — the "what do I DO about this rule"
layer.

Two orthogonal things hang off a rule:
  * DEMAND — what the rule wants PRODUCED (a disclaimer, a document, a management system, …).
    Nine primary kinds; the PRIMARY drives the CTA. SECONDARY effects are an OPEN set — the
    same rule means different tasks in different use cases, so a secondary is a free tag +
    optional handler, never a closed enum.
  * OVERLAY — how the AGENT is gated when acting under the rule. The FLOOR is an admin act
    (immovable); a normal user may only OVERLAY within authority and only TIGHTEN — never
    paint a regulated cell looser. Reuses `controlforms` (strictest-wins by construction).

A CTA is ``f(demand, state)`` and it OPENS a handler — it never executes (declares, not
certifies; a disclosure is drafted not sent; a gate is proposed not applied).

`legal_basis` and `training` are deliberately NOT demand kinds — they are user-world / legal-
tech content Rvnd CARRIES and displays (see `governance_map` ``carried``), never authors.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from . import controlforms as _cf

# ── the nine primary demand kinds — verb · label · handler it opens ───────────────────────
DEMANDS: dict[str, tuple[str, str, str]] = {
    "disclosure":                ("draft",     "Draft disclosure",                    "disclosure"),
    "record":                    ("add",       "Add document / record",               "evidence"),
    "management_system":         ("establish", "Stand up process · assign owner",      "requirements_house"),
    "assessment":                ("run",       "Run assessment",                       "assessment"),
    "oversight":                 ("set",       "Designate reviewer · set control form", "controlforms"),
    "technical_measure":         ("configure", "Configure measure",                    "connectors"),
    "appointment":               ("assign",    "Assign the role",                      "parties"),
    "registration_notification": ("register",  "Register · wire notification",         "connectors"),
    "guard":                     ("attest",    "Attest absence · add guard",           "gate"),
}
DEMAND_TYPES = tuple(DEMANDS)

_CUES: tuple[tuple[str, re.Pattern], ...] = (
    ("disclosure",                re.compile(r"disclos|inform|notify|make clear|label|transparen", re.I)),
    ("assessment",                re.compile(r"assess|conformity|evaluat|dpia|impact assessment|examination", re.I)),
    ("management_system",         re.compile(r"management system|establish.*system|implement.*maintain|quality management|governance.*process", re.I)),
    ("oversight",                 re.compile(r"human oversight|oversee|human review|reviewed by|under the control", re.I)),
    ("technical_measure",         re.compile(r"security|robust|accura|encrypt|logging|record events|technical and organisational|cyber", re.I)),
    ("appointment",               re.compile(r"appoint|designate|representative|shall have a", re.I)),
    ("registration_notification", re.compile(r"register|registration|notify the|notification to|inform the authority", re.I)),
    ("record",                    re.compile(r"document|technical documentation|keep.*record|draw up|logs?\b", re.I)),
)


def classify_demand(duty: Any) -> str:
    """The PRIMARY demand a rule makes — a prohibition is a ``guard``; else the first cue that
    fits its action; else ``record`` (a generic duty produces a record). This is a structural
    read, not a legal one — it says what SHAPE of response satisfies the rule, not what it means."""
    if getattr(duty, "operator", "") == "F":
        return "guard"
    text = f"{getattr(duty, 'action', '')} {getattr(duty, 'raw', '')}"
    for kind, rx in _CUES:
        if rx.search(text):
            return kind
    return "record"


def cta_for(*, demand_type: str, operator: str = "O", coverage: str = "n/a",
            needs_interpreter: bool = False, status: Optional[str] = None) -> dict[str, Any]:
    """The CTA = demand × STATE. State wins first: an unread rule ratifies before anything; an
    undetermined applicability confirms; only a settled, applicable rule shows its furnish CTA.
    Every CTA names a ``handler`` to OPEN — never an auto-action."""
    if needs_interpreter:
        return {"verb": "ratify", "label": "Read & ratify", "handler": "interpreter",
                "target": "reading"}
    if status == "may_apply":
        return {"verb": "confirm", "label": "Confirm applicability", "handler": "use_case_intake",
                "target": "subject_card"}
    if operator == "F" or demand_type == "guard":
        v, lbl, h = DEMANDS["guard"]
        return {"verb": v, "label": lbl, "handler": h, "target": "gate"}
    verb, label, handler = DEMANDS.get(demand_type, DEMANDS["record"])
    if coverage == "furnished":
        return {"verb": "review", "label": "Review / refresh", "handler": handler,
                "target": demand_type}
    # empty OR n/a (evidence layer not yet connected) → furnish it
    return {"verb": verb, "label": label, "handler": handler, "target": demand_type}


# ── the overlay — admin FLOOR (immovable) + user TIGHTEN (within authority) ────────────────
_OVERSIGHT_GUARANTEES: dict[str, frozenset] = {
    "auto":            frozenset(),
    "notify":          frozenset({_cf.G_NOTIFY}),
    "single_approver": frozenset({_cf.G_PRE_APPROVAL}),
    "two_approvers":   frozenset({_cf.G_PRE_APPROVAL, _cf.G_TWO_APPROVERS}),
    "competent":       frozenset({_cf.G_PRE_APPROVAL, _cf.G_COMPETENCE}),
    "block":           frozenset({_cf.G_BLOCKED}),
}


def _grade_rank(g: Any) -> int:
    m = re.search(r"(\d+)", str(g or "L2"))
    return int(m.group(1)) if m else 2


def overlay_effective(floor: dict[str, Any], user: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Compose an admin FLOOR with a user OVERLAY, strictest-wins — the user can only TIGHTEN.

    * autonomy grade — the floor is the CEILING; the effective grade is the *lower* autonomy
      (``min`` rank). A user asking for MORE autonomy than the floor is clamped to the floor.
    * oversight — guarantees compose by UNION; a user can only ADD guarantees. A looser user
      choice is absorbed (the floor's guarantees remain).

    So the result is ALWAYS ≥ the floor in strictness — the safety invariant, by construction."""
    floor = floor or {}
    fg, fo = floor.get("grade", "L2"), floor.get("oversight", "single_approver")
    floor_guar = _OVERSIGHT_GUARANTEES.get(fo, frozenset({_cf.G_PRE_APPROVAL}))
    if not user:
        composed = floor_guar
        eff_grade = fg
    else:
        ug, uo = user.get("grade", fg), user.get("oversight", fo)
        eff_grade = f"L{min(_grade_rank(fg), _grade_rank(ug))}"          # never above the ceiling
        composed = floor_guar | _OVERSIGHT_GUARANTEES.get(uo, frozenset())  # only adds
    return {
        "grade": eff_grade,
        "control_form": _cf.name_of(composed),
        "guarantees": sorted(composed),
        "tightened_by_user": bool(user) and (
            _grade_rank(eff_grade) < _grade_rank(fg) or composed != floor_guar),
        "floor": {"grade": fg, "oversight": fo},
    }
