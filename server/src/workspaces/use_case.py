# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Use-case registry — the identity hub.

Every entity in the system was already addressable on its own: a party
(human/agent) has a party_id, a problem shape has a fingerprint, a chain event
has an audit_id, an issue has an issue_id. What was missing is the thing that
BINDS them: a governed use case. This module mints a stable use_case_id and
ties together, in one registered record:

  * the problem fingerprint it governs,
  * the step contract (now with a contract_id) — risk, granted autonomy, debt,
    timed override,
  * the allowed agents (party_ids) permitted to act on it,
  * the reserved human acts it owes,
  * the risk.

So governance becomes a join: agent -> use case -> contract -> problem ->
case -> override, all by ID. Registered as signed chain events (parties
pattern): re-registering appends a new version, never edits; the projection
is latest-wins. Erasure, seal and tamper-evidence inherited from the substrate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog
from .reservation import reserved_acts_for
from .step_contract import RISK_LEVELS, derive_contract


def contract_id_for(risk: str, *, prior_approvals: int = 0,
                    disagreement_rate: float = 0.0,
                    override_window_seconds: int = 0) -> str:
    """A content-addressed id for a step contract: same contract content ->
    same id; change any governing input -> a different id. So a contract is a
    referenceable, comparable object, not an anonymous derivation."""
    contract = derive_contract(
        risk, prior_approvals=prior_approvals,
        disagreement_rate=disagreement_rate,
        override_window_seconds=override_window_seconds)
    blob = json.dumps(contract, sort_keys=True).encode()
    return "ct-" + hashlib.sha256(blob).hexdigest()[:12]


def _append(folder_context: str, actor: str, log_root, extra: dict) -> str:
    log = MutationLog(folder_context, log_root=log_root)
    return log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"usecase:{extra.get('use_case_id', '')}",
        channel="system",
        actor=actor,
        extra=extra,
    ))


def register_use_case(
    folder_context: str,
    *,
    use_case_id: str,
    name: str,
    fingerprint: dict[str, Any],
    risk: str,
    allowed_agents: list[str],
    actor: str,
    prior_approvals: int = 0,
    disagreement_rate: float = 0.0,
    override_window_seconds: int = 0,
    policy_reservations: Optional[dict[str, dict[str, Any]]] = None,
    prohibited: Optional[bool] = None,
    obligations: Optional[list[dict[str, Any]]] = None,
    redress: Optional[list[dict[str, Any]]] = None,
    carry_reserved: Optional[list[dict[str, Any]]] = None,
    tags: Optional[list[str]] = None,
    log_root: Optional[str] = None,
) -> str:
    """Register (or re-version) a governed use case. Binds the contract and
    reserved acts at registration. Fail-closed: needs an actor, a name, and a
    valid risk.

    ``policy_reservations`` (a company/policy reservation map, e.g. from an applied
    governance twin) adds reserved acts on top of any legal reservations the
    fingerprint triggers. ``prohibited=True`` marks the use case as a severed act —
    the gate denies it (NO-GO) regardless of grade (the twin's ``prohibit``)."""
    if not (actor or "").strip():
        raise ValueError("registering a use case needs a named actor")
    if not (name or "").strip():
        raise ValueError("a use case needs a name")
    if risk not in RISK_LEVELS:
        raise ValueError(f"unknown risk {risk!r}; valid: {list(RISK_LEVELS)}")

    contract = derive_contract(
        risk, prior_approvals=prior_approvals,
        disagreement_rate=disagreement_rate,
        override_window_seconds=override_window_seconds)
    cid = contract_id_for(
        risk, prior_approvals=prior_approvals,
        disagreement_rate=disagreement_rate,
        override_window_seconds=override_window_seconds)
    # Reservations come only from what users author or ingest
    # (policy_reservations), never from a baked-in legal catalog. Rvnd is
    # jurisdiction-neutral and must not assert statutes. The Loomground
    # LEGAL_RESERVATIONS catalog is left intact in reservation.py but is not
    # auto-applied here (no issue types are passed for legal lookup).
    reserved = reserved_acts_for([], policy_reservations=policy_reservations)
    # Sticky POLICY reservations (no silent drop, mirroring sticky prohibitions):
    # a re-registration carries the gate's existing *policy* reserved acts forward
    # so adding one company reservation never erases a prior one. LEGAL/professional
    # reservations are NOT carried — they are re-derived fresh from the current
    # fingerprint above, so they track the law (a changed fingerprint must not
    # preserve a stale legal reservation). Dedup by (trigger, act_type, reserved_to).
    # Removing a policy reservation needs an explicit lift action, not a silent drop.
    # A BARE re-registration (e.g. the app wiring an agent via use_case_register, which
    # passes no carry) must therefore inherit the gate's existing policy reservations —
    # else a wire would silently UN-reserve a reserved act (a fail-open). When no carry
    # is supplied, read the prior projection and carry it forward.
    _prior = None
    if carry_reserved is None or prohibited is None:
        _prior = _project(folder_context, log_root).get(use_case_id)
    if carry_reserved is None:
        carry_reserved = (_prior or {}).get("reserved_acts") or []
    # Sticky prohibition (mirroring sticky reservations above): a BARE re-registration
    # must not silently un-prohibit a severed act — that is a governance-WIDENING
    # fail-open (the exact bug the reservation stickiness guards against). When the
    # caller doesn't specify (prohibited is None — the app wiring / patch_netlist /
    # use_case_register op), inherit the prior projection's flag. Lifting a prohibition
    # needs an EXPLICIT prohibited=False (as patch_apply passes when the kind is no
    # longer in the authored prohibition set), never a silent drop.
    if prohibited is None:
        prohibited = bool((_prior or {}).get("prohibited", False))
    if carry_reserved:
        seen = {(a.get("trigger", ""), a.get("act_type", ""), a.get("reserved_to", "")) for a in reserved}
        for a in carry_reserved:
            if a.get("basis_kind") != "policy":     # only policy is sticky; legal re-derives
                continue
            key = (a.get("trigger", ""), a.get("act_type", ""), a.get("reserved_to", ""))
            if key not in seen:
                seen.add(key)
                reserved.append(dict(a))

    _append(folder_context, actor, log_root, {
        "kind": "UseCaseRegistered",
        "use_case_id": use_case_id,
        "name": name,
        "fingerprint": dict(fingerprint or {}),
        "risk": risk,
        "allowed_agents": list(allowed_agents or []),
        "contract": contract,
        "contract_id": cid,
        "reserved_acts": reserved,
        "prohibited": bool(prohibited),
        # Declared duties (obligation) + remedies (redress), persisted to the chain
        # so an applied twin / netlist no longer drops them. They ride WITH the use
        # case (an obligation is borne alongside the act, a remedy guarantees an
        # appeal route) — they are recorded, projected and carried forward, not gates.
        "obligations": [dict(o) for o in (obligations or [])],
        "redress": [dict(r) for r in (redress or [])],
        # User-authored data-lineage tags on the act (the "on top" overlay of the tag
        # data-lens); unioned with connector-derived tags at run time. Neutral facts —
        # the guards that act on them are authored/ingested policy.
        "tags": [str(t) for t in (tags or [])],
        # the contract INPUTS, so a re-registration (e.g. a netlist round-trip)
        # can carry the earned grade forward instead of re-baselining to zero (E1).
        "prior_approvals": int(prior_approvals),
        "disagreement_rate": float(disagreement_rate),
        "override_window_seconds": int(override_window_seconds),
    })
    return use_case_id


