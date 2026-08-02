# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The Breaker — interdiction at machine tempo (USP 3).

In high-frequency settings no human can press the stop button in time. The
Breaker inverts the problem: **stopping is the default state; running is what
requires continuous permission.** The human does not stop the agent — the
human's *absence* stops the agent (taxonomy §6).

Three mechanisms, pure-stdlib, deterministic (injectable clock):

  * :class:`Lease` — the dead-man's switch. Autonomy is leased, never granted
    permanently. Renewal is conditional on green checks. No renewal → the
    effective grade decays to L0 automatically. (§6.3; the architectural
    reading of AI Act Art. 14(4)(d): the capacity "to decide not to use" is,
    at machine tempo, only satisfiable as architecture.)
  * :class:`Tripwire` — pre-armed conditions that trip without a human in the
    loop (budget, rate, anomaly, attestation, chain-verification). (§6.2)
  * :class:`Breaker` — composes leases + tripwires into one
    :func:`Breaker.status` verdict per agent: RUNNING / DECAYED / QUARANTINED,
    with the effective grade the gate must use. A trip → QUARANTINE: the
    effective grade is L0 and the gate refuses everything above interactive.

The Breaker decides; it does not execute. Like the rest of the substrate it is
detective-where-it-cannot-prevent: it returns a state and a reason; the runtime
(refuse tokens, freeze the queue) and the gate (read the effective grade)
enforce it. Scope honesty (§6.5): preventive for workspace-gated runs, detective for
hosts that bypass the gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Iterable, Optional


_GRADES = ("L0", "L1", "L2", "L3", "L4")
_GRADE_IDX = {g: i for i, g in enumerate(_GRADES)}


class BreakerState(str, Enum):
    RUNNING = "RUNNING"          # lease live, no tripwire armed-and-tripped
    DECAYED = "DECAYED"          # lease lapsed → grade fell to L0, recoverable by renew
    QUARANTINED = "QUARANTINED"  # a tripwire tripped → frozen until human clears


def _clock() -> float:
    import time
    return time.time()


@dataclass
class Lease:
    """A time-boxed grant of autonomy. ``granted_grade`` is what the agent may
    use *while the lease is live*; once ``expires_at`` passes the effective
    grade decays to L0 until a renewal moves ``expires_at`` forward.

    The lease is content for the log: every grant/renew is an event, so the
    autonomy an agent held at any past instant is reconstructible by replay."""
    agent: str
    granted_grade: str
    expires_at: float                       # unix seconds
    ttl_seconds: float = 60.0               # default renewal length
    granted_at: float = field(default_factory=_clock)

    def __post_init__(self) -> None:
        if self.granted_grade not in _GRADE_IDX:
            raise ValueError(f"unknown grade: {self.granted_grade!r}")
        if self.ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be > 0: {self.ttl_seconds!r}")

    def live(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else _clock()) < self.expires_at

    def effective_grade(self, now: Optional[float] = None) -> str:
        """The grade the gate must use: the grant while live, L0 once lapsed.
        Decay is automatic — no human action, no daemon: a stale lease simply
        reads as L0 the next time anyone asks."""
        return self.granted_grade if self.live(now) else "L0"

    def renew(self, *, ok: bool, reason: str = "",
              now: Optional[float] = None,
              ttl_seconds: Optional[float] = None) -> "RenewResult":
        """Conditional renewal. ``ok`` is the caller's green-checks verdict
        (budget/attestation/queue-health/drift, computed elsewhere). A renewal
        with ``ok=False`` is *refused* — the lease keeps its old expiry and is
        allowed to lapse. Renewal never resurrects a quarantine (that needs a
        human clear); it only extends a still-valid or recently-lapsed grant."""
        now = now if now is not None else _clock()
        if not ok:
            return RenewResult(False, self.effective_grade(now),
                               f"renewal refused: {reason or 'green checks failed'}")
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        self.expires_at = now + ttl
        self.ttl_seconds = ttl
        return RenewResult(True, self.granted_grade,
                           f"renewed for {ttl}s")


@dataclass
class RenewResult:
    renewed: bool
    effective_grade: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Tripwire:
    """A pre-armed interdiction condition. ``metric`` names the observable;
    ``limit`` the threshold; ``kind`` how to compare. A tripwire that trips
    sends the agent to QUARANTINE — frozen, not merely throttled."""
    name: str
    metric: str
    limit: float
    kind: str = "max"                       # "max" | "min" | "flag"

    def trips(self, value: Optional[float | bool]) -> bool:
        if value is None:
            return False                    # unmeasured ≠ tripped (it's a gap; see note)
        if self.kind == "flag":
            return bool(value)
        if self.kind == "min":
            return float(value) < self.limit
        return float(value) > self.limit    # "max"

    def evaluate(self, value: Optional[float | bool]) -> Optional[str]:
        if self.trips(value):
            return (f"tripwire {self.name!r}: {self.metric}={value} "
                    f"{'is set' if self.kind=='flag' else self.kind+' '+str(self.limit)}")
        return None


