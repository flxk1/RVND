# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Guardian action layer — agents that control agents, monotonically (§ 1.1).

This module is the guardian's HANDS, not its eyes: the closed action
vocabulary and its refusal paths. The watchdog (chain+queue reader with
budget/rate/loop/drift rules) is a separate component that calls
`guardian_act` and nothing else. Invariants:

1. MONOTONE RESTRICTION — the vocabulary is {pause, escalate}. Pause only
   ever restricts (active → suspended); escalate changes no state. Anything
   that would loosen — resume, activate, approve, expand — or exercise the
   human's kill switch is refused, and the refusal is appended to the chain
   (fail-closed: attempts leave evidence, never effect).
2. ROOT KEY UN-GATEABLE — a guardian action targeting a HUMAN party is
   refused. The human kill switch is a plain `parties.set_party_status`
   append with no guardian hook on its path; nothing in this vocabulary can
   undo it.

Supervision is recursive: every act, including a refusal, is an event on the
signed chain with the guardian stamped as actor.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog
from .parties import list_parties, set_party_status

GUARDIAN_ACTIONS = ("pause", "escalate")


class GuardianRefused(ValueError):
    """An action outside the monotone vocabulary, or against the root path."""


def _append(folder_context: str, actor: str, log_root, extra: dict) -> str:
    log = MutationLog(folder_context, log_root=log_root)
    return log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"party:{extra.get('party_id', '')}",
        channel="system",
        actor=actor,
        extra=extra,
    ))


def _refuse(folder_context: str, guardian_id: str, log_root,
            *, kind: str, party_id: str, why: str) -> None:
    _append(folder_context, guardian_id, log_root, {
        "kind": "GuardianRefused",
        "party_id": party_id,
        "attempted": kind,
        "why": why,
    })
    raise GuardianRefused(why)


def guardian_act(
    folder_context: str,
    kind: str,
    party_id: str,
    reason: str = "",
    guardian_id: str = "guardian",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Execute one guardian action against a registered AGENT party.

    pause    -> the agent is suspended (never killed, never reactivated).
    escalate -> a GuardianEscalation event carries the evidence to a human;
                no state changes.
    Everything else is refused + logged (see module invariants).
    """
    if kind not in GUARDIAN_ACTIONS:
        _refuse(folder_context, guardian_id, log_root, kind=kind,
                party_id=party_id,
                why=f"guardian action {kind!r} outside the monotone "
                    f"vocabulary {GUARDIAN_ACTIONS}; guardians restrict, "
                    f"never expand — reactivation and the kill switch are "
                    f"the human's")
    rows = list_parties(folder_context, log_root=log_root)["parties"]
    target = next((r for r in rows if r.get("party_id") == party_id), None)
    if target is None:
        _refuse(folder_context, guardian_id, log_root, kind=kind,
                party_id=party_id,
                why=f"party {party_id!r} is not on the chain; the guardian "
                    f"acts on registered parties only")
    if target.get("party_kind") != "agent":
        _refuse(folder_context, guardian_id, log_root, kind=kind,
                party_id=party_id,
                why="the human path is un-gateable: a guardian cannot act "
                    "on a human party")
    if kind == "escalate":
        audit_id = _append(folder_context, guardian_id, log_root, {
            "kind": "GuardianEscalation",
            "party_id": party_id,
            "reason": reason,
            "status_at_escalation": target.get("status"),
        })
        return {"ok": True, "action": "escalate", "party_id": party_id,
                "audit_id": audit_id}
    # pause: restrict only — an already-killed agent is NOT brought back to
    # 'suspended' (that would loosen); same-or-stricter is a no-op append-free
    if target.get("status") in ("suspended", "killed"):
        return {"ok": True, "action": "pause", "party_id": party_id,
                "status": target.get("status"), "unchanged": True}
    res = set_party_status(folder_context, party_id, "suspended",
                           reason=f"guardian: {reason}", actor=guardian_id,
                           log_root=log_root)
    return {"ok": True, "action": "pause", "party_id": party_id,
            "status": "suspended", "audit_id": res["audit_id"]}
