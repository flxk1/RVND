# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Behavioral attestation — the producer behind the Breaker's integrity flag.

The Breaker (``breaker.py``) already *consumes* an ``attestation_failed`` flag:
its default tripwires freeze an agent whose attestation has failed. Nothing
*produced* that flag. This module is the producer.

The control it implements (taxonomy §4.5 / §6.2): the honest limit is that Workspace
can seal its own memory layer but not a third-party model's weights, so the
backstop against an agent that learned something it should not have — including
an agent subverted by an injected/poisoned teacher — is a scheduled gold-set
probe battery. The rule is sharp:

    behaviour changed AND the log holds no admitted-learning event that
    accounts for the change  ⇒  unlogged learning  ⇒  attestation fails
    ⇒  the Breaker quarantines the agent.

Behaviour that changed *with* a corresponding admitted event is governed
learning (the Lens admitted it, with provenance); it is surfaced for review but
does not fail attestation. Behaviour that did not change passes.

Scope honesty, same as the rest of the substrate: this is a **detective**
control. It compares observed behaviour to a trusted baseline and reconciles the
delta against the audit log; it returns a verdict and the metric the Breaker
reads. It does not itself stop anything — the Breaker decides, the runtime
enforces. The reconciliation count (how many admitted-learning events sit in the
window) is injected by the caller, exactly as the Breaker injects its clock and
its green-checks verdict, so this module stays pure and replayable.

Nothing here interprets behaviour. A "signature" is an opaque fingerprint of a
probe's output supplied by the caller; :func:`signature` is a convenience
default (normalise + SHA-256) but the core only compares strings.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

_WS = re.compile(r"\s+")


def signature(text: str) -> str:
    """Default behavioural fingerprint: case- and whitespace-normalised SHA-256.

    A convenience so callers have one stable way to turn a probe's response into
    a comparable signature. The attestation core does not require it — any
    deterministic string fingerprint works."""
    norm = _WS.sub(" ", (text or "").strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Probe:
    """One gold-set item: a probe id and the behavioural signature captured at a
    trusted baseline. The battery is a set of these, frozen when the agent (or
    its governed memory) was known-good."""
    id: str
    baseline_signature: str


# Attestation verdicts.
PASS = "PASS"                          # observed behaviour matches the baseline
UNLOGGED_LEARNING = "UNLOGGED_LEARNING"  # changed, and the log explains nothing
EXPLAINED_DRIFT = "EXPLAINED_DRIFT"    # changed, but admitted-learning events exist


@dataclass
class AttestationResult:
    verdict: str
    diverged: list[str] = field(default_factory=list)     # probe ids that changed
    unobserved: list[str] = field(default_factory=list)   # probes with no observation
    admitted_learning_events: int = 0
    attestation_failed: bool = False
    reason: str = ""

    def to_metrics(self) -> dict[str, Any]:
        """The metric bag the Breaker reads. The ``attestation_failed`` flag is
        the one its default ``attestation`` tripwire consumes; the extras are
        informational for a richer status."""
        return {
            "attestation_failed": self.attestation_failed,
            "attestation_diverged": len(self.diverged),
            "attestation_unobserved": len(self.unobserved),
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def attest(
    observed: Mapping[str, str],
    gold: Iterable[Probe],
    *,
    admitted_learning_events: int,
    tolerance: int = 0,
) -> AttestationResult:
    """Compare observed probe signatures to the gold-set baseline and reconcile
    any divergence against admitted-learning events.

    - ``observed`` maps probe id → the signature observed now.
    - ``gold`` is the baseline battery.
    - ``admitted_learning_events`` is how many admitted-learning events sit in
      the window under test (the caller queries the log for this; 0 means the
      log accounts for no behaviour change).
    - ``tolerance`` allows N diverged probes before failing (default 0: any
      unexplained change is a failure).

    A probe present in ``gold`` but absent from ``observed`` is *unobserved* — a
    coverage gap, not evidence of tampering — so it is counted and reported but
    does not on its own fail attestation (mirrors the Breaker's "unmeasured ≠
    tripped").
    """
    if admitted_learning_events < 0:
        raise ValueError("admitted_learning_events must be >= 0")
    if tolerance < 0:
        raise ValueError("tolerance must be >= 0")

    diverged: list[str] = []
    unobserved: list[str] = []
    for probe in gold:
        seen = observed.get(probe.id)
        if seen is None:
            unobserved.append(probe.id)
        elif seen != probe.baseline_signature:
            diverged.append(probe.id)

    n = len(diverged)
    if n <= tolerance:
        reason = "behaviour matches baseline"
        if unobserved:
            reason += f"; {len(unobserved)} probe(s) unobserved (coverage gap)"
        return AttestationResult(
            PASS, diverged, unobserved, admitted_learning_events,
            attestation_failed=False, reason=reason)

    if admitted_learning_events <= 0:
        return AttestationResult(
            UNLOGGED_LEARNING, diverged, unobserved, admitted_learning_events,
            attestation_failed=True,
            reason=(f"{n} probe(s) diverged with no admitted-learning event to "
                    f"account for the change — unlogged learning"))

    return AttestationResult(
        EXPLAINED_DRIFT, diverged, unobserved, admitted_learning_events,
        attestation_failed=False,
        reason=(f"{n} probe(s) diverged; {admitted_learning_events} admitted-"
                f"learning event(s) in window — governed drift, review advised"))


def breaker_metrics(
    *,
    attestation: AttestationResult,
    chain_valid: bool,
    **extra: Any,
) -> dict[str, Any]:
    """Assemble the metric bag for :meth:`Breaker.status`.

    Couples the two integrity producers to the two integrity tripwires the
    Breaker arms by default: ``attestation_failed`` and ``chain_invalid``. Pass
    extra observables (``usd_spent_iteration``, ``error_rate`` …) as kwargs.
    This is the seam that turns a detected subversion into an actual quarantine:
    a failed attestation here trips the Breaker, which drops the effective grade
    to L0."""
    return {
        **attestation.to_metrics(),
        "chain_invalid": not chain_valid,
        **extra,
    }


@dataclass
class GreenChecks:
    ok: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def green_checks(
    *,
    attestation: AttestationResult,
    chain_valid: bool,
    budget_ok: bool = True,
) -> GreenChecks:
    """The lease-renewal verdict: a lease may be renewed only on green checks.

    Green = attestation did not fail AND the audit chain verifies AND the budget
    is within cap. Feed ``ok``/``reason`` straight into :meth:`Breaker.renew`.
    A non-green result refuses renewal, so the lease is allowed to lapse and
    autonomy decays to L0 — the dead-man's switch closing on a failing agent
    without any human having to press stop."""
    failures: list[str] = []
    if attestation.attestation_failed:
        failures.append(f"attestation: {attestation.reason}")
    if not chain_valid:
        failures.append("audit chain does not verify")
    if not budget_ok:
        failures.append("budget cap exceeded")
    if failures:
        return GreenChecks(False, "renewal refused — " + "; ".join(failures))
    return GreenChecks(True, "all green checks passed")
