# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Oversight orchestrator — the embedded-engine entry point.

The three USPs are built as separate decision layers. This module composes
them into the single call a workflow node makes: hand it one proposed action
plus the agent's current state, get back one verdict and, when a human is
needed, the transport-ready payload.

Pipeline, per action, in order:

    Breaker.effective_grade   ── the lease/tripwire-adjusted grade (USP 3)
        │                        a decayed lease or a tripped wire ⇒ L0
        ▼
    action_gate.gate          ── footprint × grade × standing approvals,
        │                        with telemetry (case 1) and aggregate caps
        │                        (case 6) (USP 1)
        ▼
    build_grounds_bundle      ── on CONDITIONAL: the Approve/Decide payload +
                                 doubt dossier when ADM/profiling/high-risk
                                 (Oversight ND OUT face)

The Lens (USP 2) governs learning, a different stream, and is NOT on the
per-action path — an action verdict never depends on what the agent has
learned, only on what it may do. Kept separate by design.

One pure function, no I/O. The caller writes the audit event and honours the
verdict (execute on GO, route the bundle on CONDITIONAL, refuse on NO-GO).
Scope honesty (taxonomy §6.5): preventive for workspace-gated runs, detective for
hosts that bypass the gateway — this function returns the verdict either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .action_gate import (
    ActionRequest, ApprovalUsage, GateDecision, Observables, StandingApproval,
    Verdict, gate)
from .breaker import Breaker, BreakerState, cap_grade
from .oversight_emit import GroundsBundle, build_grounds_bundle


@dataclass
class OversightOutcome:
    """The single object a workflow node reads back."""
    verdict: str                            # GO | CONDITIONAL | NO-GO
    effective_grade: str                    # grade the gate actually used
    breaker_state: str                      # RUNNING | DECAYED | QUARANTINED
    reason: str
    decision: GateDecision
    bundle: Optional[GroundsBundle] = None  # set when a human is needed
    breaker_reasons: list[str] = field(default_factory=list)

    @property
    def proceed(self) -> bool:
        """True iff the action may execute now with no human in the path."""
        return self.verdict == Verdict.GO.value

    @property
    def blocked(self) -> bool:
        return self.verdict == Verdict.NO_GO.value

    @property
    def needs_human(self) -> bool:
        return self.verdict == Verdict.CONDITIONAL.value

    def to_dict(self) -> dict[str, Any]:
        d = {
            "verdict": self.verdict,
            "effective_grade": self.effective_grade,
            "breaker_state": self.breaker_state,
            "reason": self.reason,
            "decision": self.decision.to_dict(),
            "breaker_reasons": self.breaker_reasons,
        }
        if self.bundle is not None:
            d["bundle"] = self.bundle.to_dict()
        return d


def assess(
    req: ActionRequest,
    *,
    breaker: Optional[Breaker] = None,
    standing_approvals: Iterable[StandingApproval] = (),
    prohibited_actions: Iterable[str] = (),
    posture: str = "balanced",
    observables: Optional[Observables] = None,
    approval_usage: Optional[dict[str, ApprovalUsage]] = None,
    grade_ceiling: str = "",
    metrics: Optional[dict[str, Any]] = None,
    now: Optional[float] = None,
    scope: str = "",
    grounds: Iterable[dict[str, Any]] = (),
    options: Iterable[dict[str, Any]] = (),
    link_target: str = "",
    dossier_material: Optional[dict[str, Any]] = None,
) -> OversightOutcome:
    """Run the full per-action oversight pipeline and return one outcome.

    The agent's requested grade (``req.autonomy_grade``) is *capped* by, in
    order: the Breaker's effective grade (a decayed/quarantined agent drops to
    L0) and any ``grade_ceiling`` from a compiled OversightFacet. The capped
    grade is what the gate sees — the request's own grade is never trusted
    above what the lease and the law allow.

    On CONDITIONAL, a grounds bundle is built (with a doubt dossier when the
    footprint/scope mandates one). On GO/NO-GO, ``bundle`` is None."""
    requested = req.autonomy_grade
    breaker_state = BreakerState.RUNNING.value
    breaker_reasons: list[str] = []

    effective = requested
    if breaker is not None:
        status = breaker.status(metrics=metrics, now=now)
        breaker_state = status.state.value
        breaker_reasons = list(status.reasons)
        effective = cap_grade(effective, status.effective_grade)
    if grade_ceiling:
        effective = cap_grade(effective, grade_ceiling)

    gated_req = ActionRequest(
        agent=req.agent, action_class=req.action_class,
        autonomy_grade=effective, footprint=req.footprint,
        folder=req.folder, affected_parties=req.affected_parties,
        magnitude=req.magnitude)

    decision = gate(
        gated_req, standing_approvals=standing_approvals,
        prohibited_actions=prohibited_actions, posture=posture,
        observables=observables, approval_usage=approval_usage)

    bundle: Optional[GroundsBundle] = None
    if decision.verdict is Verdict.CONDITIONAL:
        bundle = build_grounds_bundle(
            decision.audit_triple, grounds=list(grounds),
            options=list(options), link_target=link_target, scope=scope,
            dossier_material=dossier_material)

    reason = decision.reason
    if effective != requested:
        reason = (f"grade capped {requested}→{effective} "
                  f"({'breaker ' + breaker_state.lower() if breaker_state != 'RUNNING' else 'ceiling'}); "
                  f"{reason}")

    return OversightOutcome(
        verdict=decision.verdict.value,
        effective_grade=effective,
        breaker_state=breaker_state,
        reason=reason,
        decision=decision,
        bundle=bundle,
        breaker_reasons=breaker_reasons,
    )
