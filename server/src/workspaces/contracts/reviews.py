# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Contract-review persistence + approval queue.

Adds two new event kinds to the mutation log so the Contract Governance
Workbench artifact can show traffic-light status across many documents
and surface pending approvals without re-running reviews on every load:

* ``contract-review``    — one event per Composer Hand-Off, carries the
                           full findings_json + decision + derived
                           traffic-light. ``contract_id`` is the document
                           identity and is repeated on each review (latest
                           wins for traffic-light status).

* ``contract-approval``  — one event per approval lifecycle transition
                           (``request`` / ``signoff`` / ``finalise`` /
                           ``expire``). Mirrors workspace-music-business's
                           ``signer-flow`` state model but at the contract
                           level, not the credit-roll level.

Both event kinds are stored as ``LogEvent(event="system", pair_id=...,
extra={...})`` in the standard folder-scoped MutationLog. No new storage
format, no schema migration — Workspace MCP already reads this log and
exposes audit-by-id via ``get_audit_event``.

Traffic-light derivation (deterministic, no LLM call):
    GREEN  — decision = "Approve"  AND no Critical / High findings open
                                   AND all required approvers signed
    AMBER  — decision = "Approve with Conditions"  OR
             High findings open                    OR
             approvers pending
    RED    — decision = "Block"                    OR
             any Critical finding present          OR
             approval explicitly rejected
    GREY   — no review yet
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ..mutation_log import LogEvent, MutationLog


PAIR_CONTRACT_REVIEW = "contract-review"
PAIR_CONTRACT_APPROVAL = "contract-approval"

# Findings payload above this size is spilled to a sibling JSON file
# so the mutation log JSONL stays grep-friendly.
MAX_INLINE_FINDINGS_BYTES = 16 * 1024


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Traffic-light derivation
# ---------------------------------------------------------------------------

TRAFFIC_GREEN = "green"
TRAFFIC_AMBER = "amber"
TRAFFIC_RED = "red"
TRAFFIC_GREY = "grey"


def derive_traffic_light(decision: str,
                          findings: list[dict[str, Any]],
                          approvals_pending: int = 0,
                          approvals_rejected: int = 0) -> str:
    """Pure function — given Composer decision + findings list + approval
    state, return one of green/amber/red/grey.

    Findings are dicts with at least ``severity`` ∈
    {Critical, High, Medium, Low}.
    """
    if approvals_rejected > 0:
        return TRAFFIC_RED
    decision_norm = (decision or "").strip().lower()
    severities = {(f.get("severity") or "").lower() for f in findings}
    if decision_norm == "block" or "critical" in severities:
        return TRAFFIC_RED
    if decision_norm == "approve with conditions" or "high" in severities or approvals_pending > 0:
        return TRAFFIC_AMBER
    if decision_norm == "approve":
        return TRAFFIC_GREEN
    return TRAFFIC_GREY


# ---------------------------------------------------------------------------
# Contract review persistence
# ---------------------------------------------------------------------------

