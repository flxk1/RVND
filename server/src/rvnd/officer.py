# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Officer — a policy-programmed oversight binding, NOT an autonomous agent.

Design rationale: an "officer agent that oversees agents' tasks" must not be a second
LLM (that only relocates the trust problem — who governs the governor). So an Officer is a thin
binding over structures that already exist:

    Officer = { the POLICY it enforces (a set of Policy Cards / rule ids) }
            × { the GATES / agents it OVERSEES }
            × { its CONTROL FORM (how it constrains — controlforms) }
            × { its human ESCALATION PARTY (a named party for the judgment calls) }

It is deterministic where it can be and human where it must be — GOVERNOR, NOT DOER:
  * it composes its control form with a gate's floor STRICTEST-WINS — it can only TIGHTEN,
    never loosen a regulated gate to `auto` (the policy_matrix safety invariant);
  * a reserved act (a genuine judgment) is ROUTED to the escalation party — the officer never
    auto-decides it (`solver_topology`: a judgment node may never be graded auto).

An officer MAY be executed by a mechanical agent (run the checks, assemble the evidence, draft
the escalation) — but that agent is `reasoning_walker`-style: a machine, never the judge.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from . import demand_cta as _dc


@dataclass
class Officer:
    officer_id: str
    name: str
    oversees: list[str]                              # gate / agent ids this officer governs
    control_form: str = "single_approver"           # its default constraint (a controlforms name)
    escalation_party: str = ""                       # named party (role) for reserved judgments
    policy: list[str] = field(default_factory=list)  # the Policy-Card rule_ids that DEFINE it
    authority: str = ""                              # the grant/role that lets it act at all

    def governs(self, target: str) -> bool:
        return target in self.oversees

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def oversight_for(officer: Officer, *, gate_floor: str, grade: str = "L4") -> dict[str, Any]:
    """Compose the officer's control form with a gate's floor, STRICTEST-WINS. The officer can
    only tighten: if the gate's floor is already stricter, the floor holds. Returns the effective
    control form + guarantees + who a reserved act escalates to."""
    eff = _dc.overlay_effective({"grade": grade, "oversight": gate_floor},
                                {"oversight": officer.control_form})
    return {
        "officer": officer.officer_id,
        "control_form": eff["control_form"],
        "guarantees": eff["guarantees"],
        "tightened": eff["tightened_by_user"],
        "escalation_party": officer.escalation_party,
    }


def route_reserved(officer: Officer, act: dict[str, Any], *,
                   folder_context: Optional[str] = None, log_root: Optional[str] = None) -> dict[str, Any]:
    """A reserved act (a judgment) goes to the officer's escalation party — decided by a human,
    never auto ('governor, not doer'). This reuses the EXISTING escalation stack: it resolves the
    human via ``parties.route_approvers`` (competence = the escalation party) and names
    ``oversight_dispatch`` as the delivery — it does not reinvent routing or delivery."""
    routed = None
    if folder_context and officer.escalation_party:
        try:
            from . import parties
            routed = parties.route_approvers(folder_context, officer.escalation_party, log_root=log_root)
        except Exception:
            routed = None
    return {
        "to": officer.escalation_party or "designated-officer",
        "routed": routed,                       # parties.route_approvers result (the actual people)
        "via": "oversight_dispatch",            # delivery through the existing dispatch record + connector
        "act": act,
        "decided_by": "human",
        "auto": False,
        "reason": "reserved act — routed to the escalation party via parties.route_approvers, "
                  "delivered via oversight_dispatch; the officer never decides it",
    }
