# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Approval semantics (§ 1.5) — requests, decisions, delegation, resolution.

Chain events on the folder's signed log (no new store, the parties.py
pattern); ``resolve_approval`` is a PURE projection of (chain, now) — same
inputs, same answer, and nothing is written at resolve time.

The control-form algebra gives a request its requirements:

- ``pre_approval``      → at least one counting approval;
- ``two_approvers``     → two DISTINCT counting approvers (one hand twice
                          is one approver);
- ``competent_approver``→ the approver holds the routed competence, or has
                          a LOGGED delegation from a holder (absence →
                          delegate is an explicit grant on the chain, never
                          an ambient power);
- ``blocked``           → never grantable.

Hard rules, all fail-closed: TIMEOUT IS DENY (silence is never consent; a
late approval cannot resurrect a timed-out request — counting stops at the
deadline); deny is immediate and absorbing; a counting hand is an ACTIVE
HUMAN that is not the requester (agents and suspended/killed parties never
count, the kill switch retro-invalidates). Delegation requires the
delegator to hold the competence; the grant itself is audited.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .controlforms import (
    G_BLOCKED, G_COMPETENCE, G_PRE_APPROVAL, G_TWO_APPROVERS, guarantees,
)
from .mutation_log import LogEvent, MutationLog
from .parties import list_parties

DECISIONS = ("approve", "deny")


def _append(folder_context: str, actor: str, log_root, extra: dict) -> str:
    log = MutationLog(folder_context, log_root=log_root)
    return log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"approval:{extra.get('request_id', extra.get('competence', ''))}",
        channel="system",
        actor=actor,
        extra=extra,
    ))


def _replay(folder_context: str, log_root) -> list[LogEvent]:
    return list(MutationLog(folder_context, log_root=log_root).replay())


def _request_event(events, request_id: str) -> Optional[dict]:
    for e in events:
        x = e.extra or {}
        if x.get("kind") == "ApprovalRequested" and \
                x.get("request_id") == request_id:
            return x
    return None