def record_contract_review(folder_path: str | Path,
                            *,
                            contract_id: str,
                            decision: str,
                            findings_json: dict[str, Any] | list[Any] | None = None,
                            actor: str = "system",
                            jurisdiction_anchors: Optional[list[str]] = None,
                            audience_side: str = "",
                            contract_type: str = "",
                            total_value_eur: Optional[float] = None,
                            run_id: str = "",
                            log_root: Optional[Path] = None) -> dict[str, Any]:
    """Persist a contract review to the folder's mutation log.

    ``findings_json`` is the Composer's Hand-Off envelope (or the
    ``3_consolidated_findings`` array — caller's choice). If larger than
    ``MAX_INLINE_FINDINGS_BYTES`` it spills to a sibling file
    ``<log_root>/<folder_hash>/contract-reviews/<audit_id>.json`` and the
    LogEvent.extra references it.
    """
    if not contract_id:
        raise ValueError("contract_id is required")

    log = MutationLog(folder_path, log_root=log_root)

    # Extract findings list for traffic-light derivation
    findings_list: list[dict[str, Any]] = []
    if isinstance(findings_json, dict):
        sections = findings_json.get("sections") or {}
        f = sections.get("3_consolidated_findings")
        if isinstance(f, list):
            findings_list = f
        elif isinstance(findings_json.get("findings"), list):
            findings_list = findings_json["findings"]
    elif isinstance(findings_json, list):
        findings_list = findings_json

    traffic_light = derive_traffic_light(decision, findings_list)

    # Possibly spill large payloads
    findings_serialised = json.dumps(findings_json or {}, ensure_ascii=False)
    spill_path: Optional[str] = None
    extra: dict[str, Any] = {
        "contract_id":          contract_id,
        "decision":             decision or "",
        "traffic_light":        traffic_light,
        "audience_side":        audience_side,
        "contract_type":        contract_type,
        "jurisdictions":        list(jurisdiction_anchors or []),
        "total_value_eur":      total_value_eur,
        "findings_count":       len(findings_list),
        "findings_by_severity": _count_by_severity(findings_list),
        "run_id":               run_id,
        "reviewed_at":          _now_iso(),
    }

    audit_id = str(uuid.uuid4())

    if len(findings_serialised.encode("utf-8")) > MAX_INLINE_FINDINGS_BYTES:
        # Spill to sibling file
        from ..mutation_log import folder_hash, LOG_ROOT_DEFAULT
        root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
        spill_dir = root / folder_hash(folder_path) / "contract-reviews"
        spill_dir.mkdir(parents=True, exist_ok=True)
        sf = spill_dir / f"{audit_id}.json"
        sf.write_text(findings_serialised, encoding="utf-8")
        spill_path = str(sf)
        extra["findings_spill_path"] = spill_path
    else:
        extra["findings_json"] = findings_json

    event = LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id=PAIR_CONTRACT_REVIEW,
        lifecycle_state="",
        channel="system",
        actor=actor or "system",
        audit_id=audit_id,
        extra=extra,
    )
    log.append(event)
    return {
        "audit_id":      audit_id,
        "contract_id":   contract_id,
        "traffic_light": traffic_light,
        "spill_path":    spill_path,
    }


def _count_by_severity(findings: list[dict[str, Any]]) -> dict[str, int]:
    out = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for f in findings:
        s = (f.get("severity") or "").strip().capitalize()
        if s in out:
            out[s] += 1
    return out