def _project(folder_context: str, log_root) -> dict[str, dict[str, Any]]:
    log = MutationLog(folder_context, log_root=log_root)
    recs: dict[str, dict[str, Any]] = {}
    for evt in log.replay():
        extra = evt.extra or {}
        if extra.get("kind") != "UseCaseRegistered":
            continue
        uid = extra.get("use_case_id", "")
        recs[uid] = {k: extra.get(k) for k in (
            "use_case_id", "name", "fingerprint", "risk", "allowed_agents",
            "contract", "contract_id", "reserved_acts", "prohibited",
            "obligations", "redress", "tags",
            "prior_approvals", "disagreement_rate", "override_window_seconds")}
    return recs


def get_use_case(folder_context: str, use_case_id: str,
                 log_root: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Latest-wins projection of one use case, or None."""
    return _project(folder_context, log_root).get(use_case_id)


def list_use_cases(folder_context: str,
                   log_root: Optional[str] = None) -> list[dict[str, Any]]:
    recs = _project(folder_context, log_root)
    return [recs[k] for k in sorted(recs)]


def agent_permitted(folder_context: str, use_case_id: str, agent_id: str,
                    log_root: Optional[str] = None) -> bool:
    """Join check: is this agent on the use case's allowed list? A use case
    that does not exist, or an agent not listed, is not permitted."""
    rec = get_use_case(folder_context, use_case_id, log_root=log_root)
    if rec is None:
        return False
    return agent_id in (rec.get("allowed_agents") or [])


def revoke_agent(folder_context: str, use_case_id: str, agent_id: str, *,
                 actor: str, log_root: Optional[str] = None) -> dict[str, Any]:
    """Remove one agent's authority over one use case — the tighten-only write
    a coverage cell may carry. Re-versions the use case with the agent dropped
    and everything else carried forward (reserved acts, duties, tags, the
    earned grade); granting never happens here. Fail-closed: an unknown use
    case, an agent without authority, or a missing actor refuses."""
    if not (actor or "").strip():
        return {"ok": False, "error": "a revoke must name its actor"}
    rec = get_use_case(folder_context, use_case_id, log_root=log_root)
    if rec is None:
        return {"ok": False, "error": f"unknown use case {use_case_id!r}"}
    allowed = list(rec.get("allowed_agents") or [])
    if agent_id not in allowed:
        return {"ok": False, "error": f"{agent_id!r} holds no authority over"
                                      f" {use_case_id!r} — nothing to revoke"}
    remaining = [a for a in allowed if a != agent_id]
    register_use_case(
        folder_context, use_case_id=use_case_id,
        name=rec.get("name") or use_case_id,
        fingerprint=dict(rec.get("fingerprint") or {}),
        risk=rec.get("risk") or "low",
        allowed_agents=remaining, actor=actor,
        prior_approvals=int(rec.get("prior_approvals") or 0),
        disagreement_rate=float(rec.get("disagreement_rate") or 0.0),
        override_window_seconds=int(rec.get("override_window_seconds") or 0),
        prohibited=bool(rec.get("prohibited")) or None,
        obligations=list(rec.get("obligations") or []) or None,
        redress=list(rec.get("redress") or []) or None,
        carry_reserved=list(rec.get("reserved_acts") or []) or None,
        tags=list(rec.get("tags") or []) or None,
        log_root=log_root)
    return {"ok": True, "use_case_id": use_case_id, "agent_id": agent_id,
            "allowed_agents": remaining}
