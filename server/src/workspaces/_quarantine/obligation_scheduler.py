# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The obligation scheduler — a deterministic, replayable ``tick``.

No background daemon. ``tick(as_of)`` is an explicit, auditable sweep — call
it from an MCP op, a cron line, or a test — that:

  1. resolves every open obligation's deadline (absolute, or relative against
     the contract's known event dates; unresolvable stays visible as
     ``deadline_unresolved`` — surfaced, never guessed);
  2. advances machine states by date arithmetic only:
     ``pending → due_soon`` (inside the warning window) ``→ due`` (deadline
     day) ``→ breached_candidate`` (deadline passed). The machine stops at
     breached_candidate — breach is a human judgment on the decision surface;
  3. proposes follow-up actions (reminder, escalation notice) and routes every
     one through :func:`workspaces.action_gate.gate` — nothing the scheduler wants
     to do bypasses the autonomy/footprint verdict.

Determinism and replay safety: state advancement is monotone and idempotent —
running ``tick`` twice at the same ``as_of`` produces no second transition and
no duplicate proposals (proposals are keyed by (obligation, target state)).
Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _pydate
from typing import Any, Iterable, Optional

from .action_gate import ActionRequest, GateDecision, StandingApproval, gate
from .contracts.instance import ContractRegistry
from .obligation_runtime import Obligation, ObligationRegistry
from workspaces.adapters.solver.temporal import Date, Duration

__all__ = ["SchedulerReport", "ObligationScheduler", "DEFAULT_WARNING_WINDOW"]

DEFAULT_WARNING_WINDOW = Duration.parse("P14D")


@dataclass
class Proposal:
    """One action the scheduler wants to take, with its gate verdict."""
    obligation_id: str
    action_class: str                      # remind-obligor | surface-breach-candidate
    target_state: str
    decision: GateDecision

    def to_dict(self) -> dict[str, Any]:
        return {"obligation_id": self.obligation_id, "action_class": self.action_class,
                "target_state": self.target_state, "decision": self.decision.to_dict()}


@dataclass
class SchedulerReport:
    as_of: str
    transitions: list[dict] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)   # obligations w/o resolvable deadline
    candidates: list[str] = field(default_factory=list)   # breach candidates after this tick

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of,
                "transitions": self.transitions,
                "proposals": [p.to_dict() for p in self.proposals],
                "unresolved": self.unresolved,
                "candidates": self.candidates}


def _target_state(deadline: Date, as_of: Date, window: Duration) -> str:
    """Pure date arithmetic → the state an open obligation should be in."""
    if as_of.as_date() > deadline.as_date():
        return "breached_candidate"
    if as_of.as_date() == deadline.as_date():
        return "due"
    warn_from = window.add_to(deadline, sign=-1)
    if as_of.as_date() >= warn_from.as_date():
        return "due_soon"
    return "pending"


_FORWARD = {"pending": 0, "due_soon": 1, "due": 2, "breached_candidate": 3}

# What the scheduler does at each arrival state. Reminders go OUT (to the
# obligor) → footprint "external-publish": without a standing approval the
# gate returns CONDITIONAL, so no message leaves silently. Surfacing a breach
# candidate is internal (decision-surface queue) → benign, GO at L1+.
_ACTION_FOR_STATE = {
    "due_soon": ("remind-obligor", ("external-publish",)),
    "due": ("remind-obligor", ("external-publish",)),
    "breached_candidate": ("surface-breach-candidate", ()),
}


