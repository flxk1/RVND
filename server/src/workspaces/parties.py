# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Party registry — accountable humans and agents (§ 1.5), as chain events.

No new store: a party is registered by an event on a folder's signed chain
(the org's registry folder), updated by appending, never edited. Projections
replay the chain. Erasure and seal come free from the substrate.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog

PARTY_KINDS = ("human", "agent")
PARTY_STATUSES = ("active", "suspended", "killed")


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


def register_party(
    folder_context: str,
    party_id: str,
    kind: str,
    name: str = "",
    role: str = "",
    competences: Optional[list[str]] = None,
    channels: Optional[list[str]] = None,
    owner: str = "",
    purpose: str = "",
    grade: str = "",
    agent_uid: str = "",
    actor: str = "user",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Register or update a party. Humans carry role/competences/channels;
    agents carry owner/purpose/grade. Re-registering appends a new version."""
    if kind not in PARTY_KINDS:
        raise ValueError(f"kind must be one of {PARTY_KINDS}, got {kind!r}")
    if not party_id:
        raise ValueError("party_id required")
    if kind == "agent":
        # Re-registration preserves identity. A caller registering the same
        # real agent in another workspace may pass its existing UID explicitly.
        prior = _list_parties_local(folder_context, log_root=log_root)
        previous = next((p for p in prior["parties"]
                         if p.get("party_id") == party_id
                         and p.get("party_kind") == "agent"), None)
        agent_uid = agent_uid or (previous or {}).get("agent_uid", "")
        if not agent_uid:
            agent_uid = str(uuid.uuid4())
        try:
            agent_uid = str(uuid.UUID(agent_uid))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("agent_uid must be a UUID") from exc
    else:
        agent_uid = ""
    audit_id = _append(folder_context, actor, log_root, {
        "kind": "PartyRegistered",
        "party_id": party_id,
        "party_kind": kind,
        "name": name,
        "role": role,
        "competences": list(competences or []),
        "channels": list(channels or []),
        "owner": owner,
        "purpose": purpose,
        "grade": grade,
        "agent_uid": agent_uid,
    })
    result = {"ok": True, "party_id": party_id, "audit_id": audit_id}
    if agent_uid:
        result["agent_uid"] = agent_uid
    return result


def set_party_status(
    folder_context: str,
    party_id: str,
    status: str,
    reason: str = "",
    actor: str = "user",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """active | suspended | killed. 'killed' is the kill switch: an
    appended event, immediate for every projection, never an edit."""
    if status not in PARTY_STATUSES:
        raise ValueError(
            f"status must be one of {PARTY_STATUSES}, got {status!r}")
    audit_id = _append(folder_context, actor, log_root, {
        "kind": "PartyStatus",
        "party_id": party_id,
        "status": status,
        "reason": reason,
    })
    return {"ok": True, "party_id": party_id, "status": status,
            "audit_id": audit_id}


def _list_parties_local(
    folder_context: str,
    kind: str = "",
    competence: str = "",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Replay projection: latest registration + latest status per party.
    The LOCAL backend behind LocalPartyResolver; callers use ``list_parties``."""
    log = MutationLog(folder_context, log_root=log_root)
    records: dict[str, dict[str, Any]] = {}
    for evt in log.replay():
        extra = evt.extra or {}
        k = extra.get("kind")
        pid = extra.get("party_id", "")
        if k == "PartyRegistered":
            rec = {f: extra.get(f) for f in (
                "party_id", "party_kind", "name", "role", "competences",
                "channels", "owner", "purpose", "grade", "agent_uid")}
            if rec["party_kind"] == "agent" and not rec.get("agent_uid"):
                # Old events remain readable and countable, but their identity
                # is intentionally workspace-scoped and cannot be deduplicated.
                raw = f"{Path(folder_context).expanduser().resolve()}\0{pid}"
                rec["agent_uid"] = "legacy-" + hashlib.sha256(
                    raw.encode("utf-8")).hexdigest()[:24]
            rec["status"] = records.get(pid, {}).get("status", "active")
            records[pid] = rec
        elif k == "PartyStatus" and pid in records:
            records[pid]["status"] = extra.get("status", "active")
    rows = list(records.values())
    if kind:
        rows = [r for r in rows if r.get("party_kind") == kind]
    if competence:
        rows = [r for r in rows if competence in (r.get("competences") or [])]
    return {"ok": True, "count": len(rows), "parties": rows}


def list_parties(
    folder_context: str,
    kind: str = "",
    competence: str = "",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Roster projection through the active PartyResolver (local by default).
    Routing identity through the resolver is the sole seam an IdP adapter uses;
    every existing caller reaches an adapter automatically by calling this."""
    from .party_resolver import get_resolver
    return get_resolver().list_parties(
        folder_context, kind=kind, competence=competence, log_root=log_root)


BUILTIN_ACTORS = ("user", "system")    # pre-registry principals


def actor_stamp_report(
    folder_context: str,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Measurement projection (§ 1.5 acting-party stamps): classify every
    chain event's actor as attributed (a registered party), builtin (the
    pre-registry principals), or unknown — and count unstamped events.
    Measurement, not judgment: a non-empty unknown list is a lead, the
    controller decides what it means."""
    registered = {p["party_id"] for p in
                  list_parties(folder_context, log_root=log_root)["parties"]}
    log = MutationLog(folder_context, log_root=log_root)
    total = attributed = builtin = unstamped = 0
    unknown: set[str] = set()
    for evt in log.replay():
        total += 1
        a = evt.actor or ""
        if not a:
            unstamped += 1
        elif a in registered:
            attributed += 1
        elif a in BUILTIN_ACTORS:
            builtin += 1
        else:
            unknown.add(a)
    return {"ok": True, "total": total, "attributed": attributed,
            "builtin": builtin, "unstamped": unstamped,
            "unknown_actors": sorted(unknown)}


def _route_approvers_local(
    folder_context: str,
    competence: str,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """The § 1.5 routing join: ACTIVE humans matching the competence domain.
    LOCAL backend behind LocalPartyResolver; callers use ``route_approvers``."""
    res = _list_parties_local(folder_context, kind="human", competence=competence,
                              log_root=log_root)
    rows = [r for r in res["parties"] if r.get("status") == "active"]
    return {"ok": True, "competence": competence, "count": len(rows),
            "approvers": rows}


def route_approvers(
    folder_context: str,
    competence: str,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Routing through the active PartyResolver (local by default)."""
    from .party_resolver import get_resolver
    return get_resolver().route_approvers(
        folder_context, competence, log_root=log_root)
