# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Operations core — the governed step executor.

Workspaces already has the runtime substrate: a persistent leased queue
(`queue.py`), a background worker (`worker.py`), a workflow lifecycle that
emits signed events, a guardian watchdog and a scheduler. What this module
adds is the GOVERNED CORE the worker calls per unit of work: the loop that
drives one use case through the solver, composing the session's primitives
into a single deterministic decision per node.

For each detected issue it decides a disposition by composing three things,
all already built:

  * the use case's step contract — the EARNED autonomy grade (precedent
    hardens it; risk lowers it; critical is floored);
  * the completeness band — the node's confidence;
  * the reservations — acts the law or policy reserves to a human.

The governing rule: a confident node runs `auto` ONLY if the contract grants
enough autonomy. Autonomy is earned, never assumed — a new use case sends
everything to humans; a hardened one auto-runs the confident nodes. A reserved
issue is `reserved` whatever the grade. A human node carries a timed-override
deadline; `resolve_timeout` applies the contract's on_timeout (proceed on low
risk, halt on higher) when the clock elapses. Every step threads the ids
(use_case_id, contract_id) — the join. Deterministic; the human decision and
the clock are inputs, never invented here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .connectors import list_connectors
from .llm_capture import folder_spend_cents
from rvnd.adapters.solver.loomground import _guard_holds, grade_meets
from .mutation_log import LogEvent, MutationLog
from .policy import verified_cost_cap
from .review_card import review_card
from .use_case import agent_permitted, get_use_case

#: minimum granted grade (L0..L4) at which a confident node may run unattended.
AUTO_GRADE_MIN = 3


def _journal(folder_context: str, actor: str, log_root, extra: dict) -> str:
    log = MutationLog(folder_context, log_root=log_root)
    return log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"run:{extra.get('run_id', extra.get('kind', ''))}",
        channel="system",
        actor=actor,
        extra=extra,
    ))


