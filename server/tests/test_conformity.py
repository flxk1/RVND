# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""workspace_conformity projections (C1) — golden tests.

Gate C1: each op golden-tested against a seeded folder; evidence_pack
replay-verified (every record resolves to a signed event in the chain);
no op writes state.
"""


import pytest

from rvnd import conformity as cf
from rvnd import drift_monitor as dm
from rvnd import incidents as inc
from rvnd.action_gate import ActionRequest, Observables, gate
from rvnd.decisions.surface import record_choice
from rvnd.mutation_log import LogEvent, MutationLog
from rvnd.workflows import Workflow, WorkflowStep, define_workflow, run_workflow


@pytest.fixture()
def log_root(tmp_path):
    return tmp_path / "logroot"


@pytest.fixture()
def folder(tmp_path, log_root):
    """A folder that has lived: ingests, gate verdicts (incl. telemetry
    escalation and NO-GO), a workflow with a human-released hold, a drift
    baseline + finding + determination, and an agentic bypass event."""
    f = tmp_path / "ws"
    f.mkdir()
    log = MutationLog(f, log_root=log_root)
    for i in range(6):
        log.append(LogEvent(event="ingest", folder_path=str(f),
                            pair_id=f"p{i}", channel="document", actor="user"))
    # Gate history: benign GO, telemetry escalation (benign GO → CONDITIONAL
    # on low confidence — NT-13), a flagged sign-off action, and a NO-GO.
    inc.log_gate_decision(f, gate(ActionRequest("agent:nd-x", "read_folder", "L2")),
                          log_root=log_root)
    inc.log_gate_decision(
        f, gate(ActionRequest("agent:nd-x", "summarise-inbox", "L2"),
                observables=Observables(confidence=0.4)),
        log_root=log_root)
    inc.log_gate_decision(
        f, gate(ActionRequest("agent:nd-x", "export-report", "L3",
                              footprint=("personal-data",))),
        log_root=log_root)
    inc.log_gate_decision(
        f, gate(ActionRequest("agent:nd-x", "wire-funds", "L1",
                              footprint=("financial",))),
        log_root=log_root)
    # Workflow with a static external-publish footprint, approved by a human.
    define_workflow(f, Workflow(name="publish", steps=[
        WorkflowStep("draft", query="draft it"),
        WorkflowStep("mailer", query="send: ${steps[0].body}",
                     footprint=("external-publish",),
                     affected_parties=("the team",)),
    ]), log_root=log_root)
    run_workflow(f, "publish",
                 dispatcher=lambda folder_context, skill_id, query:
                     {"ok": True, "body": "a clean draft"},
                 log_root=log_root, autonomy_grade="L3",
                 step_approvals={1: "send approved by operator"},
                 actor="operator")
    # Drift: baseline, then a catalogue change, finding recorded + determined.
    dm.baseline(f, log_root=log_root, catalogue_fingerprint="cat-1")
    rep = dm.drift_tick(f, log_root=log_root,
                        thresholds=dm.DriftThresholds(min_events=1),
                        catalogue_fingerprint="cat-2")
    dm.record_findings(rep, log_root=log_root)
    record_choice(dm.finding_surface(rep), chosen_option_id="within-envelope",
                  rationale="planned catalogue addition for 0.7",
                  actor="operator", folder=str(f), log_root=log_root)
    # One agentic bypass (attestation must fail because of it).
    log.append(LogEvent(event="system", folder_path=str(f), pair_id="cap-x",
                        channel="llm_answer", actor="agent:nd-x",
                        extra={"kind": "llm-capture", "oversight_bypassed": True}))
    return f


def _log_bytes(folder, log_root):
    return MutationLog(folder, log_root=log_root).log_file.read_bytes()


# ── read-only property: no op writes state ───────────────────────────────────

def test_no_op_writes_state(folder, log_root):
    before = _log_bytes(folder, log_root)
    cf.evidence_pack(folder, log_root=log_root)
    cf.oversight_attestation(folder, log_root=log_root)
    cf.trigger_map(folder, log_root=log_root)
    cf.drift_report(folder, log_root=log_root, catalogue_fingerprint="cat-2")
    cf.risk_register(folder, log_root=log_root)
    cf.threat_model()
    assert _log_bytes(folder, log_root) == before


# ── evidence pack ─────────────────────────────────────────────────────────────

def test_evidence_pack_reconciles_to_the_chain(folder, log_root):
    pack = cf.evidence_pack(folder, log_root=log_root)
    assert pack["chain"]["ok"] is True
    log_ids = {e.audit_id for e in MutationLog(folder, log_root=log_root).replay()}
    pack_ids = {r["event_id"] for r in pack["records"]}
    assert pack_ids <= log_ids                  # every record resolves to an event
    assert pack_ids == log_ids                  # and the pack omits nothing
    assert all(r["signature_present"] for r in pack["records"])


def test_evidence_pack_distinguishes_initiation(folder, log_root):
    pack = cf.evidence_pack(folder, log_root=log_root)
    kinds = {r["initiation"] for r in pack["records"]}
    assert {"user", "agent", "system"} <= kinds  # Art. 15(4): user- vs AI-initiated


def test_evidence_pack_never_invents_unsourced_fields(folder, log_root):
    pack = cf.evidence_pack(folder, log_root=log_root)
    for r in pack["records"]:
        assert r["decision_model_identifier"] == cf.NOT_SPECIFIED
        assert r["training_data_provenance"] == cf.NOT_SPECIFIED


def test_evidence_pack_window_filters(folder, log_root):
    full = cf.evidence_pack(folder, log_root=log_root)
    empty = cf.evidence_pack(folder, log_root=log_root, until=1.0)  # epoch start
    assert len(full["records"]) > 0 and len(empty["records"]) == 0


def test_evidence_pack_carries_telemetry_escalation(folder, log_root):
    pack = cf.evidence_pack(folder, log_root=log_root)
    gates = [r for r in pack["records"] if r["record_kind"] == "gate-verdict"]
    esc = [r for r in gates if ((r["detail"] or {}).get("telemetry") or {}).get("escalated")]
    assert esc and esc[0]["detail"]["verdict"] == "CONDITIONAL"
    assert esc[0]["detail"]["telemetry"]["verdict_before"] == "GO"


# ── oversight attestation ─────────────────────────────────────────────────────

def test_attestation_fails_on_bypass_and_names_it(folder, log_root):
    att = cf.oversight_attestation(folder, log_root=log_root)
    assert att["attested"] is False
    assert len(att["bypassed_events"]) == 1
    assert att["gate_verdicts"]["NO-GO"] == 1
    # The human determinations are present with rationale.
    assert att["determinations"] and all(
        d["rationale_present"] for d in att["determinations"])
    # The workflow hold release is a recorded conditional release.
    assert att["conditional_releases"] and \
        att["conditional_releases"][0]["rationale_present"]


def test_attestation_passes_on_clean_period(tmp_path, log_root):
    f = tmp_path / "clean"
    f.mkdir()
    log = MutationLog(f, log_root=log_root)
    log.append(LogEvent(event="ingest", folder_path=str(f), pair_id="p0",
                        channel="document", actor="user"))
    att = cf.oversight_attestation(f, log_root=log_root)
    assert att["attested"] is True


# ── trigger map ───────────────────────────────────────────────────────────────

def test_trigger_map_is_neutral_without_a_regime(folder, log_root):
    """Substrate default: the inventory is produced, but legal labels are
    omitted and no statute is cited — jurisdiction-neutral by construction."""
    tm = cf.trigger_map(folder, log_root=log_root)
    assert tm["regime"] == "none"
    assert tm["operator_questions"] == []
    by_class = {a["action_class"]: a for a in tm["actions"]}
    assert by_class["export-report"]["instruments"] == [
        "no regime loaded — instruments omitted"]
    assert "Art." not in tm["basis"] and "GDPR" not in tm["basis"]


def test_trigger_map_derives_instruments_from_footprints(folder, log_root):
    reg = cf.load_regime()                       # EU AI Act reference regime (pack data)
    tm = cf.trigger_map(folder, log_root=log_root, regime=reg)
    assert tm["regime"] == "eu-ai-act"
    by_class = {a["action_class"]: a for a in tm["actions"]}
    assert "export-report" in by_class
    assert any("GDPR" in i for i in by_class["export-report"]["instruments"])
    # The workflow's STATIC footprint surfaces even though step 1 was approved.
    assert "dispatch:mailer" in by_class
    assert any("Art. 50" in i for i in by_class["dispatch:mailer"]["instruments"])
    assert any("GDPR" in i for i in tm["instruments_union"])


def test_trigger_map_asks_what_it_cannot_see(folder, log_root):
    tm = cf.trigger_map(folder, log_root=log_root, regime=cf.load_regime())
    qids = {q["id"] for q in tm["operator_questions"]}
    assert qids == {"q1", "q2", "q3", "q4"}
    q2 = next(q for q in tm["operator_questions"] if q["id"] == "q2")
    assert "operator input required" in q2["note"]   # Data Act has no runtime signal


# ── drift report ──────────────────────────────────────────────────────────────

def test_drift_report_shows_baseline_and_closed_finding(folder, log_root):
    dr = cf.drift_report(folder, log_root=log_root, catalogue_fingerprint="cat-2")
    assert len(dr["baselines"]) == 1
    assert dr["open_findings"] == []           # determined in the fixture
    assert dr["tick"]["baseline_audit_id"]


# ── risk register ─────────────────────────────────────────────────────────────

def test_risk_register_boundary_as_designed_and_exercised(folder, log_root):
    rr = cf.risk_register(folder, log_root=log_root)
    rows = {b["footprint"]: b for b in rr["automation_boundary"]}
    assert rows["financial"]["min_grade_base"] == "L3"
    assert rows["personal-data"]["min_grade_under_posture"] == "L2"
    restrictive = cf.risk_register(folder, log_root=log_root, posture="restrictive")
    rrows = {b["footprint"]: b for b in restrictive["automation_boundary"]}
    assert rrows["personal-data"]["min_grade_under_posture"] == "L3"
    # Exercised: the NO-GO on wire-funds is in the observed history.
    wf = next(a for a in rr["observed_actions"]
              if a["action_class"] == "wire-funds")
    assert wf["verdicts"].get("NO-GO") == 1


# ── threat model ──────────────────────────────────────────────────────────────

def test_threat_model_checks_presence_not_assumption(folder):
    t = cf.threat_model()
    by_cat = {r["category"]: r for r in t["categories"]}
    thread = by_cat["cascading injection across agents (inter-agent channel)"]
    assert thread["status"] == "covered"
    rce = by_cat["arbitrary code execution via agent tools"]
    assert rce["status"] == "not-applicable"
    # A made-up tests dir yields gaps, not silent "covered".
    t2 = cf.threat_model(tests_dir="/nonexistent")
    assert all(r["status"] in ("gap", "not-applicable")
               for r in t2["categories"])


def test_determinism_same_log_same_output(folder, log_root):
    a = cf.evidence_pack(folder, log_root=log_root)
    b = cf.evidence_pack(folder, log_root=log_root)
    assert a == b
    ta = cf.trigger_map(folder, log_root=log_root)
    tb = cf.trigger_map(folder, log_root=log_root)
    assert ta == tb