class ObligationScheduler:
    """Sweeps one folder's obligations against the calendar."""

    def __init__(self, folder, *, log_root=None,
                 warning_window: Duration = DEFAULT_WARNING_WINDOW,
                 autonomy_grade: str = "L2",
                 standing_approvals: Iterable[StandingApproval] = (),
                 posture: str = "balanced",
                 deadline_shift=None):
        """``deadline_shift``: an OPTIONAL jurisdiction rule supplied by a
        pack (e.g. a weekend-extension rule using ``temporal.weekend_shift``).
        The substrate applies no jurisdiction's deadline doctrine by default —
        it only observes calendar facts ("deadline falls on a weekend") and
        flags; whether an extension rule applies is the governing law's call,
        configured, never assumed."""
        self.obligations = ObligationRegistry(folder, log_root=log_root)
        self.contracts = ContractRegistry(folder, log_root=log_root)
        self.window = warning_window
        self.grade = autonomy_grade
        self.standing = tuple(standing_approvals)
        self.posture = posture
        self.deadline_shift = deadline_shift

    # ── the tick ──────────────────────────────────────────────────────────────
    def tick(self, as_of: Optional[Date] = None) -> SchedulerReport:
        as_of = as_of or Date(_pydate.today().isoformat())
        report = SchedulerReport(as_of=as_of.iso)
        for ob in list(self._open()):
            contract = self._contract_for(ob)
            deadline = ob.resolved_deadline(contract)
            if deadline is None:
                report.unresolved.append(ob.obligation_id)
                continue
            # Jurisdiction-neutral by default: the substrate OBSERVES that a
            # deadline falls on a weekend (a calendar fact) and flags that
            # extension rules of the governing law may defer it; it APPLIES
            # such a rule only when one is configured (``deadline_shift``,
            # supplied by a jurisdiction pack). Public holidays are likewise
            # never resolved — the caveat travels with the transition.
            effective = (self.deadline_shift(deadline)
                         if self.deadline_shift else deadline)
            shifted = effective.iso != deadline.iso
            on_weekend = deadline.as_date().weekday() >= 5
            target = _target_state(effective, as_of, self.window)
            if _FORWARD.get(target, 0) > _FORWARD.get(ob.state, 99):
                reason = f"as_of {as_of.iso} vs deadline {deadline.iso}"
                caveat = ""
                if shifted:
                    reason += f" → effective {effective.iso} (configured deadline-shift rule)"
                elif on_weekend and target in ("due", "breached_candidate"):
                    caveat = ("deadline falls on a weekend — extension rules "
                              "of the governing law may defer it; verify "
                              "before acting")
                    reason += f" ({caveat})"
                if target in ("due", "breached_candidate"):
                    caveat = (caveat + "; " if caveat else "") + \
                        "public holidays not checked"
                rec = self.obligations.advance(ob.obligation_id, target,
                                               reason=reason)
                report.transitions.append(
                    {"obligation_id": ob.obligation_id, "from": ob.state,
                     "to": target, "deadline": deadline.iso,
                     "effective_deadline": effective.iso,
                     "weekend_deadline": on_weekend,
                     "shift_rule_applied": shifted,
                     "caveat": caveat})
                self._propose(report, ob.obligation_id, target)
        report.candidates = [o.obligation_id
                             for o in self.obligations.in_state("breached_candidate")]
        return report

    # ── internals ─────────────────────────────────────────────────────────────
    def _open(self) -> Iterable[Obligation]:
        for state in ("pending", "due_soon", "due"):
            yield from self.obligations.in_state(state)

    def _contract_for(self, ob: Obligation):
        cid, _, ver = ob.contract_ref.partition("@")
        try:
            return self.contracts.get(cid, int(ver)) if ver else self.contracts.get(cid)
        except Exception:                                       # noqa: BLE001
            return None

    def _propose(self, report: SchedulerReport, oid: str, state: str) -> None:
        spec = _ACTION_FOR_STATE.get(state)
        if spec is None:
            return
        action_class, footprint = spec
        # C2 (Art. 50): a reminder going OUT to the obligor names the obligor
        # as the affected party — the disclosure has an addressee by
        # construction. Surfacing a breach candidate is internal (no parties).
        ob = next((o for o in self.obligations.in_state(state)
                   if o.obligation_id == oid), None)
        affected = ((ob.obligor_role or "obligor",)
                    if "external-publish" in footprint and ob else ())
        decision = gate(
            ActionRequest(agent="obligation-scheduler", action_class=action_class,
                          autonomy_grade=self.grade, footprint=tuple(footprint),
                          affected_parties=affected),
            standing_approvals=self.standing, posture=self.posture)
        report.proposals.append(Proposal(
            obligation_id=oid, action_class=action_class,
            target_state=state, decision=decision))
