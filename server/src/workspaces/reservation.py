# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Reserved decisions — placing the human where they are REQUIRED or WANTED.

The correction this encodes: human oversight is not a risk-acceptance toggle.
A company does not pick a risk appetite from a menu. The best solver does ALL
the determinate work correctly and then places the human at the act that is
RESERVED to them — a sign-off, an approval, an authorisation, a
co-determination — by law or by the company's own policy.

This is a different trigger from an epistemic residual (the machine being
unsure). A reserved act fires even when the analysis is fully certain, because
the law (or policy) reserves the ACT, not the uncertainty. The human's role is
to perform their authorised act on work that is already done — not to gamble.

Legal reservations are mandatory and cannot be disabled. Policy reservations
are company-chosen and additive. Each reserved act names WHO (a competence),
WHAT (the act type), and WHY (the basis and its source), so it can be routed
to the right person via ``parties.route_approvers``.

NOTE: canonical Loomground module; if it moves into loomground_core, this file
should become a thin compatibility shim.
"""
from __future__ import annotations

from typing import Any, Optional

ACT_TYPES = ("sign", "approve", "authorize", "co-determine", "review")

#: Mandatory legal reservations: issue type -> the human act the law reserves.
#: basis_kind 'law' (statute/regulation) or 'professional' (e.g. legal sign-off).
LEGAL_RESERVATIONS: dict[str, dict[str, Any]] = {
    "ai_high_risk": {
        "reserved_to": "ai-oversight-officer", "act_type": "authorize",
        "basis_kind": "law",
        "source": "AI Act (Reg. 2024/1689) Art. 14 — human oversight of "
                  "high-risk AI"},
    "automated_decision": {
        "reserved_to": "data-protection", "act_type": "review",
        "basis_kind": "law",
        "source": "GDPR Art. 22 — automated individual decisions / profiling"},
    "co_determination": {
        "reserved_to": "works-council", "act_type": "co-determine",
        "basis_kind": "law",
        "source": "BetrVG § 87(1) Nr. 6 — co-determination, employee "
                  "monitoring systems"},
    "data_transfer": {
        "reserved_to": "controller-representative", "act_type": "sign",
        "basis_kind": "law",
        "source": "GDPR Art. 24 accountability + Art. 46 transfer safeguards"},
    "legal_conclusion": {
        "reserved_to": "qualified-counsel", "act_type": "sign",
        "basis_kind": "professional",
        "source": "professional reservation — legal advice is counsel's; "
                  "Workspace output is organisational analysis, not legal advice"},
}


def reserved_acts_for(
    issue_types: list[str],
    *,
    policy_reservations: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """The reserved human acts owed for a set of detected issue types.

    Legal reservations (mandatory) fire for any matching issue; policy
    reservations (the company's own choices) are additive on top. Each act is
    independent of the analysis' certainty — a fully determinate issue with a
    reservation still owes its act. Deduped by (trigger, act_type), stable
    order."""
    policy = policy_reservations or {}
    out: dict[tuple, dict[str, Any]] = {}
    for itype in issue_types:
        spec = LEGAL_RESERVATIONS.get(itype)
        if spec is not None:
            act = dict(spec, trigger=itype)
            out[(itype, act["act_type"], act.get("reserved_to", ""))] = act
    # Policy reservations are additive and fire regardless of a matched analysis
    # issue type — e.g. a governance twin's gate-keyed reservation ("reserve
    # generated_content by moderator") has no detected itype but must still owe its
    # act. A gate may carry MORE THAN ONE approver, so a value may be a single act
    # dict OR a list of them; dedup by (trigger, act_type, reserved_to) so distinct
    # approvers all survive (a legal + a policy reservation on one gate keep both).
    for key, pol in policy.items():
        entries = pol if isinstance(pol, list) else [pol]
        for p in entries:
            if not isinstance(p, dict):
                continue
            act = {"trigger": key, "basis_kind": "policy",
                   "reserved_to": p.get("reserved_to", "designated-approver"),
                   "act_type": p.get("act_type", "approve"),
                   "source": p.get("source", "company policy")}
            # Option 2: preserve the authored `when` GUARD so the run-path can honour
            # it (a conditional reserve, incl. `tags contains <tag>`). Absent ⇒ unconditional.
            if p.get("when"):
                act["when"] = p["when"]
            out.setdefault((key, act["act_type"], act["reserved_to"]), act)
    return [out[k] for k in sorted(out)]
