# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Complete-mediation evidence — reconcile the authorisation ledger against the
effect ledger, both projected from the one signed chain.

RVND records, per workflow step, a ``gate-verdict`` (the decision that authorised
— or refused — a dispatch) and, when the step actually runs, a terminal
``workflow-event`` (``done``/``failed`` — the effect). A governance report that
counts only the first and calls it coverage is keeping a single-entry book. This
projects BOTH and reconciles them via the ``effect-reconciliation`` package: an
executed step with no authorising GO/CONDITIONAL behind it is
OBSERVED_NOT_AUTHORISED — a mediation gap MEASURED from two ledgers that
disagree, not asserted about the architecture.

The binding is by identity: the gate-verdict carries ``run_id`` + ``step_index``
(stamped by ``incidents.log_gate_decision``) and the step effect carries the same,
so a confirmed match is BOUND (proof), never inferred.

Pure projection — reads chain events only, no clock, no environment; the window
bounds arrive as parameters (the replay-reconcilable contract, mirroring
``enforcement_posture_binding``). Consume-not-regrow: all reconciliation logic
lives upstream in ``effect_reconciliation`` (MIT, stdlib, git-pinned); this
module is reader + adapter only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

GATE_EVENT_KIND = "gate-verdict"
WORKFLOW_EVENT_KIND = "workflow-event"
# Verdicts that GRANT a permission. NO_GO is a refusal and must never be passed
# as an authorisation — an effect matching a refused step is exactly the loud
# OBSERVED_NOT_AUTHORISED reading the package exists to surface.
_AUTHORISING = frozenset({"GO", "CONDITIONAL"})
# Terminal per-step states that mean the dispatch actually executed (an effect).
# A blocked or held step never reaches the dispatch, so it never emits these.
_EFFECT_STATES = frozenset({"done", "failed"})


def _iso(ts: float) -> str:
    """Microsecond resolution, deliberately.

    Rendering at whole seconds silently destroyed any window narrower than one
    second: both bounds collapsed to the same string, the window came out empty
    or inverted, and the projection reported UNRECONCILED — "nobody looked" —
    for a window somebody had in fact asked about. It fails safe rather than
    permissive, but a legitimate sub-second window could not be measured at all,
    and a test splitting a chain microseconds apart passed or failed depending
    on where the run fell relative to a second boundary.

    The same rendering is used for the record timestamps, so ordering within a
    second now survives into the reconciliation too.
    """
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _key(run_id: str, step_index: Any) -> str:
    return f"{run_id}:{step_index}"


def reconcile_projection(events: Iterable, *, since_ts: float,
                         until_ts: float) -> dict[str, Any]:
    """Project the authorisation + effect ledgers from ``events`` and reconcile
    over the half-open window ``[since_ts, until_ts)``. Returns a JSON-able
    summary.

    Evidence enrichment must never break the pack, so if the package is absent
    the block degrades to ``{"status": "unavailable"}`` rather than raising.
    """
    try:
        import effect_reconciliation as er
    except Exception:                                   # noqa: BLE001
        return {"status": "unavailable",
                "detail": "effect-reconciliation not installed"}

    auths: list = []
    effects: list = []
    for e in events:
        x = getattr(e, "extra", None) or {}
        kind = x.get("kind") or getattr(e, "event", "")
        ts = float(getattr(e, "ts", 0.0) or 0.0)
        if kind == GATE_EVENT_KIND:
            run_id = str(x.get("run_id", ""))
            if not run_id or x.get("step_index") is None:
                continue                                # non-workflow gate — unbindable
            decision = x.get("decision") or {}
            if str(decision.get("verdict", "")) not in _AUTHORISING:
                continue                                # a refusal is not a permission
            triple = decision.get("audit_triple") or {}
            auths.append(er.Authorisation(
                id=_key(run_id, x.get("step_index")),
                action=str(triple.get("object") or ""),
                subject=str(triple.get("subject") or ""),
                at=_iso(ts)))
        elif kind == WORKFLOW_EVENT_KIND:
            step_index = x.get("step_index")
            if (str(x.get("state", "")) not in _EFFECT_STATES
                    or step_index is None or step_index < 0):
                continue                                # not an executed-step effect
            run_id = str(x.get("run_id", ""))
            skill = str(x.get("skill_id") or "")
            effects.append(er.Effect(
                id=f"{_key(run_id, step_index)}:{getattr(e, 'audit_id', '')}",
                action=f"dispatch:{skill}" if skill else "",
                subject="",
                at=_iso(ts),
                authorisation_id=_key(run_id, step_index)))

    rec = er.reconcile(auths, effects, since=_iso(since_ts), until=_iso(until_ts))
    return {
        "status": rec.status.value,
        "ok": rec.ok,
        # Mediation coverage, measured: share of observed effects with no
        # authorisation behind them. 0.0 means every effect passed the gate.
        "unauthorised_rate": rec.unauthorised_rate,
        # How much of the result is proof vs inference (1.0 here — id-bound).
        "binding_rate": rec.binding_rate,
        "matched": len(rec.matched),
        "authorised_not_observed": len(rec.authorised_not_observed),
        "observed_not_authorised": [
            {"action": f.action, "at": f.at, "authorisation_id": f.authorisation_id}
            for f in rec.observed_not_authorised],
        "duplicated_excess": sum(d.excess for d in rec.duplicated),
    }
