# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P0b Layer 1b — complete-mediation reconciliation.

RVND consumes the ``effect-reconciliation`` package to reconcile its two ledgers,
both projected from the one signed chain: per-step ``gate-verdict`` events (the
authorisations) against terminal ``workflow-event`` outcomes (the effects). The
binding is by identity — both carry ``run_id`` + ``step_index`` — so a match is
BOUND, not inferred. An executed step with no authorising GO/CONDITIONAL behind
it is OBSERVED_NOT_AUTHORISED: mediation coverage MEASURED, not asserted.

Unit tests drive the projection with synthetic events; the integration tests run
a real workflow and read the reconciliation back out of ``evidence_pack``.
"""
from __future__ import annotations

from types import SimpleNamespace

from workspaces.reconciliation_binding import reconcile_projection


# ── synthetic chain events ──────────────────────────────────────────────────

def _ev(ts, extra, audit_id="a"):
    return SimpleNamespace(extra=extra, event="system", ts=ts, audit_id=audit_id)


def _gate(ts, run_id, step, verdict, audit_id="g"):
    return _ev(ts, {"kind": "gate-verdict", "run_id": run_id, "step_index": step,
                    "decision": {"verdict": verdict,
                                 "audit_triple": {"object": f"dispatch:p:{step}",
                                                  "subject": "workflow:wf"}}}, audit_id)


def _effect(ts, run_id, step, state="done", audit_id="e"):
    return _ev(ts, {"kind": "workflow-event", "run_id": run_id, "step_index": step,
                    "state": state, "skill_id": f"p:{step}"}, audit_id)


def test_authorised_effect_is_matched_and_bound():
    r = reconcile_projection([_gate(1.0, "r", 0, "GO"), _effect(2.0, "r", 0)],
                             since_ts=0.0, until_ts=10.0)
    assert r["status"] == "reconciled"
    assert r["matched"] == 1
    assert r["unauthorised_rate"] == 0.0
    assert r["binding_rate"] == 1.0                 # id-bound, not inferred
    assert r["observed_not_authorised"] == []


def test_effect_with_no_authorisation_is_flagged():
    # The whole point: an effect with no permission behind it is measured, not
    # asserted away. unauthorised_rate is mediation coverage, computed.
    r = reconcile_projection([_effect(2.0, "r", 0)], since_ts=0.0, until_ts=10.0)
    assert r["status"] == "diverged"
    assert r["unauthorised_rate"] == 1.0
    assert len(r["observed_not_authorised"]) == 1
    assert r["observed_not_authorised"][0]["authorisation_id"] == "r:0"


def test_a_refusal_is_not_an_authorisation():
    # A NO_GO gate-verdict must NOT launder into a permission: an effect that
    # followed a refusal reads as OBSERVED_NOT_AUTHORISED, the louder truth.
    r = reconcile_projection([_gate(1.0, "r", 0, "NO_GO"), _effect(2.0, "r", 0)],
                             since_ts=0.0, until_ts=10.0)
    assert r["matched"] == 0
    assert r["unauthorised_rate"] == 1.0
    assert len(r["observed_not_authorised"]) == 1


def test_conditional_counts_as_an_authorisation():
    r = reconcile_projection([_gate(1.0, "r", 0, "CONDITIONAL"), _effect(2.0, "r", 0)],
                             since_ts=0.0, until_ts=10.0)
    assert r["matched"] == 1
    assert r["unauthorised_rate"] == 0.0


def test_authorised_not_observed_is_never_a_defect():
    # Permission granted, nothing done (a held or skipped step). Reported, but
    # it does not diverge the reconciliation on its own.
    r = reconcile_projection([_gate(1.0, "r", 0, "GO")], since_ts=0.0, until_ts=10.0)
    assert r["status"] == "reconciled"
    assert r["authorised_not_observed"] == 1
    assert r["unauthorised_rate"] == 0.0


def test_non_workflow_gate_is_not_reconciled():
    # A gate call with no run_id/step (e.g. an ad-hoc governance gate) is
    # unbindable, so it is skipped — not counted as a phantom authorisation.
    g = _ev(1.0, {"kind": "gate-verdict",
                  "decision": {"verdict": "GO", "audit_triple": {}}})
    r = reconcile_projection([g], since_ts=0.0, until_ts=10.0)
    assert r["matched"] == 0
    assert r["authorised_not_observed"] == 0
    assert r["status"] == "reconciled"


# ── real chain, through run_workflow + evidence_pack ────────────────────────

def test_evidence_pack_reconciles_a_real_run(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    from workspaces.workflows import (
        Workflow, WorkflowStep, define_workflow, run_workflow,
    )
    define_workflow(str(fc), Workflow(name="intake", description="t",
                    steps=[WorkflowStep(skill_id="p:a")]), log_root=log)
    out = run_workflow(str(fc), "intake",
                       dispatcher=lambda **kw: {"ok": True}, log_root=log)
    assert out["ok"], out

    from workspaces import conformity
    rec = conformity.evidence_pack(fc, log_root=log)["reconciliation"]
    assert rec["status"] == "reconciled"
    assert rec["matched"] >= 1
    assert rec["unauthorised_rate"] == 0.0
    assert rec["binding_rate"] == 1.0               # the gate-verdict stamp bound it
    assert rec["observed_not_authorised"] == []


def test_evidence_pack_flags_an_unauthorised_effect(tmp_path, monkeypatch):
    # A step outcome journalled onto the chain with no gate-verdict behind it —
    # what a bypass path would leave. The pack must MEASURE it, not miss it.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    from workspaces.workflows import _log_workflow_event
    _log_workflow_event(str(fc), run_id="ghost", workflow="wf", step_index=0,
                        state="done", skill_id="p:x", log_root=log)

    from workspaces import conformity
    rec = conformity.evidence_pack(fc, log_root=log)["reconciliation"]
    assert rec["status"] == "diverged"
    assert rec["unauthorised_rate"] == 1.0
    assert rec["observed_not_authorised"][0]["authorisation_id"] == "ghost:0"


def test_a_sub_second_window_is_measurable(tmp_path):
    """`_iso` rendered whole seconds, so both bounds of any window narrower than
    a second collapsed to one string and the projection reported UNRECONCILED —
    "nobody looked" — for a window somebody had asked about. Fails safe, but a
    legitimate sub-second window could not be measured at all."""
    from workspaces.reconciliation_binding import _iso, reconcile_projection

    t = 1755000000.100000
    assert _iso(t) != _iso(t + 0.000100), "bounds inside one second must stay distinct"

    out = reconcile_projection([], since_ts=t, until_ts=t + 0.5)
    assert out["status"] != "unreconciled", out
