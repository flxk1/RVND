# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Action-gating verdict substrate.

The assessment graph and the runtime gate share one substrate (concept §7):
the obligation pairs an auditor reads are the same pairs the gate queries when
an agent proposes an action. The reframe makes "regulatory-secure by
construction" honest — nothing fires that was not gated against the same
graph the report renders from.

Flow:

    action proposed
        │
        ▼
    fast-path (deterministic, no LLM):
        action-shape (footprint tags) × autonomy grade × standing approvals
        │
        ├─ benign + grade allows            → GO     (minimal audit triple)
        ├─ covered by a standing approval    → GO     (cites the obligation pair)
        └─ flagged                           → deeper verdict:
                                               GO / CONDITIONAL / NO-GO

Three skills converge here, as the concept states: ``governance-by-design``
sets the default approval breadth (posture), ``autonomy-grades`` scales the
threshold (L0–L6), and the obligation graph supplies what a standing approval
is *approved under*. A **standing approval is an edge**
``(agent, action_class) approved-under (obligation_pair) until (date)``; a
**promotion gate is a graph query** over the agent's verdict history.

Every verdict emits a hash-chain-ready audit triple
``(agent, verdict, action_class)`` with its reason + provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from enum import Enum
from typing import Any, Iterable, Optional


class Verdict(str, Enum):
    GO = "GO"
    CONDITIONAL = "CONDITIONAL"   # may proceed only with human sign-off
    NO_GO = "NO-GO"


# Autonomy grades on the 0–6 level-of-automation ladder (grounded in ISO/IEC
# 22989:2022 §5.13; the ladder itself lives in governance's grammar, not here):
# L0 operator-controlled (a human approves every step) → L3 standby-conditional
# (a human on standby) → L5 full automation (the former "silent publish") → L6
# self-governing, which RVND never grants (the always-refused ceiling).
from .adapters.policy_languages import grade_index as _grade_index

_GRADE = _grade_index()  # grade lattice consumed from governance's grammar

# Footprint tags that flag an action out of the fast path, with the minimum
# autonomy grade at which they may proceed at all (below it → NO-GO). Values are
# RANK INDICES on the active ladder (grade_index). The ladder grew to the 0–6
# ISO/IEC 22989:2022 §5.13 scale, but these indices are UNCHANGED — each index's
# meaning is preserved: 2 = L2 "partial", 3 = L3 "standby-conditional" (a human on
# standby). High-stakes footprints require L3, which IS the sign-off level: at L3
# a flagged action proceeds only as CONDITIONAL (human sign-off), below it → NO-GO.
# So behaviour is identical to the former 0–4 ladder for every existing grade
# (non-loosening), and self-governing L6 is refused outright above.
_RISK_MIN_GRADE = {
    "personal-data": 2,
    "financial": 3,
    "irreversible": 3,
    "external-publish": 3,
    "security-control": 2,
}

# Governance-by-design posture shifts the grade threshold: a restrictive
# posture demands one grade higher; permissive allows one lower (floored at the
# risk minimum's intent — permissive never lets a sub-minimum grade through).
_POSTURE_SHIFT = {"restrictive": +1, "balanced": 0, "permissive": -1}

# Verdict severity order for the NT-13 monotonicity invariant: telemetry may
# move a verdict RIGHT along this order, never left.
_SEVERITY = {Verdict.GO: 0, Verdict.CONDITIONAL: 1, Verdict.NO_GO: 2}

# Affected-party count at or above which a flagged action escalates to
# CONDITIONAL even under a standing approval (Art. 14 commensurability:
# breadth of effect is a runtime observable, not an ontology property).
AFFECTED_PARTY_THRESHOLD = 10