def hardening_inputs(
    folder_context: str,
    fingerprint: dict[str, Any],
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Read a use case's precedent LIVE so autonomy is earned from real
    history, not passed in by hand: prior_approvals = the human-closed cases
    the case memory holds for this fingerprint; disagreement_rate = the
    calibration ledger's sampled-reuse disagreement. These feed
    ``step_contract.derive_contract`` (more clean approvals → more autonomy;
    disagreement → less)."""
    from .case_index import retrieve
    from .calibration import calibration_report
    hits = retrieve(folder_context, fingerprint, log_root=log_root)
    prior_approvals = sum(h.get("evidence", 0) for h in hits)
    cal = calibration_report(folder_context, log_root=log_root)
    return {"prior_approvals": prior_approvals,
            "disagreement_rate": cal.get("disagreement_rate", 0.0)}


def _refuse(folder_context, use_case_id, agent_id, reason, journal, log_root):
    out = {"use_case_id": use_case_id, "agent_id": agent_id,
           "final": "refused", "reason": reason, "steps": []}
    if journal:
        out["run_id"] = _journal(folder_context, agent_id or "system", log_root,
                                 {"kind": "RunRefused", "use_case_id": use_case_id,
                                  "agent_id": agent_id, "reason": reason})
    return out


def _registered_agent_active(
    folder_context: str,
    agent_id: str,
    log_root: Optional[str],
) -> tuple[bool, str]:
    """Enforce the party kill switch without inventing a second registry.

    A registered agent must be active. An unregistered id retains the legacy
    authority-cord behavior until session capabilities make registration
    mandatory at dispatch. Failure to read the registry refuses: an
    unavailable kill-switch projection must never be interpreted as active.
    """
    try:
        from .parties import list_parties
        parties = list_parties(
            folder_context, kind="agent", log_root=log_root
        ).get("parties", [])
    except Exception as exc:
        return False, f"agent status unavailable: {type(exc).__name__}"
    party = next(
        (row for row in parties if row.get("party_id") == agent_id),
        None,
    )
    if party is None:
        return True, ""
    status = str(party.get("status") or "active")
    if status != "active":
        return False, f"agent is {status}"
    return True, ""


def operate(
    folder_context: str,
    *,
    use_case_id: str,
    agent_id: str,
    issues: list[dict[str, Any]],
    now_epoch: int,
    capability_token: str = "",
    journal: bool = True,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Drive one use case over a set of detected issues, returning a run
    record: per-issue dispositions + the final state. Refuses the whole run
    if the use case is unknown or the agent is not permitted on it.

    With ``journal`` (default), every run writes signed chain events —
    RunStarted, a RunStep per issue, RunOutcome (or RunRefused) — so the run
    is auditable and replayable. Auto steps are journalled but never become
    human-closed evidence; that separation is the calibration ledger's."""
    if journal:
        # Attest the effective enforcement posture onto this folder's chain before
        # its evidence — idempotent + process-memoised (a no-op after the first run
        # per folder+posture). Evidence only; a failure never blocks the run.
        try:
            from .enforcement_posture_binding import attest_posture
            attest_posture(folder_context, log_root=log_root)
        except Exception:                    # noqa: BLE001
            pass
    from .session_admission import verify_operation_session
    from .session_capability import CapabilityError
    try:
        verify_operation_session(
            folder_context,
            agent_id=agent_id,
            capability_token=capability_token,
            log_root=log_root,
        )
    except CapabilityError as exc:
        return _refuse(
            folder_context, use_case_id, agent_id,
            f"session admission refused: {exc}", journal, log_root,
        )

    uc = get_use_case(folder_context, use_case_id, log_root=log_root)
    if uc is None:
        return _refuse(folder_context, use_case_id, agent_id,
                       "unknown use case", journal, log_root)
    if not agent_permitted(folder_context, use_case_id, agent_id,
                           log_root=log_root):
        return _refuse(folder_context, use_case_id, agent_id,
                       "agent not permitted", journal, log_root)
    active, status_reason = _registered_agent_active(
        folder_context, agent_id, log_root
    )
    if not active:
        return _refuse(
            folder_context, use_case_id, agent_id,
            status_reason, journal, log_root,
        )

    # Prohibition is a HARD stop on the run-path (G1): an act the authored patch
    # marks `prohibit` is severed — no one may run it, person or agent, at ANY
    # autonomy grade. The graph already shows this act `prohibited`; the run-path
    # must enforce the same, or the display promises a boundary the engine breaks.
    # Checked BEFORE the cost cap so a prohibited act is refused unconditionally.
    if uc.get("prohibited"):
        return _refuse(folder_context, use_case_id, agent_id,
                       "prohibited — severed boundary, no one may run it",
                       journal, log_root)

    # Cost-cap enforcement: refuse the run once the folder's recorded
    # spend reaches its cap. Opt-in — no-op when the policy sets no cap. This
    # is the pre-run gate; spend itself is independently readable.
    #
    # Fail CLOSED on a budget: if the policy file is present but unreadable /
    # the cap value is invalid, we cannot trust the budget, so refuse rather
    # than run unbudgeted. verified_cost_cap reads ONLY the cap field, so an
    # unrelated malformed field (e.g. local_llm) never makes operate refuse.
    cap, verifiable = verified_cost_cap(folder_context)
    if not verifiable:
        return _refuse(folder_context, use_case_id, agent_id,
                       "policy unreadable — cannot verify cost cap", journal,
                       log_root)
    if cap is not None:
        spend = folder_spend_cents(folder_context, log_root=log_root,
                                   actor=agent_id or "system")
        if spend >= cap:
            return _refuse(folder_context, use_case_id, agent_id,
                           f"cost cap reached: {spend} >= {cap} cents",
                           journal, log_root)

    contract = uc.get("contract") or {}
    contract_id = uc.get("contract_id", "")
    # A well-formed contract always carries a grade; a missing one means we do NOT
    # know the earned autonomy, so it is UNGRADED (None), not "L0 granted". grade_meets
    # treats None as below every real threshold → human. Defaulting to 0 would also be
    # human while AUTO_GRADE_MIN >= 1, but None keeps the fail-closed reading explicit
    # and independent of the threshold's value (and reads as "ungraded" downstream).
    grade = contract.get("grade")
    window = contract.get("override_window_seconds", 0)
    on_timeout = contract.get("on_timeout", "halt")

    run_id = ""
    if journal:
        run_id = _journal(folder_context, agent_id, log_root, {
            "kind": "RunStarted", "use_case_id": use_case_id,
            "contract_id": contract_id, "agent_id": agent_id,
            "n_issues": len(issues)})

    steps: list[dict[str, Any]] = []
    awaiting = False
    # G1b/G2: reservations bind the run-path from what the user AUTHORED or INGESTED
    # (carried on the use_case record as reserved_acts), NOT from a baked-in legal
    # catalog. This is the same data governance_graph reads for its 'reserved'
    # verdict, so display and the run-path agree.
    authored_reserved = uc.get("reserved_acts") or []
    uc_risk = uc.get("risk", "low")
    # Tag data-lens: the activation's tags = connector-DERIVED (connectors linked to
    # this use case stamp their declared tags) ∪ user-AUTHORED (uc.tags) ∪ per-activation
    # (issue.tags). Neutral data-lineage facts the authored guards act on. A connector
    # read failure is additive-only and must not drop the run (fail toward the run, the
    # guards stay fail-closed downstream).
    lens_tags: set[str] = set(uc.get("tags") or [])
    try:
        for _c in list_connectors(folder_context, log_root=log_root):
            if use_case_id in (_c.get("use_cases") or []):
                lens_tags.update(_c.get("tags") or [])
    except (OSError, ValueError) as _e:
        # The connector STORE was unreadable (IO / parse) — NOT a programming bug
        # (AttributeError/TypeError surface, never masked). Record the gap on the chain
        # so an auditor can tell "no connectors linked" (0 tags) from "read failed"
        # (tags incomplete) — non-repudiation. Then proceed with the tags we do have;
        # a tag-guard simply can't be confirmed, so it stays unfired (no authority is
        # ever GRANTED by a missing tag — monotonic).
        if journal:
            _journal(folder_context, agent_id, log_root, {
                "kind": "RunWarning", "run_id": run_id, "use_case_id": use_case_id,
                "reason": "connector-tag read failed",
                "error": f"{type(_e).__name__}: {_e}"})
    for issue in issues:
        itype = issue.get("issue_type", "")
        # Option 2: the run-path now HONOURS the reservation's authored `when` guard,
        # using the loomground engine's OWN _guard_holds (one guard authority, no
        # reimplementation). Build the activation token; a reservation whose guard is
        # unsatisfied does NOT bind (a conditional reserve only reserves when it holds —
        # incl. `tags contains <tag>`). No guard ⇒ always binds (backward-compatible).
        tok = {"id": issue.get("issue_id", ""), "kind": use_case_id, "risk": uc_risk,
               "party": agent_id, "provenance": [],
               "tags": sorted(lens_tags | set(issue.get("tags") or []))}

        def _holds(a):
            return _guard_holds(a.get("when"), tok, uc_risk)
        # Which authored reservations bind THIS issue (loop fix A2): an issue-type
        # match if one exists; else the gate-level reservations (no trigger — they
        # reserve the whole act); else, defensively, all (fail toward oversight). A
        # triggered reservation for a DIFFERENT issue type must NOT be mis-applied here.
        matched = [a for a in authored_reserved if a.get("trigger") == itype and _holds(a)]
        gate_level = [a for a in authored_reserved if not a.get("trigger") and _holds(a)]
        reserved = matched or gate_level or [a for a in authored_reserved if _holds(a)]
        card = review_card(
            node_id=issue.get("issue_id", ""), stage="analysis",
            what=f"resolve {itype}", why="solver analysis",
            signals={"completeness": issue.get("completeness", "high")},
            reserved_act=reserved[0] if reserved else None)

        step: dict[str, Any] = {
            "issue_id": issue.get("issue_id", ""),
            "use_case_id": use_case_id, "contract_id": contract_id,
        }
        if card["status"] == "reserved":
            step["disposition"] = "reserved"
            step["reserved_to"] = reserved[0]["reserved_to"]
            step["basis"] = reserved[0].get("source", "")
            if len(reserved) > 1:
                # loop fix A1: every authored reservation on this act must fire —
                # don't silently drop co-reservers (separation-of-duty / multi-approver).
                step["reserved_to_all"] = [a.get("reserved_to") for a in reserved]
                step["bases_all"] = [a.get("source", "") for a in reserved]
            awaiting = True
        elif card["status"] == "needs-review":
            step["disposition"] = "human"
            step["deadline"] = now_epoch + window
            step["on_timeout"] = on_timeout
            awaiting = True
        elif grade_meets(grade, AUTO_GRADE_MIN):   # confident AND contract grants it
            # Option 2: the auto/human split is decided by the language's canonical
            # grade authority (engine-as-source-of-truth), not a hand-rolled compare —
            # the same `grade_meets` the loomground engine's step-(4) consults.
            step["disposition"] = "auto"
        else:                                # confident but autonomy not earned
            step["disposition"] = "human"
            step["deadline"] = now_epoch + window
            step["on_timeout"] = on_timeout
            step["reason"] = "contract grade below auto threshold"
            awaiting = True
        if journal:
            step["receipt"] = _journal(folder_context, agent_id, log_root, {
                "kind": "RunStep", "run_id": run_id,
                "use_case_id": use_case_id, "issue_id": step["issue_id"],
                "disposition": step["disposition"],
                "deadline": step.get("deadline"),
                "reserved_to": step.get("reserved_to")})
        steps.append(step)

    # loop fix A3: a reserved use case run with NO detected issues must not silently
    # complete — the act is reserved, so the run reserves (fail toward oversight)
    # rather than auto-completing past a sign-off the user authored. Only UNCONDITIONAL
    # reserves apply here: with no activation there is no token to test a `when` guard
    # against, so a guarded reserve cannot be evaluated and does not force-reserve.
    unconditional = [a for a in authored_reserved if not a.get("when")]
    if not steps and unconditional:
        a0 = unconditional[0]
        rstep: dict[str, Any] = {
            "issue_id": "", "use_case_id": use_case_id, "contract_id": contract_id,
            "disposition": "reserved", "reserved_to": a0.get("reserved_to"),
            "basis": a0.get("source", "")}
        if len(unconditional) > 1:
            rstep["reserved_to_all"] = [a.get("reserved_to") for a in unconditional]
        if journal:
            rstep["receipt"] = _journal(folder_context, agent_id, log_root, {
                "kind": "RunStep", "run_id": run_id, "use_case_id": use_case_id,
                "issue_id": "", "disposition": "reserved",
                "reserved_to": a0.get("reserved_to")})
        steps.append(rstep)
        awaiting = True

    final = "awaiting-human" if awaiting else "complete"
    if journal:
        _journal(folder_context, agent_id, log_root, {
            "kind": "RunOutcome", "run_id": run_id, "final": final})
    return {
        "use_case_id": use_case_id, "agent_id": agent_id,
        "contract_id": contract_id, "grade": grade, "run_id": run_id,
        "final": final, "steps": steps,
    }


def runs_for(
    folder_context: str,
    log_root: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Pure replay projection of journalled runs: one record per run_id with
    its steps and final state (a refused run is a single record). Chain order."""
    log = MutationLog(folder_context, log_root=log_root)
    runs: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for evt in log.replay():
        extra = evt.extra or {}
        kind = extra.get("kind")
        if kind == "RunStarted":
            rid = evt.audit_id
            runs[rid] = {"run_id": rid, "use_case_id": extra.get("use_case_id"),
                         "agent_id": extra.get("agent_id"), "steps": [],
                         "final": "running"}
            order.append(rid)
        elif kind == "RunRefused":
            rid = evt.audit_id
            runs[rid] = {"run_id": rid, "use_case_id": extra.get("use_case_id"),
                         "agent_id": extra.get("agent_id"), "steps": [],
                         "final": "refused", "reason": extra.get("reason")}
            order.append(rid)
        elif kind == "RunStep":
            rid = extra.get("run_id", "")
            if rid in runs:
                runs[rid]["steps"].append({
                    "issue_id": extra.get("issue_id"),
                    "disposition": extra.get("disposition"),
                    "deadline": extra.get("deadline"),
                    "reserved_to": extra.get("reserved_to"),
                    "receipt": evt.audit_id})
        elif kind == "RunOutcome":
            rid = extra.get("run_id", "")
            if rid in runs:
                runs[rid]["final"] = extra.get("final")
    return [runs[r] for r in order]


def transport_audit(folder_context: str, log_root: Optional[str] = None) -> dict[str, Any]:
    """Surface the transport/clock discipline: audit that
    every run on the chain is recorded with an external actor. This is an
    AUDIT, not enforcement — it derives `actor_present` from the agent_id each
    run carries (holds by construction, since `operate` requires an actor). It
    therefore cannot see *where* the actor's invocation originated: a timer that
    supplies an agent_id would still pass. Read-only replay over the chain."""
    rows: list[dict[str, Any]] = []
    no_actor = 0
    for r in runs_for(folder_context, log_root=log_root):
        actor = r.get("agent_id") or ""
        present = bool(actor)
        if not present:
            no_actor += 1
        rows.append({"run_id": r.get("run_id"), "use_case": r.get("use_case_id"),
                     "actor": actor, "final": r.get("final", ""),
                     "actor_present": present})
    return {"folder_context": folder_context, "runs": rows, "total": len(rows),
            "actor_present": len(rows) - no_actor,
            "missing_actor": no_actor,
            "invariant": "every run is recorded with an external actor",
            "basis": "audit, not enforcement; derived from actor presence per run "
                     "(holds by construction — operate requires an actor — and does "
                     "not detect a timer that supplies an actor)",
            "holds": no_actor == 0}


def resolve_timeout(step: dict[str, Any], *, now_epoch: int) -> str:
    """Apply the timed override when the clock elapses: 'pending' before the
    deadline, otherwise the contract's on_timeout ('proceed' on low risk,
    'halt' on higher). Silence resolves safely BY RISK — never silently the
    permissive way."""
    deadline = step.get("deadline")
    if deadline is None or now_epoch < deadline:
        return "pending"
    return step.get("on_timeout", "halt")
