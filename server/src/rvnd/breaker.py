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
from pathlib import Path
from typing import Any, Iterable, Optional


from .adapters.policy_languages import (grade_index as _grade_index,
                                         grade_levels as _grade_levels)

_GRADES = _grade_levels()          # grade lattice consumed from governance's grammar
_GRADE_IDX = _grade_index()


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


# ---------------------------------------------------------------------------
# Witness escape as a tripwire input (detect → respond closure)
# ---------------------------------------------------------------------------
#
# ``witness_escape.record_witness_escape`` writes the "detect" half — one
# signed event per recorded escape. This section is the "respond" half: it
# reads those events back as a Breaker metric and arms the SAME flag-style
# ``Tripwire`` mechanism ``default_tripwires`` and ``oversight_drift.
# drift_tripwire`` already use. No new state and no new ordering are
# introduced — a witness escape simply supplies one more ``metrics`` entry to
# the existing ``Breaker.status`` / ``trip_check`` composition, so QUARANTINED
# stays exactly as sticky and exactly as human-clear-only as it already was
# for every other tripwire.

#: The metric name a witness-escape tripwire watches. ``0.0`` / "max" would
#: also work, but "flag" (like ``attestation_failed`` / ``chain_invalid``)
#: reads truer: a witness escape either happened or it didn't.
WITNESS_ESCAPE_METRIC = "witness_escape_detected"


def witness_escape_tripwire(name: str = "witness_escape") -> Tripwire:
    """The tripwire a Breaker arms for a recorded witness escape (an agent's
    run touched paths outside its authorised folder). Pair it with the
    metrics from :func:`witness_escape_metrics` — the same
    ``{"<metric>": True/False}`` shape :func:`default_tripwires` and
    ``oversight_drift.drift_tripwire`` already produce."""
    return Tripwire(name, WITNESS_ESCAPE_METRIC, 0.0, "flag")


def witness_escape_metrics(
    folder_context: "str | Path", actor: str, *,
    since: Optional[float] = None,
    log_root: "str | Path | None" = None,
) -> dict[str, Any]:
    """Breaker-metrics read for the witness-escape tripwire.

    ``True`` iff a witness-escape event for THIS actor was recorded in THIS
    workspace's mutation log — scoped by construction (a mutation log is one
    folder's chain, so an escape recorded in a different workspace is never
    read here) and by the actor filter in
    ``witness_escape.recent_witness_escapes`` (so an escape recorded for a
    different actor in the SAME workspace never sets this flag either). With
    no witness-escape event recorded for this actor/workspace/window, this
    returns ``False`` — the tripwire that consumes it is then a no-op, same as
    any other unarmed or clean tripwire (default/empty ⇒ unchanged).

    ``since`` bounds the window (pass a lease's ``granted_at`` to scope to
    "since the current lease"); ``None`` matches any escape ever recorded for
    this actor in this workspace.
    """
    from .witness_escape import recent_witness_escapes
    hits = recent_witness_escapes(folder_context, actor, since=since, log_root=log_root)
    return {WITNESS_ESCAPE_METRIC: bool(hits)}


def ensure_witness_escape_armed(breaker: "Breaker") -> "Breaker":
    """Ensure the witness-escape tripwire is armed on ``breaker`` (idempotent
    — mirrors ``loop_graph._breaker_with_drift``'s "arm if not already armed"
    shape). Adds no new tripwire kind beyond the existing flag mechanism."""
    if any(t.metric == WITNESS_ESCAPE_METRIC for t in breaker.tripwires):
        return breaker
    breaker.tripwires.append(witness_escape_tripwire())
    return breaker


def status_after_witness_escape_check(
    breaker: "Breaker",
    folder_context: "str | Path",
    actor: str,
    *,
    since: Optional[float] = None,
    log_root: "str | Path | None" = None,
    extra_metrics: Optional[dict[str, Any]] = None,
    now: Optional[float] = None,
) -> BreakerStatus:
    """Arm the witness-escape tripwire (if not already) and evaluate
    ``breaker.status`` with the recorded-escape metric folded in.

    This is the single call that closes detect → respond for a caller: record
    an escape via ``witness_escape.record_witness_escape``, then ask this for
    the verdict. ``since`` defaults to ``breaker.lease.granted_at`` — "recent"
    means "since the current lease/window" — pass an explicit value to widen
    or narrow it. Scoping (folder × actor) and stickiness (QUARANTINED persists
    on this ``Breaker`` instance until a human ``clear``s it) both come for
    free from the composition this reuses; nothing new is invented here.
    """
    ensure_witness_escape_armed(breaker)
    window_since = since if since is not None else breaker.lease.granted_at
    metrics = dict(extra_metrics or {})
    metrics.update(witness_escape_metrics(
        folder_context, actor, since=window_since, log_root=log_root))
    return breaker.status(metrics=metrics, now=now)