def request_approval(
    folder_context: str,
    request_id: str,
    *,
    form: str = "single_approver",
    competence: str = "",
    requester: str = "",
    timeout_seconds: float = 86400.0,
    now: float,
    actor: str = "",
    quorum: int = 0,
    competences: Optional[list[str]] = None,
    on_elapse: str = "halt",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Open an approval request under a named control form (validated —
    an invented form is an error at request time, not at resolve time).

    ``quorum`` (m-of-n): when >0, exactly this many DISTINCT counting hands are
    required, overriding the form's default count. ``competences`` is the quorum's
    role set — an approver counts if they hold ``competence`` OR any of these.
    ``on_elapse`` ∈ {halt, proceed}: at the deadline, ``halt`` denies (timeout-is-deny,
    the safe default), ``proceed`` grants (fail-OPEN; the caller must have applied the
    reserved-by-law guardrail before choosing it)."""
    guarantees(form)                       # closed vocabulary, raises
    if not request_id:
        raise ValueError("request_id required")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if on_elapse not in ("halt", "proceed"):
        raise ValueError("on_elapse must be 'halt' or 'proceed'")
    audit_id = _append(folder_context, actor or requester or "system",
                       log_root, {
                           "kind": "ApprovalRequested",
                           "request_id": request_id,
                           "form": form,
                           "competence": competence,
                           "competences": list(competences or []),
                           "quorum": int(quorum),
                           "on_elapse": on_elapse,
                           "requester": requester,
                           "timeout_seconds": float(timeout_seconds),
                           "requested_at": float(now),
                       })
    return {"ok": True, "request_id": request_id, "audit_id": audit_id}


def request_from_reservation(
    folder_context: str,
    request_id: str,
    reservation: dict[str, Any],
    *,
    requester: str = "",
    now: float,
    actor: str = "",
    default_timeout_seconds: float = 86400.0,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Open the approval request a Loomground ``reserved`` verdict implies.

    The engine produces ``reserved`` carrying the reservation's target and duration but
    does not act on them; this bridges that reservation
    (``{kind, by, when?, duration?, on_elapse?}``) to the control-form + m-of-n quorum +
    deadline + elapse policy and opens the live request the patchbay drives. The pure
    mapping lives in ``reservation_bridge``; this adds only the I/O."""
    from .reservation_bridge import duration_to_seconds, reservation_to_request
    spec = reservation_to_request(reservation)
    timeout = (duration_to_seconds(spec["duration"])
               if spec.get("duration") else default_timeout_seconds)
    primary = spec["competence"][0] if spec.get("competence") else ""
    return request_approval(
        folder_context, request_id,
        form=spec["control_form"],
        competence=primary,
        competences=spec.get("quorum_set") or [],
        quorum=spec.get("quorum_m") or 0,
        on_elapse=spec.get("on_elapse", "halt"),
        timeout_seconds=timeout,
        requester=requester, now=now, actor=actor, log_root=log_root,
    )


def decide_approval(
    folder_context: str,
    request_id: str,
    decision: str,
    *,
    actor: str,
    now: float,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Record one party's decision. Whether it COUNTS is the projection's
    call (active human, not the requester, inside the deadline) — the event
    lands either way so the attempt is on the chain."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    events = _replay(folder_context, log_root)
    if _request_event(events, request_id) is None:
        raise ValueError(f"unknown approval request {request_id!r}")
    audit_id = _append(folder_context, actor, log_root, {
        "kind": "ApprovalDecision",
        "request_id": request_id,
        "decision": decision,
        "decided_at": float(now),
    })
    return {"ok": True, "request_id": request_id, "decision": decision,
            "audit_id": audit_id}


def delegate_competence(
    folder_context: str,
    competence: str,
    *,
    from_party: str,
    to_party: str,
    actor: str,
    now: float,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Absence → delegate: a LOGGED grant of one competence from a holder
    to another human. Refused unless the delegator is an active human who
    holds the competence and the delegate is a registered human."""
    rows = list_parties(folder_context, log_root=log_root)["parties"]
    by_id = {r["party_id"]: r for r in rows}
    src, dst = by_id.get(from_party), by_id.get(to_party)
    if (src is None or src.get("party_kind") != "human"
            or src.get("status") != "active"
            or competence not in (src.get("competences") or [])):
        raise ValueError(
            f"{from_party!r} is not an active human holding {competence!r}; "
            f"delegation is a grant of what one HAS")
    if dst is None or dst.get("party_kind") != "human":
        raise ValueError(f"{to_party!r} is not a registered human")
    audit_id = _append(folder_context, actor, log_root, {
        "kind": "CompetenceDelegated",
        "competence": competence,
        "from_party": from_party,
        "to_party": to_party,
        "delegated_at": float(now),
    })
    return {"ok": True, "competence": competence, "from_party": from_party,
            "to_party": to_party, "audit_id": audit_id}


def delegate_signing(
    folder_context: str,
    *,
    from_party: str,
    to_party: str,
    actor: str,
    now: float,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """#58 — a LOGGED grant of *signing authority* from one signer to another:
    ``to_party`` may record approval sign-offs on ``from_party``'s behalf. Unlike
    competence delegation this carries NO competence (the decided rule for the
    contract-approval path). Refused unless the delegator is an active human (a
    valid signer) and the delegate is a registered, active human. Fail-closed: an
    unreadable party registry refuses rather than delegating."""
    try:
        rows = list_parties(folder_context, log_root=log_root)["parties"]
    except Exception:
        raise ValueError("cannot verify the party registry — refusing to delegate signing (fail-closed)")
    by_id = {r.get("party_id"): r for r in rows if isinstance(r, dict) and r.get("party_id")}
    src, dst = by_id.get(from_party), by_id.get(to_party)
    if src is None or src.get("party_kind") != "human" or src.get("status") != "active":
        raise ValueError(f"{from_party!r} is not an active human — cannot delegate signing")
    if dst is None or dst.get("party_kind") != "human" or dst.get("status") != "active":
        raise ValueError(f"{to_party!r} is not a registered, active human")
    audit_id = _append(folder_context, actor, log_root, {
        "kind": "SigningDelegated",
        "from_party": from_party,
        "to_party": to_party,
        "delegated_at": float(now),
    })
    return {"ok": True, "from_party": from_party, "to_party": to_party,
            "audit_id": audit_id}


def _holds_competence(party: dict, competence: str, *,
                      delegations: list[dict]) -> bool:
    if not competence:
        return True
    if competence in (party.get("competences") or []):
        return True
    return any(d.get("to_party") == party.get("party_id")
               and d.get("competence") == competence
               for d in delegations)


def _holds_any(party: dict, competences: list[str], *,
               delegations: list[dict]) -> bool:
    """A quorum draws from a SET of roles; a hand counts if it holds any one of
    them (or its delegation). An empty set means no competence is routed → any
    active human counts."""
    comps = [c for c in competences if c]
    if not comps:
        return True
    return any(_holds_competence(party, c, delegations=delegations) for c in comps)


def resolve_approval(
    folder_context: str,
    request_id: str,
    *,
    now: float,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Pure projection: granted | denied | pending, with the reason."""
    events = _replay(folder_context, log_root)
    req = _request_event(events, request_id)
    if req is None:
        raise ValueError(f"unknown approval request {request_id!r}")
    g = guarantees(req["form"])
    deadline = req["requested_at"] + req["timeout_seconds"]
    base = {"request_id": request_id, "form": req["form"],
            "competence": req["competence"], "deadline": deadline,
            "competences": list(req.get("competences") or []),
            "quorum": req.get("quorum") or 0,
            "on_elapse": req.get("on_elapse", "halt"),
            "requested_at": req.get("requested_at"),
            "requester": req.get("requester", "")}

    if G_BLOCKED in g:
        return {**base, "state": "denied", "reason": "blocked"}

    parties = list_parties(folder_context, log_root=log_root)["parties"]
    by_id = {r["party_id"]: r for r in parties}
    delegations = [e.extra for e in events
                   if (e.extra or {}).get("kind") == "CompetenceDelegated"
                   and e.extra["delegated_at"] <= min(now, deadline)]

    def counts(e: LogEvent) -> bool:
        """A counting hand: active human, not the requester, competent (or
        delegated), decided inside both the deadline and `now`."""
        x = e.extra or {}
        if x.get("kind") != "ApprovalDecision" or \
                x.get("request_id") != request_id:
            return False
        if x["decided_at"] > min(now, deadline):
            return False
        party = by_id.get(e.actor)
        if party is None or party.get("party_kind") != "human" or \
                party.get("status") != "active":
            return False
        if e.actor == req["requester"]:
            return False
        if x["decision"] != "approve":
            return True
        comps = ([req["competence"]] if req.get("competence") else []) \
            + list(req.get("competences") or [])
        return _holds_any(party, comps, delegations=delegations)

    counting = [e for e in events if counts(e)]
    for e in counting:                                  # deny absorbs
        if e.extra["decision"] == "deny":
            return {**base, "state": "denied",
                    "reason": f"denied-by-{e.actor}"}
    approvers = sorted({e.actor for e in counting
                        if e.extra["decision"] == "approve"})

    # How many counting approvals the form demands. Zero (auto / notify /
    # spot_check) grants by construction — those forms' guarantees live in
    # notification and sampling, not in this gate.
    needed = req.get("quorum") or (2 if G_TWO_APPROVERS in g else (
        1 if (G_PRE_APPROVAL in g or _needs_window(g)) else 0))
    if len(approvers) >= needed:
        return {**base, "state": "granted", "approvers": approvers}
    if now > deadline:
        # halt (default) denies on elapse — timeout-is-deny, fail-closed.
        # proceed grants on elapse — fail-OPEN, opt-in; the reserved-by-law
        # guardrail is applied upstream (engine apply-stage), never here.
        if req.get("on_elapse") == "proceed":
            return {**base, "state": "granted", "approvers": approvers,
                    "reason": "elapsed-proceed"}
        return {**base, "state": "denied", "reason": "timeout"}
    return {**base, "state": "pending", "approvers": approvers,
            "needed": needed}


def _needs_window(g: frozenset) -> bool:
    """veto_window-style forms (veto-expires-to-deny without pre-approval):
    fail-closed — silence at expiry is DENY, an explicit approval grants."""
    from .controlforms import G_VETO_DENY
    return G_VETO_DENY in g and G_PRE_APPROVAL not in g


def list_approvals(
    folder_context: str,
    *,
    now: float,
    state: Optional[str] = None,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """The §1.5 approval inbox: every request on the chain, resolved at ``now``.

    A pure projection over the signed log — one entry per distinct ApprovalRequested,
    each carrying its resolved state (granted | denied | pending), the m-of-n
    ``needed``/``approvers``, the ``deadline`` and ``on_elapse`` policy, and the role
    ``competences`` the quorum draws from. ``state`` filters the result. This is the
    role-based (no-id) counterpart to the named-signer contract reviews — surfaced
    beside them in the patchbay, not merged with them."""
    events = _replay(folder_context, log_root)
    ids: list[str] = []
    seen: set[str] = set()
    for e in events:
        x = e.extra or {}
        if x.get("kind") == "ApprovalRequested":
            rid = x.get("request_id")
            if rid and rid not in seen:
                seen.add(rid)
                ids.append(rid)
    out = [resolve_approval(folder_context, rid, now=now, log_root=log_root)
           for rid in ids]
    if state is not None:
        out = [r for r in out if r.get("state") == state]
    return {"ok": True, "approvals": out}


def effect_approval(
    folder_context: str,
    request_id: str,
    *,
    now: float,
    actor: str = "system",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve, and if the deadline elapsed into a fail-OPEN ``proceed`` grant with no
    human, record a distinct ``ElapsedProceed`` audit event — ONCE (idempotent).

    ``resolve_approval`` is a pure projection and must not write; this is the effecting
    entry point a caller uses when it is about to ACT on the resolution. Guardrail #3
    a proceed-elapse means the action went ahead with
    *no person deciding* — the signed trail must say so, separately from any human
    ``approve``. ``halt`` elapses and normal human grants record nothing here."""
    res = resolve_approval(folder_context, request_id, now=now, log_root=log_root)
    if res.get("state") == "granted" and res.get("reason") == "elapsed-proceed":
        events = _replay(folder_context, log_root)
        already = any((e.extra or {}).get("kind") == "ElapsedProceed"
                      and (e.extra or {}).get("request_id") == request_id
                      for e in events)
        if not already:
            audit_id = _append(folder_context, actor, log_root, {
                "kind": "ElapsedProceed",
                "request_id": request_id,
                "elapsed_at": float(now),
                "reason": "deadline elapsed with no sign-off; on_elapse=proceed (fail-open) — no person decided",
            })
            res = {**res, "audit_id": audit_id, "recorded": "ElapsedProceed"}
    return res