@dataclass
class ActionRequest:
    agent: str
    action_class: str
    autonomy_grade: str = "L1"            # L0..L4
    footprint: tuple[str, ...] = ()        # risk tags
    folder: str = ""
    # C2 (Art. 50): natural persons the action's external effect touches.
    # Required when the footprint carries ``external-publish`` — an output
    # bound for a third party with no named recipient cannot be disclosed.
    affected_parties: tuple[str, ...] = ()
    # Aggregate-cap substrate (stress-test case 6): the instance's size in
    # the approval's unit (EUR, rows, recipients), consumed against a
    # StandingApproval.max_total. None = unmeasured.
    magnitude: Optional[float] = None


@dataclass(frozen=True)
class Observables:
    """Runtime telemetry for one action instance (NT-13 substrate).

    The automation boundary cannot be a static, system-wide configuration;
    it must reflect per-instance runtime observables alongside the action's
    ontology-inherited footprint. All fields optional: an absent observable
    contributes nothing (backwards compatible — ``gate()`` without
    observables behaves exactly as before).

    **Monotonicity invariant (NT-13):** observables may only RAISE the
    verdict severity (GO → CONDITIONAL), never lower it, and never past
    CONDITIONAL — NO-GO stays reserved for structural rules (prohibition,
    under-grade). Telemetry adds oversight; it cannot remove it.
    """
    confidence: Optional[float] = None          # extractor/model confidence 0..1
    authority_tier: Optional[int] = None        # 1 primary-law .. 5 general
    novelty: Optional[int] = None               # prior occurrences of (agent, action_class); 0 = first ever
    affected_party_count: Optional[int] = None  # natural persons touched by this instance

    def triggers(self, flagged: bool) -> list[str]:
        """Names of escalation triggers that fire for this instance."""
        out: list[str] = []
        if self.confidence is not None and self.confidence < _CONFIDENCE_FLOOR:
            out.append(f"confidence {self.confidence} < floor {_CONFIDENCE_FLOOR}")
        if flagged and self.authority_tier is not None and self.authority_tier >= 4:
            out.append(f"authority tier {self.authority_tier} (secondary/general) on flagged action")
        if flagged and self.novelty == 0:
            out.append("first occurrence of flagged (agent, action_class)")
        if (self.affected_party_count is not None
                and self.affected_party_count >= AFFECTED_PARTY_THRESHOLD):
            out.append(f"affected_party_count {self.affected_party_count} ≥ {AFFECTED_PARTY_THRESHOLD}")
        return out


def _confidence_floor() -> float:
    """Single source of truth: the suite-wide floor from norm_contract."""
    from .norm_contract import CONFIDENCE_FLOOR
    return CONFIDENCE_FLOOR


_CONFIDENCE_FLOOR = _confidence_floor()


@dataclass
class StandingApproval:
    """An edge: (agent, action_class) approved-under (obligation_pair) until date.

    ``until`` is validated at construction (NT-11 typed-date-at-write): a
    malformed expiry is rejected when the approval is *created*, not silently
    treated as expired when it is *checked* — a standing approval whose expiry
    nobody can read is a governance hole, not a default."""
    agent: str
    action_class: str
    obligation_pair: str
    until: Optional[str] = None            # validated ISO date; None = no expiry
    # Aggregate caps (stress-test case 6): an uncapped standing approval is
    # an unbounded amplifier — per-instance predicates pass 101 × €99 under
    # a "< €100" rule; the aggregate caps catch the stream.
    max_uses: Optional[int] = None         # invocations the approval covers
    max_total: Optional[float] = None      # summed req.magnitude it covers

    def __post_init__(self) -> None:
        if self.until is not None:
            from rvnd.adapters.solver.temporal import Date, TemporalError
            try:
                Date(self.until)
            except TemporalError as exc:
                raise ValueError(
                    f"StandingApproval.until must be a valid ISO date "
                    f"(NT-11): {self.until!r}") from exc
        if self.max_uses is not None and self.max_uses < 1:
            raise ValueError(
                f"StandingApproval.max_uses must be ≥ 1: {self.max_uses!r}")
        if self.max_total is not None and self.max_total <= 0:
            raise ValueError(
                f"StandingApproval.max_total must be > 0: {self.max_total!r}")

    def covers(self, req: "ActionRequest", as_of: date) -> bool:
        if self.agent != req.agent or self.action_class != req.action_class:
            return False
        if self.until is not None and date.fromisoformat(self.until) < as_of:
            return False
        return True

    def cap_blocks(self, req: "ActionRequest",
                   usage: Optional["ApprovalUsage"]) -> Optional[str]:
        """Why the aggregate caps refuse this instance, or None.

        Conservative by default: a capped approval never covers an
        *unmeasured* instance (``req.magnitude is None`` while ``max_total``
        is set) — what cannot be counted cannot consume a counted budget."""
        used = usage or ApprovalUsage()
        if self.max_uses is not None and used.uses + 1 > self.max_uses:
            return f"uses cap: {used.uses} used of {self.max_uses}"
        if self.max_total is not None:
            if req.magnitude is None:
                return ("magnitude cap set but instance unmeasured — "
                        "unmeasured actions cannot consume a counted budget")
            if used.total + req.magnitude > self.max_total:
                return (f"total cap: {used.total} + {req.magnitude} "
                        f"> {self.max_total}")
        return None

    def to_edge(self) -> dict[str, Any]:
        return {"subject": f"{self.agent}/{self.action_class}",
                "predicate": "approved-under", "object": self.obligation_pair,
                "dimension": "intentional", "until": self.until,
                "max_uses": self.max_uses, "max_total": self.max_total}


