# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Dispatch-record writer — the Workspace-side half of a connector.

Workspace never delivers outbound itself (taxonomy §4.2): an escalation is *written
to the log as a dispatch record*; a connector host (ticket system, e-mail,
webhook) picks it up under its own per-host token and does the delivery. This
module writes that record from a `GroundsBundle.connector_payload()`. It keeps
the substrate pure (no network), and makes delivery inherit the signed audit
chain — the dispatch and, later, the return are chained events.

Two invariants enforced here, not left to the connector:

  * **Notification ≠ decision** (§4.3). The dispatch record carries notice +
    grounds + a link target. It does NOT carry an approve/reject affordance,
    and :func:`record_decision_return` refuses to accept a decision that did
    not come through the decision surface (a ``surface_audit_id`` is required)
    — a reply parsed from a ticket comment can never close an escalation.
  * **Anti-ratification rendering** (§4.4). A residual payload (``render ==
    "options"``) must carry ≥2 options; this writer refuses to dispatch a
    residual as a binary control.

Pure stdlib + MutationLog. Graceful: a log failure attaches to the record,
never raised.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog


@dataclass
class DispatchResult:
    ok: bool
    dispatch_id: str = ""
    audit_id: str = ""
    error: str = ""
    channel: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "dispatch_id": self.dispatch_id,
                "audit_id": self.audit_id, "error": self.error,
                "channel": self.channel}


def _dispatch_id(payload: dict[str, Any], channel: str) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(channel.encode())
    h.update(b"|")
    h.update(str(payload.get("action", "")).encode())
    h.update(b"|")
    h.update(str(payload.get("agent", "")).encode())
    h.update(b"|")
    h.update(str(payload.get("link", "")).encode())
    return "dispatch:" + h.hexdigest()[:24]


def dispatch(
    payload: dict[str, Any],
    *,
    folder: str | Path,
    channel: str,
    recipient: str = "",
    log_root: Optional[str | Path] = None,
) -> DispatchResult:
    """Write a dispatch record for one escalation payload.

    ``payload`` is a ``GroundsBundle.connector_payload()``. ``channel`` is the
    transport ("jira", "email", "webhook"); ``recipient`` the routed target.
    Refuses a residual rendered without options (anti-ratification)."""
    if payload.get("render") == "options":
        opts = payload.get("options") or []
        if len(opts) < 2:
            return DispatchResult(
                False, error="residual payload must carry ≥2 options "
                "(anti-ratification §4.4); refusing to dispatch as binary",
                channel=channel)
        if not payload.get("link"):
            return DispatchResult(
                False, error="residual dispatch needs a link to the decision "
                "surface (notification ≠ decision §4.3)", channel=channel)

    did = _dispatch_id(payload, channel)
    record = {
        "kind": "escalation-dispatch",
        "dispatch_id": did,
        "channel": channel,
        "recipient": recipient,
        "render": payload.get("render", "ratify"),
        "payload": payload,
        "delivered": False,            # the host flips this when it delivers
    }
    res = DispatchResult(True, dispatch_id=did, channel=channel)
    try:
        log = MutationLog(Path(folder),
                          log_root=Path(log_root) if log_root else None)
        res.audit_id = log.append(LogEvent(
            event="system", folder_path=str(folder), pair_id=did,
            channel="system", actor="system", extra=record))
    except Exception as exc:           # noqa: BLE001
        res.ok = False
        res.error = f"{type(exc).__name__}: {exc}"
    return res


def record_decision_return(
    *,
    folder: str | Path,
    dispatch_id: str,
    surface_audit_id: str,
    chosen_option_id: str = "",
    actor: str = "",
    log_root: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Record that a dispatched escalation was decided — but ONLY by reference
    to a decision-surface event (``surface_audit_id``). The decision content
    itself lives in that surface event (origination, signed); this is just the
    chain link back to the dispatch. Refuses without the surface reference:
    notification ≠ decision (§4.3)."""
    if not surface_audit_id.strip():
        return {"error": "a decision return must reference a decision-surface "
                "event (surface_audit_id) — a ticket reply cannot close an "
                "escalation (§4.3)"}
    if not actor.strip():
        return {"error": "the deciding actor must be named"}
    record = {
        "kind": "escalation-return",
        "dispatch_id": dispatch_id,
        "surface_audit_id": surface_audit_id,
        "chosen_option_id": chosen_option_id,
        "actor": actor.strip(),
    }
    try:
        log = MutationLog(Path(folder),
                          log_root=Path(log_root) if log_root else None)
        record["audit_id"] = log.append(LogEvent(
            event="system", folder_path=str(folder),
            pair_id=f"return:{dispatch_id}", channel="system",
            actor=actor.strip(), extra=record))
    except Exception as exc:           # noqa: BLE001
        record["audit_error"] = f"{type(exc).__name__}: {exc}"
    return record