def _load_spilled_findings(spill_path: str) -> dict[str, Any]:
    try:
        return json.loads(Path(spill_path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def list_contract_reviews(folder_path: str | Path,
                           *,
                           filters: Optional[dict[str, Any]] = None,
                           include_findings: bool = False,
                           log_root: Optional[Path] = None) -> list[dict[str, Any]]:
    """Return all contract reviews in the folder, newest-first per contract.

    For each ``contract_id`` only the LATEST review is returned (the
    traffic-light reflects current state). Set ``include_findings=True``
    to embed the full findings_json (incurs spill-file reads).

    Filters dict supports:
        decision         — substring match against Composer decision
        traffic_light    — exact match on green / amber / red / grey
        jurisdiction     — must appear in jurisdiction_anchors
        audience_side    — exact match
        min_severity     — Critical / High / Medium / Low — return only
                           reviews with at least one finding ≥ this level
        since            — ISO timestamp lower bound on reviewed_at
        contract_id      — exact match on a specific contract
    """
    log = MutationLog(folder_path, log_root=log_root)
    latest: dict[str, dict[str, Any]] = {}
    for evt in log.replay():
        if evt.pair_id != PAIR_CONTRACT_REVIEW:
            continue
        cid = (evt.extra or {}).get("contract_id") or ""
        if not cid:
            continue
        existing = latest.get(cid)
        if existing is None or evt.ts > existing.get("_ts", 0):
            row = {
                "audit_id":             evt.audit_id,
                "contract_id":          cid,
                "decision":             evt.extra.get("decision", ""),
                "traffic_light":        evt.extra.get("traffic_light", TRAFFIC_GREY),
                "audience_side":        evt.extra.get("audience_side", ""),
                "contract_type":        evt.extra.get("contract_type", ""),
                "jurisdictions":        evt.extra.get("jurisdictions", []) or [],
                "total_value_eur":      evt.extra.get("total_value_eur"),
                "findings_count":       evt.extra.get("findings_count", 0),
                "findings_by_severity": evt.extra.get("findings_by_severity", {}),
                "reviewed_at":          evt.extra.get("reviewed_at", ""),
                "run_id":               evt.extra.get("run_id", ""),
                "actor":                evt.actor,
                "_ts":                  evt.ts,
            }
            if include_findings:
                spill = evt.extra.get("findings_spill_path")
                if spill:
                    row["findings_json"] = _load_spilled_findings(spill)
                else:
                    row["findings_json"] = evt.extra.get("findings_json") or {}
            latest[cid] = row

    rows = list(latest.values())
    # Apply filters
    filters = filters or {}
    out: list[dict[str, Any]] = []
    severity_order = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
    for r in rows:
        if "decision" in filters and (filters["decision"].lower() not in r["decision"].lower()):
            continue
        if "traffic_light" in filters and r["traffic_light"] != filters["traffic_light"]:
            continue
        if "audience_side" in filters and r["audience_side"] != filters["audience_side"]:
            continue
        if "jurisdiction" in filters and filters["jurisdiction"] not in (r["jurisdictions"] or []):
            continue
        if "contract_id" in filters and r["contract_id"] != filters["contract_id"]:
            continue
        if "since" in filters and r["reviewed_at"] < filters["since"]:
            continue
        if "min_severity" in filters:
            threshold = severity_order.get(filters["min_severity"], 0)
            counts = r["findings_by_severity"] or {}
            has = any(
                counts.get(level, 0) > 0
                for level, rank in severity_order.items() if rank >= threshold
            )
            if not has:
                continue
        out.append(r)
    # Sort newest first
    out.sort(key=lambda r: r.get("_ts", 0), reverse=True)
    for r in out:
        r.pop("_ts", None)
    return out


# ---------------------------------------------------------------------------
# Approval queue
# ---------------------------------------------------------------------------

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_EXPIRED = "expired"


def request_contract_approval(folder_path: str | Path,
                                *,
                                contract_id: str,
                                signers: list[str],
                                deadline: str = "",
                                requested_by: str = "system",
                                reason: str = "",
                                action_summary: str = "",
                                idempotency_key: str = "",
                                log_root: Optional[Path] = None) -> dict[str, Any]:
    """Create a new approval request. Returns the new approval_id (== audit_id).

    ``deadline``, when given, must be a valid ISO calendar date or ISO 8601
    timestamp — validated at write (NT-11 typed-date-at-write), never parsed
    defensively at read.

    ``signers`` is a list of identifiers (emails, names, role labels) the
    approval is being requested from. Each signer's individual decision
    is tracked separately on subsequent ``record_contract_approval`` calls.

    ``action_summary`` is a human-readable one-liner describing what is being
    approved — required context when the request originates outside a chat
    session (e.g. a workflow engine behind the gateway).

    ``idempotency_key``, when non-empty, makes the call replay-safe: a
    second call with the same (contract_id, requested_by, idempotency_key)
    returns the EXISTING approval (``deduplicated: True``) instead of
    minting a new one. Workflow engines deliver at-least-once; without
    this, every retry files a fresh approval and the human signs the wrong
    one.
    """
    if not contract_id:
        raise ValueError("contract_id is required")
    if not signers:
        raise ValueError("at least one signer is required")
    # Distinct roster: a duplicated name ("bob","bob") must not look like two required
    # hands then be satisfied by one signature — collapse to the distinct identities.
    signers = list(dict.fromkeys(signers))
    if deadline:
        from workspaces.adapters.solver.temporal import TemporalError, validate_iso_instant
        try:
            validate_iso_instant(deadline)
        except TemporalError as exc:
            raise ValueError(
                f"deadline must be a valid ISO date/timestamp (NT-11): {deadline!r}") from exc
    log = MutationLog(folder_path, log_root=log_root)
    if idempotency_key:
        for evt in log.replay():
            if evt.pair_id != PAIR_CONTRACT_APPROVAL:
                continue
            if evt.lifecycle_state != "request":
                continue
            ex = evt.extra or {}
            if (ex.get("idempotency_key") == idempotency_key
                    and ex.get("contract_id") == contract_id
                    and ex.get("requested_by") == (requested_by or "system")):
                return {
                    "approval_id":  ex.get("approval_id", ""),
                    "contract_id":  contract_id,
                    "state":        ex.get("state", APPROVAL_PENDING),
                    "signers":      list(ex.get("signers") or []),
                    "deduplicated": True,
                }
    audit_id = str(uuid.uuid4())
    event = LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id=PAIR_CONTRACT_APPROVAL,
        lifecycle_state="request",
        channel="system",
        actor=requested_by or "system",
        audit_id=audit_id,
        extra={
            "approval_id":     audit_id,
            "contract_id":     contract_id,
            "signers":         list(signers),
            "deadline":        deadline or "",
            "reason":          reason or "",
            "action_summary":  action_summary or "",
            "idempotency_key": idempotency_key or "",
            "requested_at":    _now_iso(),
            "requested_by":    requested_by or "system",
            "state":           APPROVAL_PENDING,
        },
    )
    log.append(event)
    return {
        "approval_id": audit_id,
        "contract_id": contract_id,
        "state":       APPROVAL_PENDING,
        "signers":     list(signers),
        "deduplicated": False,
    }


def record_contract_approval(folder_path: str | Path,
                              *,
                              approval_id: str,
                              signer: str,
                              decision: str,
                              comment: str = "",
                              actor: str = "",
                              log_root: Optional[Path] = None) -> dict[str, Any]:
    """Record one signer's decision against an existing approval request.

    ``decision`` is one of ``"approved" | "rejected" | "expired"``.
    The first ``rejected`` flips the overall approval to rejected.
    When every signer has approved, the overall state flips to approved.
    """
    if decision not in (APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_EXPIRED):
        raise ValueError(f"unknown decision: {decision!r}")
    log = MutationLog(folder_path, log_root=log_root)

    # Replay to find the original request + any prior signoffs
    request_evt: Optional[LogEvent] = None
    prior_signoffs: list[LogEvent] = []
    for evt in log.replay():
        if evt.pair_id != PAIR_CONTRACT_APPROVAL:
            continue
        if (evt.extra or {}).get("approval_id") != approval_id:
            continue
        if evt.lifecycle_state == "request":
            request_evt = evt
        elif evt.lifecycle_state == "signoff":
            prior_signoffs.append(evt)
    if request_evt is None:
        raise ValueError(f"no approval request found for approval_id={approval_id!r}")

    # D12/M6 — authority: only a signer the approval was REQUESTED from may record a
    # decision. A signer absent from the request roster has no authority over this
    # approval, so accepting their sign-off would let any caller approve a contract.
    # Fail closed: reject rather than record an unauthorized sign-off.
    requested_signers = list(request_evt.extra.get("signers") or [])
    if signer not in requested_signers:
        raise ValueError(
            f"signer {signer!r} is not on this approval's roster — not authorized to sign")

    # #58 — when the workspace opts into access control, a sign-off must come from
    # a registered, ACTIVE *human*, and the recording caller (actor) must be that
    # signer OR a party the signer has delegated authority to. Competence is NOT
    # required on this path (the decided rule). Fail-closed. OFF (the default) keeps
    # the free-text local-first path — only the roster check above applies.
    from ..authorization import access_control_on
    if access_control_on(str(folder_path)):
        from ..parties import list_parties
        # Any failure to READ the registry refuses the sign-off (fail-closed) —
        # never record on an unverifiable register.
        try:
            rows = list_parties(str(folder_path), log_root=log_root).get("parties", [])
        except Exception:
            raise ValueError(
                "cannot verify the party registry — refusing the sign-off (fail-closed)")
        by_id = {r.get("party_id"): r for r in rows if isinstance(r, dict)}

        def _active_human(pid: str) -> bool:
            p = by_id.get(pid)
            return bool(p) and p.get("status") == "active" and p.get("party_kind") == "human"

        # The signer being signed-for must be a registered, ACTIVE human.
        if not _active_human(signer):
            raise ValueError(
                f"signer {signer!r} is not a registered, active person — not authorized to sign")
        # The recording caller must be NAMED, itself a registered active human, and
        # either the signer or a CURRENT delegate of the signer. A blank actor when
        # access control is on is unattributable → reject (no silent self-sign).
        recorder = (actor or "").strip()
        if not recorder:
            raise ValueError(
                "access control is on — a named actor is required to record a sign-off")
        if not _active_human(recorder):
            raise ValueError(
                f"actor {recorder!r} is not a registered, active person — not authorized to sign")
        if recorder != signer:
            delegated = any(
                (e.extra or {}).get("kind") == "SigningDelegated"
                and e.extra.get("from_party") == signer
                and e.extra.get("to_party") == recorder
                for e in log.replay())
            if not delegated:
                raise ValueError(
                    f"actor {recorder!r} is neither {signer!r} nor a delegate of theirs — not authorized to sign")

    # Append the signoff event
    event = LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id=PAIR_CONTRACT_APPROVAL,
        lifecycle_state="signoff",
        channel="system",
        actor=actor or signer,
        audit_id=str(uuid.uuid4()),
        extra={
            "approval_id":  approval_id,
            "contract_id":  request_evt.extra.get("contract_id", ""),
            "signer":       signer,
            "decision":     decision,
            "comment":      comment or "",
            "signed_at":    _now_iso(),
        },
    )
    log.append(event)

    # Compute overall state. signer_decisions uses the SAME nested shape as
    # list_contract_approvals (decision/comment/signed_at) so the UI can read
    # ``d.decision`` uniformly regardless of which call produced the row.
    # Replayed decisions are validated against the known set: a log line with
    # an arbitrary decision string is skipped (fail-closed — an unknown value
    # never counts as an approval), not trusted into the tally.
    signers = list(request_evt.extra.get("signers") or [])
    all_signoffs = prior_signoffs + [event]
    signer_decisions: dict[str, dict[str, Any]] = {}
    for s in all_signoffs:
        sx = s.extra.get("signer", "")
        if not sx:
            continue
        dec = s.extra.get("decision", "")
        if dec not in (APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_EXPIRED):
            # Unknown/forged decision — do not let it count toward state.
            continue
        signer_decisions[sx] = {
            "decision":  dec,
            "comment":   s.extra.get("comment", ""),
            "signed_at": s.extra.get("signed_at", ""),
        }
    decisions = [v["decision"] for v in signer_decisions.values()]
    # Separation of duty (mirrors §1.5 "the requester's own hand never counts"): a
    # signer cannot APPROVE a request they themselves raised. A reject still absorbs
    # (incl. the requester's). If the requester is a required signer, their slot can
    # never be self-satisfied, so the request stays pending until another hand signs.
    requester = request_evt.extra.get("requested_by")
    def _approved(s):
        return s != requester and signer_decisions.get(s, {}).get("decision") == APPROVAL_APPROVED
    overall = APPROVAL_PENDING
    if any(d == APPROVAL_REJECTED for d in decisions):
        overall = APPROVAL_REJECTED
    elif signers and all(_approved(s) for s in signers):
        overall = APPROVAL_APPROVED

    return {
        "approval_id":     approval_id,
        "contract_id":     request_evt.extra.get("contract_id", ""),
        "overall_state":   overall,
        "signer_decisions": signer_decisions,
    }