@dataclass
class ApprovalUsage:
    """Replayed consumption of one standing approval (keyed by
    ``obligation_pair``). Built from verdict history — never hand-set —
    so the cap check is a projection of the log like every other read."""
    uses: int = 0
    total: float = 0.0


def usage_from_history(
    history: Iterable[dict[str, Any]],
) -> dict[str, ApprovalUsage]:
    """Replay audit triples into per-approval usage counters.

    Counts every GO that cites a standing approval; sums the instance
    magnitudes where recorded. Deterministic: same history ⇒ same counters
    (G-series replay discipline)."""
    out: dict[str, ApprovalUsage] = {}
    for h in history:
        if h.get("predicate") != Verdict.GO.value:
            continue
        if h.get("reason") != "standing-approval":
            continue
        mag = h.get("magnitude")
        for pair in h.get("obligation_pairs", []):
            u = out.setdefault(pair, ApprovalUsage())
            u.uses += 1
            if isinstance(mag, (int, float)):
                u.total += float(mag)
    return out


@dataclass
class GateDecision:
    verdict: Verdict
    fast_path: bool
    reason: str
    audit_triple: dict[str, Any] = field(default_factory=dict)
    obligation_pairs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


def _triple(req: ActionRequest, verdict: Verdict, reason: str,
            pairs: list[str]) -> dict[str, Any]:
    return {"subject": req.agent, "predicate": verdict.value,
            "object": req.action_class, "reason": reason,
            "obligation_pairs": pairs, "footprint": list(req.footprint),
            "autonomy_grade": req.autonomy_grade,
            "magnitude": req.magnitude}


def _apply_telemetry(decision: GateDecision, req: ActionRequest,
                     obs: Optional[Observables]) -> GateDecision:
    """Monotone escalation from runtime observables (NT-13).

    GO may become CONDITIONAL when a trigger fires — even over a standing
    approval, because an approval covers the action *class* while telemetry
    speaks about this *instance*. CONDITIONAL and NO-GO are never changed.
    The original verdict is preserved in the audit triple so the escalation
    is itself reconstructible.
    """
    if obs is None or decision.verdict is not Verdict.GO:
        if obs is not None:
            decision.audit_triple["telemetry"] = {
                "observables": asdict(obs), "escalated": False}
        return decision
    flagged = any(t in _RISK_MIN_GRADE for t in req.footprint)
    fired = obs.triggers(flagged)
    if not fired:
        decision.audit_triple["telemetry"] = {
            "observables": asdict(obs), "escalated": False}
        return decision
    reason = (f"telemetry escalation (NT-13): {'; '.join(fired)} "
              f"— was {decision.verdict.value}: {decision.reason}")
    triple = _triple(req, Verdict.CONDITIONAL, "telemetry-escalation",
                     decision.obligation_pairs)
    triple["telemetry"] = {"observables": asdict(obs), "escalated": True,
                           "triggers": fired,
                           "verdict_before": decision.verdict.value}
    return GateDecision(Verdict.CONDITIONAL, False, reason, triple,
                        obligation_pairs=decision.obligation_pairs)