# Conservative default tripwires (autonomy-grades budget cap + the integrity
# checks that must never be ignored). Override per agent via the registry.
def default_tripwires() -> list[Tripwire]:
    return [
        Tripwire("budget", "usd_spent_iteration", 0.0, "max"),     # set real cap per agent
        Tripwire("error_rate", "error_rate", 0.25, "max"),
        Tripwire("attestation", "attestation_failed", 0.0, "flag"),
        Tripwire("chain_integrity", "chain_invalid", 0.0, "flag"),
    ]


@dataclass
class BreakerStatus:
    agent: str
    state: BreakerState
    effective_grade: str
    reasons: list[str] = field(default_factory=list)
    tripped: list[str] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.state is BreakerState.RUNNING

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


class Breaker:
    """Composes a lease and tripwires into one per-agent verdict.

    ``metrics`` is the live observable bag (``{metric_name: value}``). The
    integrity tripwires (attestation, chain) are flags; a True value freezes.
    Quarantine is sticky in the sense that it must be cleared by a human
    (``clear``) — a renewal alone cannot lift it (separation of duties)."""

    def __init__(self, lease: Lease,
                 tripwires: Optional[Iterable[Tripwire]] = None) -> None:
        self.lease = lease
        self.tripwires = list(tripwires if tripwires is not None
                              else default_tripwires())
        self._quarantined_reason: Optional[str] = None

    def trip_check(self, metrics: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for tw in self.tripwires:
            msg = tw.evaluate(metrics.get(tw.metric))
            if msg:
                out.append(msg)
        return out

    def status(self, *, metrics: Optional[dict[str, Any]] = None,
               now: Optional[float] = None) -> BreakerStatus:
        metrics = metrics or {}
        # A previously-recorded quarantine stays until cleared.
        tripped = self.trip_check(metrics)
        if self._quarantined_reason:
            tripped = [self._quarantined_reason] + tripped
        if tripped:
            self._quarantined_reason = tripped[0]
            return BreakerStatus(
                self.lease.agent, BreakerState.QUARANTINED, "L0",
                reasons=["quarantined — frozen until a human clears"],
                tripped=tripped)
        if not self.lease.live(now):
            return BreakerStatus(
                self.lease.agent, BreakerState.DECAYED, "L0",
                reasons=["lease lapsed — autonomy decayed to L0; renew to restore"])
        return BreakerStatus(
            self.lease.agent, BreakerState.RUNNING,
            self.lease.effective_grade(now),
            reasons=[f"lease live; grade {self.lease.granted_grade}"])

    def renew(self, *, ok: bool, reason: str = "",
              now: Optional[float] = None,
              ttl_seconds: Optional[float] = None) -> RenewResult:
        """Renew the lease. Refused (``ok=False``) or while quarantined → no
        extension. A quarantine cannot be renewed away — it needs ``clear``."""
        if self._quarantined_reason:
            return RenewResult(False, "L0",
                               "agent is quarantined — renewal cannot lift a "
                               "tripped breaker; a human must clear it")
        return self.lease.renew(ok=ok, reason=reason, now=now,
                                ttl_seconds=ttl_seconds)

    def clear(self, *, by: str, rationale: str) -> dict[str, Any]:
        """Human clears a quarantine. Requires a named actor and a rationale —
        origination, not a silent reset (parallels the decision surface). Does
        NOT renew the lease; the caller renews separately after clearing."""
        if not (by or "").strip():
            return {"error": "a human actor must be named to clear a quarantine"}
        if not (rationale or "").strip():
            return {"error": "clearing a quarantine requires a rationale"}
        prev = self._quarantined_reason
        self._quarantined_reason = None
        return {"cleared": True, "by": by.strip(),
                "rationale": rationale.strip(), "previous_reason": prev}

    def effective_grade(self, *, metrics: Optional[dict[str, Any]] = None,
                        now: Optional[float] = None) -> str:
        """The grade the action gate must use for this agent right now — the
        single value that couples the Breaker to the gate (pass it as
        ``ActionRequest.autonomy_grade``)."""
        return self.status(metrics=metrics, now=now).effective_grade


def cap_grade(requested: str, ceiling: str) -> str:
    """Meet a requested grade with a ceiling (lattice meet on the grade
    lattice). Used wherever an OversightFacet.grade_ceiling must bind an
    agent's grade — the Breaker and the ceiling compose by taking the lower."""
    ri = _GRADE_IDX.get(requested, 0)
    if not ceiling:
        return _GRADES[ri]                 # no ceiling declared → don't cap
    # An UNRECOGNISED ceiling token is anomalous → fail-safe to L0 (most
    # restrictive), never the old fail-OPEN default of L4 / uncapped (M1).
    ci = _GRADE_IDX.get(ceiling, 0)
    return _GRADES[min(ri, ci)]