def list_contract_approvals(folder_path: str | Path,
                             *,
                             state: Optional[str] = None,
                             contract_id: Optional[str] = None,
                             log_root: Optional[Path] = None) -> list[dict[str, Any]]:
    """Return approval requests in the folder, newest-first.

    Filter by ``state ∈ pending/approved/rejected/expired`` or a specific
    ``contract_id``. Each row carries the request metadata plus the
    aggregated signer decisions and the derived overall state.
    """
    log = MutationLog(folder_path, log_root=log_root)
    requests: dict[str, dict[str, Any]] = {}
    signoffs_by_approval: dict[str, list[LogEvent]] = {}

    # Fail-closed: a replay that raises (IO error, decrypt failure, truncated
    # stream) must NOT yield a partially-built queue that reads as "fewer/no
    # approvals pending" — that would be a fail-OPEN where unverified state
    # looks clean. Surface the error with context so the caller knows the
    # queue could not be reconstructed, rather than trusting a silent truncation.
    try:
        for evt in log.replay():
            if evt.pair_id != PAIR_CONTRACT_APPROVAL:
                continue
            aid = (evt.extra or {}).get("approval_id") or ""
            if not aid:
                continue
            if evt.lifecycle_state == "request":
                requests[aid] = {
                    "approval_id":   aid,
                    "contract_id":   evt.extra.get("contract_id", ""),
                    "signers":       list(evt.extra.get("signers") or []),
                    "deadline":      evt.extra.get("deadline", ""),
                    "reason":        evt.extra.get("reason", ""),
                    "action_summary": evt.extra.get("action_summary", ""),
                    "requested_at":  evt.extra.get("requested_at", ""),
                    "requested_by":  evt.extra.get("requested_by", ""),
                    "_ts":           evt.ts,
                }
            elif evt.lifecycle_state == "signoff":
                signoffs_by_approval.setdefault(aid, []).append(evt)
    except Exception as exc:
        raise RuntimeError(
            f"approval-queue replay failed for {folder_path!r}: "
            f"refusing to return a partial queue (fail-closed)") from exc

    out: list[dict[str, Any]] = []
    now_iso = _now_iso()
    for aid, req in requests.items():
        signoffs = signoffs_by_approval.get(aid, [])
        signer_decisions: dict[str, dict[str, Any]] = {}
        for s in signoffs:
            sx = s.extra.get("signer", "")
            if not sx:
                continue
            dec = s.extra.get("decision", "")
            if dec not in (APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_EXPIRED):
                # Unknown/forged decision — never count it toward overall state.
                continue
            signer_decisions[sx] = {
                "decision":  dec,
                "comment":   s.extra.get("comment", ""),
                "signed_at": s.extra.get("signed_at", ""),
            }
        # Compute overall state
        decisions = [v["decision"] for v in signer_decisions.values()]
        _requester = req.get("requested_by")
        if any(d == APPROVAL_REJECTED for d in decisions):
            overall = APPROVAL_REJECTED
        elif req["signers"] and all(
                s != _requester
                and signer_decisions.get(s, {}).get("decision") == APPROVAL_APPROVED
                for s in req["signers"]):
            overall = APPROVAL_APPROVED
        elif req["deadline"] and req["deadline"] < now_iso:
            overall = APPROVAL_EXPIRED
        else:
            overall = APPROVAL_PENDING

        req["overall_state"] = overall
        req["signer_decisions"] = signer_decisions

        if state is not None and overall != state:
            continue
        if contract_id is not None and req["contract_id"] != contract_id:
            continue
        out.append(req)

    out.sort(key=lambda r: r.get("_ts", 0), reverse=True)
    for r in out:
        r.pop("_ts", None)
    return out