def check_telemetry_monotonicity(before: GateDecision,
                                 after: GateDecision) -> bool:
    """NT-13 checker: the post-telemetry verdict is never less severe than
    the pre-telemetry verdict. Exported for property tests and audit."""
    return _SEVERITY[after.verdict] >= _SEVERITY[before.verdict]


# Officer oversight — a policy-programmed officer that OVERSEES this gate can only TIGHTEN the
# verdict, never relax it (monotone, like telemetry / NT-13). block → NO-GO; any human-control
# form → CONDITIONAL (sign-off); auto → no change. The escalation party rides in the audit triple
# so the runtime routes the reserved act via the existing oversight_dispatch / parties stack.
# Officers are duck-typed (``.governs`` / ``.control_form`` / ``.escalation_party`` /
# ``.officer_id``) so action_gate stays decoupled from the officer module.
_OFFICER_FORM_VERDICT = {
    "block": Verdict.NO_GO,
    "two_approvers": Verdict.CONDITIONAL, "competent": Verdict.CONDITIONAL,
    "single_approver": Verdict.CONDITIONAL, "notify": Verdict.CONDITIONAL,
}


def _apply_officers(decision: GateDecision, req: ActionRequest, officers) -> GateDecision:
    overseeing = [o for o in officers if getattr(o, "governs", lambda t: False)(req.action_class)]
    if not overseeing:
        return decision
    target = decision.verdict
    for o in overseeing:
        want = _OFFICER_FORM_VERDICT.get(getattr(o, "control_form", "auto"), Verdict.GO)
        if _SEVERITY[want] > _SEVERITY[target]:
            target = want
    who = [getattr(o, "officer_id", "officer") for o in overseeing]
    if _SEVERITY[target] <= _SEVERITY[decision.verdict]:
        decision.audit_triple["officers"] = {"overseen_by": who, "escalated": False}
        return decision
    party = next((getattr(o, "escalation_party", "") for o in overseeing
                  if getattr(o, "escalation_party", "")), "")
    reason = (f"officer oversight: {', '.join(who)} require {target.value} "
              f"— was {decision.verdict.value}: {decision.reason}")
    triple = _triple(req, target, "officer-oversight", decision.obligation_pairs)
    triple["officers"] = {"overseen_by": who, "escalated": True,
                          "verdict_before": decision.verdict.value, "escalation_party": party}
    return GateDecision(target, False, reason, triple, obligation_pairs=decision.obligation_pairs)


def gate(
    req: ActionRequest,
    *,
    standing_approvals: Iterable[StandingApproval] = (),
    prohibited_actions: Iterable[str] = (),
    posture: str = "balanced",
    as_of: Optional[date] = None,
    observables: Optional[Observables] = None,
    approval_usage: Optional[dict[str, ApprovalUsage]] = None,
    officers: Iterable = (),
) -> GateDecision:
    """Return the verdict for one proposed action.

    Deterministic and LLM-free — this is the fast-path/verdict logic; richer
    expert review (when CONDITIONAL) happens above this layer.

    ``observables`` (optional) carries per-instance runtime telemetry; it can
    only escalate the verdict (NT-13 monotonicity), never relax it.

    ``approval_usage`` (optional) carries replayed consumption counters for
    capped standing approvals (build with :func:`usage_from_history`); a
    capped-out approval stops covering, and the action falls back to
    sign-off — monotone, like telemetry.

    ``officers`` (optional) are policy-programmed oversight bindings that govern
    this gate; each can only TIGHTEN the verdict (never relax) and names the
    human escalation party in the audit triple — monotone, like telemetry."""
    base = _gate_base(req, standing_approvals=standing_approvals,
                      prohibited_actions=prohibited_actions,
                      posture=posture, as_of=as_of,
                      approval_usage=approval_usage)
    decided = _apply_telemetry(base, req, observables)
    return _apply_officers(decided, req, officers)


