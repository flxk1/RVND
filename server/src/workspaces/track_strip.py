# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""track_strip — one track's governance, assembled read-only.

The per-track inspector projection (the
track channel strip"): everything the channel strip renders about a single
track, joined from the projections that already exist — parties (through the
resolver port), connectors, use cases, the approvals inbox, the credential
describe — plus a per-track verdict tally from chain replay. No new
enforcement and no new store; every lamp reflects recorded state.

A track is addressed by exactly one of ``party_id`` (an agent/human lane) or
``connector_id`` (a channel). Unknown ids fail closed. Egress cable state
appears only on egress connectors (progressive disclosure); the credential is
always the reference and its arm status, never the secret.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from ._meters import verdict_tally
from .approvals import list_approvals
from .connectors import list_connectors
from .parties import list_parties
from .use_case import list_use_cases


def _use_case_row(uc: dict[str, Any]) -> dict[str, Any]:
    return {"use_case_id": uc.get("use_case_id"), "name": uc.get("name"),
            "risk": uc.get("risk")}


def _reserved_acts(use_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every reserved human act across a track's use cases, with its basis.
    Acts with ``basis_kind="law"`` additionally lock the oversight ladder
    (tighten-only; no loosening affordance is rendered)."""
    acts = []
    for uc in use_cases:
        for act in (uc.get("reserved_acts") or []):
            if isinstance(act, dict):
                acts.append({"use_case_id": uc.get("use_case_id"),
                             "trigger": act.get("trigger"),
                             "act_type": act.get("act_type"),
                             "reserved_to": act.get("reserved_to"),
                             "basis_kind": act.get("basis_kind"),
                             "source": act.get("source")})
    return acts


def _law_locks(acts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in acts if a.get("basis_kind") == "law"]


def track_strip(folder_context: str, *, party_id: Optional[str] = None,
                connector_id: Optional[str] = None, now: Optional[float] = None,
                log_root: Optional[str] = None) -> dict[str, Any]:
    """The channel-strip projection for one track. Exactly one of ``party_id``
    or ``connector_id`` addresses the track; anything else fails closed."""
    if bool(party_id) == bool(connector_id):
        return {"ok": False,
                "reason": "address exactly one track: party_id or connector_id"}
    if party_id:
        return _party_strip(folder_context, party_id, now=now, log_root=log_root)
    return _connector_strip(folder_context, connector_id, log_root=log_root)


def _party_strip(folder_context: str, party_id: str, *, now: Optional[float],
                 log_root) -> dict[str, Any]:
    rows = list_parties(folder_context, log_root=log_root).get("parties") or []
    rec = next((p for p in rows if p.get("party_id") == party_id), None)
    if rec is None:
        return {"ok": False, "reason": f"unknown party '{party_id}'"}

    connectors_by_id = {c["connector_id"]: c
                        for c in list_connectors(folder_context, log_root=log_root)}
    channels = []
    for cid in (rec.get("channels") or []):
        c = connectors_by_id.get(cid)
        if c is None:
            # a soft binding to a connector that is not registered — shown, not
            # hidden, so a dangling cable is visible on the strip
            channels.append({"connector_id": cid, "registered": False})
            continue
        channels.append({"connector_id": cid, "registered": True,
                         "role": c.get("role"), "channel": c.get("channel"),
                         "floor": c.get("floor", "permit")})

    use_cases = [uc for uc in list_use_cases(folder_context, log_root=log_root)
                 if party_id in (uc.get("allowed_agents") or [])]
    acts = _reserved_acts(use_cases)
    locks = _law_locks(acts)

    # The sign-off meter: pending approval requests routed to a competence this
    # party holds — the requests this lane's hand could count toward.
    held = set(rec.get("competences") or [])
    pending = []
    inbox = list_approvals(folder_context, now=(now or time.time()),
                           log_root=log_root).get("approvals") or []
    for item in inbox:
        if item.get("state") != "pending":
            continue
        routed = set(item.get("competences") or [])
        if not (held and routed & held):
            continue
        # the live m-of-n meter: "signed of required" — ``needed`` from the
        # inbox is the total counting hands the request demands
        pending.append({"request_id": item.get("request_id"),
                        "form": item.get("form"),
                        "signed": len(item.get("approvers") or []),
                        "required": item.get("needed"),
                        "deadline": item.get("deadline"),
                        "on_elapse": item.get("on_elapse"),
                        "competences": sorted(routed)})

    return {"ok": True, "folder_context": folder_context, "kind": "party",
            "strip": {
                "party_id": party_id,
                "name": rec.get("name") or party_id,
                "party_kind": rec.get("party_kind"),
                "status": rec.get("status", "active"),
                "role": rec.get("role"),
                "competences": list(rec.get("competences") or []),
                # the oversight ladder: current rung + whether a law-basis
                # reservation locks it (tighten-only)
                "ladder": {"grade": rec.get("grade"),
                           "locked": bool(locks), "locks": locks},
                "channels": channels,
                "use_cases": [_use_case_row(uc) for uc in use_cases],
                "reservations": acts,
                "approvals": {"pending": pending, "count": len(pending)},
                "meter": verdict_tally(folder_context, log_root, actor=party_id),
                                       }}


def _connector_strip(folder_context: str, connector_id: str, *,
                     log_root) -> dict[str, Any]:
    rec = next((c for c in list_connectors(folder_context, log_root=log_root)
                if c.get("connector_id") == connector_id), None)
    if rec is None:
        return {"ok": False, "reason": f"unknown connector '{connector_id}'"}

    linked_ids = set(rec.get("use_cases") or [])
    use_cases = [uc for uc in list_use_cases(folder_context, log_root=log_root)
                 if uc.get("use_case_id") in linked_ids]

    drivers = [{"party_id": p.get("party_id"), "party_kind": p.get("party_kind"),
                "status": p.get("status", "active"), "grade": p.get("grade")}
               for p in (list_parties(folder_context,
                                      log_root=log_root).get("parties") or [])
               if connector_id in (p.get("channels") or [])]

    strip: dict[str, Any] = {
        "connector_id": connector_id,
        "name": rec.get("name") or connector_id,
        "role": rec.get("role"),
        "channel": rec.get("channel"),
        "floor": rec.get("floor", "permit"),
        "group": rec.get("group", ""),
        "tags": list(rec.get("tags") or []),
        "use_cases": [_use_case_row(uc) for uc in use_cases],
        "reservations": _reserved_acts(use_cases),
        "parties": drivers,
        "meter": verdict_tally(folder_context, log_root,
                               pair_id=f"connector:{connector_id}"),
    }
    if rec.get("role") == "egress":
        # the cable — only egress tracks show one (progressive disclosure);
        # reference + arm status, never the secret
        from .lock import describe
        strip["egress"] = {"credential": describe(rec.get("credential_ref"))}
    return {"ok": True, "folder_context": folder_context, "kind": "connector",
            "strip": strip}