def _gate_base(
    req: ActionRequest,
    *,
    standing_approvals: Iterable[StandingApproval] = (),
    prohibited_actions: Iterable[str] = (),
    posture: str = "balanced",
    as_of: Optional[date] = None,
    approval_usage: Optional[dict[str, ApprovalUsage]] = None,
) -> GateDecision:
    as_of = as_of or date.today()
    grade = _GRADE.get(req.autonomy_grade, 1)
    shift = _POSTURE_SHIFT.get(posture, 0)

    # Self-governing autonomy — ISO/IEC 22989:2022 §5.13 level 6, an agent that
    # could alter its own goals or domain without oversight — is categorically
    # never permitted: RVND's enforcement thesis is that it does not grant
    # self-governance. An actor presenting the ceiling grade is refused
    # regardless of the action (the taxonomy names it; the gate forbids it).
    if req.autonomy_grade == "L6":
        return GateDecision(
            Verdict.NO_GO, False,
            "self-governing autonomy (L6) is never permitted",
            _triple(req, Verdict.NO_GO, "self-governing-refused", []))

    # Hard prohibition always wins.
    if req.action_class in set(prohibited_actions):
        return GateDecision(Verdict.NO_GO, False,
                            f"action_class {req.action_class!r} is prohibited",
                            _triple(req, Verdict.NO_GO, "prohibited", []))

    # C2 (Art. 50): an external-publish action must name whom it affects.
    # A disclosure obligation that cannot identify a recipient cannot be met,
    # so the action does not fire — structural, like prohibition.
    if "external-publish" in req.footprint and not any(
            str(p).strip() for p in req.affected_parties):
        return GateDecision(
            Verdict.NO_GO, False,
            "external-publish action names no affected_parties — Art. 50 "
            "disclosure cannot be satisfied",
            _triple(req, Verdict.NO_GO, "art50-no-affected-parties", []))

    risk_tags = [t for t in req.footprint if t in _RISK_MIN_GRADE]

    # Fast path: no RECOGNISED risk tag. But footprint can carry tags the risk
    # ontology (_RISK_MIN_GRADE) never classified — that is UNREGULATED, not benign.
    # Riding the fast path silently would make a policy hole invisible AND fail open
    # (an unclassified risk permitted as benign). Mark it distinctly — the verdict is
    # UNCHANGED, so this only SURFACES the hole for policy review, never re-gates it —
    # so an unregulated action is visible and countable, not laundered as benign.
    # (An empty footprint is genuinely benign and keeps the "benign" code.)
    if not risk_tags:
        unregulated = [t for t in req.footprint if t not in _RISK_MIN_GRADE]
        if grade >= 1:
            reason = ("benign action; grade permits" if not unregulated else
                      f"unregulated footprint {unregulated} — no rule in the risk "
                      "ontology; permitted as benign, flagged for policy review")
            triple = _triple(req, Verdict.GO,
                             "unregulated" if unregulated else "benign", [])
            if unregulated:
                triple["unregulated"] = unregulated
            return GateDecision(Verdict.GO, True, reason, triple)
        # L0 = interactive: even benign needs a human.
        triple = _triple(req, Verdict.CONDITIONAL, "L0-interactive", [])
        if unregulated:
            triple["unregulated"] = unregulated
        return GateDecision(Verdict.CONDITIONAL, True,
                            "L0 interactive: human approves every step", triple)

    # A frozen agent gets no pre-authorised autonomy. At L0 (interactive —
    # set by a Breaker quarantine/decay, or an L0 grant) a standing approval
    # does NOT short-circuit: a quarantine a pre-authorisation could bypass
    # would not be a quarantine. The flagged action falls through to the grade
    # check, which refuses it (grade 0 < any risk minimum → NO-GO).
    # (Surfaced by examples/oversight_demo.py — a quarantined agent was riding
    # its standing approval to GO.)
    usage = approval_usage or {}
    covering = ([s for s in standing_approvals if s.covers(req, as_of)]
                if grade >= 1 else [])
    if grade < 1 and standing_approvals:
        # Make the bypass visible in the reason rather than silently dropping.
        capped_note = " (standing approvals suspended at L0)"
    else:
        capped_note = ""
    capped_reasons: list[str] = []
    usable: list[StandingApproval] = []
    for s in covering:
        block = s.cap_blocks(req, usage.get(s.obligation_pair))
        if block is None:
            usable.append(s)
        else:
            capped_reasons.append(f"{s.obligation_pair}: {block}")
    if usable:
        pairs = [s.obligation_pair for s in usable]
        return GateDecision(Verdict.GO, True,
                            "covered by standing approval",
                            _triple(req, Verdict.GO, "standing-approval", pairs),
                            obligation_pairs=pairs)

    # No (usable) standing approval: decide on grade vs. the highest risk
    # minimum.
    required = max(_RISK_MIN_GRADE[t] for t in risk_tags) + shift
    worst_tag = max(risk_tags, key=lambda t: _RISK_MIN_GRADE[t])
    if grade < required:
        return GateDecision(
            Verdict.NO_GO, False,
            f"grade {req.autonomy_grade} below required for {worst_tag!r} "
            f"(needs grade ≥ {required} under {posture} posture){capped_note}",
            _triple(req, Verdict.NO_GO, f"under-grade:{worst_tag}", []))

    if capped_reasons:
        triple = _triple(req, Verdict.CONDITIONAL,
                         f"standing-approval-cap-exhausted:{worst_tag}", [])
        triple["cap_exhausted"] = capped_reasons
        return GateDecision(
            Verdict.CONDITIONAL, False,
            f"standing approval cap exhausted ({'; '.join(capped_reasons)}); "
            f"requires sign-off",
            triple)

    # Grade is sufficient, but a flagged action without a standing approval
    # never fires silently — it needs human sign-off.
    return GateDecision(
        Verdict.CONDITIONAL, False,
        f"flagged ({worst_tag!r}); grade permits but requires sign-off",
        _triple(req, Verdict.CONDITIONAL, f"needs-signoff:{worst_tag}", []))


# --- promotion gate = a graph query over verdict history -------------------

def open_criticals(history: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open Critical = a NO-GO, or a CONDITIONAL not yet signed off."""
    out = []
    for h in history:
        v = h.get("verdict")
        if v == Verdict.NO_GO.value:
            out.append(h)
        elif v == Verdict.CONDITIONAL.value and not h.get("signed_off", False):
            out.append(h)
    return out


def promotion_gate(
    agent: str, from_grade: str, to_grade: str,
    history: Iterable[dict[str, Any]],
) -> GateDecision:
    """Allow autonomy promotion only when the agent has no open Critical in its
    history — a graph query, not a re-assessment."""
    hist = [h for h in history if h.get("subject") == agent]
    blockers = open_criticals(hist)
    req = ActionRequest(agent=agent, action_class=f"promote:{from_grade}->{to_grade}",
                        autonomy_grade=to_grade)
    if _GRADE.get(to_grade, 0) <= _GRADE.get(from_grade, 0):
        return GateDecision(Verdict.NO_GO, False, "not a promotion",
                            _triple(req, Verdict.NO_GO, "not-a-promotion", []))
    if blockers:
        ids = [b.get("object") for b in blockers]
        return GateDecision(
            Verdict.NO_GO, False,
            f"{len(blockers)} open Critical(s) in history: {ids}",
            _triple(req, Verdict.NO_GO, "open-criticals", []),
            obligation_pairs=[])
    return GateDecision(Verdict.GO, False, "no open Criticals; promotion clear",
                        _triple(req, Verdict.GO, "promotion-clear", []))
