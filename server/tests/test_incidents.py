# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Incident escalation — each class fires on its condition; recording is
idempotent; closure is human-only (C0 item 3)."""

import time

import pytest

from rvnd import drift_monitor as dm
from rvnd import incidents as inc
from rvnd.action_gate import ActionRequest, gate
from rvnd.decisions.surface import record_choice
from rvnd.mutation_log import DiskFullError, LogEvent, MutationLog


@pytest.fixture()
def folder(tmp_path):
    f = tmp_path / "ws"
    f.mkdir()
    return f


@pytest.fixture()
def log_root(tmp_path):
    return tmp_path / "logroot"


def _seed(folder, log_root, n=3):
    log = MutationLog(folder, log_root=log_root)
    for i in range(n):
        log.append(LogEvent(event="ingest", folder_path=str(folder),
                            pair_id=f"p{i}", channel="document", actor="user"))
    return log


def test_clean_folder_scans_clean(folder, log_root):
    _seed(folder, log_root)
    rep = inc.scan(folder, log_root=log_root)
    assert rep.ok and rep.incidents == []


# ── chain tampering ───────────────────────────────────────────────────────────

def test_chain_tamper_fires_chain_incident(folder, log_root):
    log = _seed(folder, log_root, n=5)
    # Manual rewrite of one line — the attack the chain exists to catch.
    p = log.log_file
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2].replace("p2", "p2-tampered")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rep = inc.scan(folder, log_root=log_root)
    assert any(i.klass == "chain-verification-failure" for i in rep.incidents)


# ── NO-GO storm ───────────────────────────────────────────────────────────────

def test_no_go_storm_fires_at_threshold(folder, log_root):
    _seed(folder, log_root)
    bad = ActionRequest("a", "export", "L1", footprint=("personal-data",))
    for _ in range(inc.NO_GO_STORM_THRESHOLD):
        d = gate(bad)
        assert d.verdict.value == "NO-GO"
        inc.log_gate_decision(folder, d, log_root=log_root)
    rep = inc.scan(folder, log_root=log_root)
    storm = [i for i in rep.incidents if i.klass == "no-go-storm"]
    assert storm and storm[0].detail["count"] == inc.NO_GO_STORM_THRESHOLD


def test_below_threshold_is_not_a_storm(folder, log_root):
    _seed(folder, log_root)
    bad = ActionRequest("a", "export", "L1", footprint=("personal-data",))
    for _ in range(inc.NO_GO_STORM_THRESHOLD - 1):
        inc.log_gate_decision(folder, gate(bad), log_root=log_root)
    rep = inc.scan(folder, log_root=log_root)
    assert not [i for i in rep.incidents if i.klass == "no-go-storm"]


def test_old_no_gos_age_out_of_the_window(folder, log_root):
    _seed(folder, log_root)
    bad = ActionRequest("a", "export", "L1", footprint=("personal-data",))
    for _ in range(inc.NO_GO_STORM_THRESHOLD):
        inc.log_gate_decision(folder, gate(bad), log_root=log_root)
    rep = inc.scan(folder, log_root=log_root,
                   as_of=time.time() + 10, window_s=1.0)
    assert not [i for i in rep.incidents if i.klass == "no-go-storm"]


# ── oversight bypass ─────────────────────────────────────────────────────────

def test_agentic_bypass_fires_incident(folder, log_root):
    log = _seed(folder, log_root)
    log.append(LogEvent(event="system", folder_path=str(folder),
                        pair_id="cap-1", channel="llm_answer",
                        actor="agent:nd-x",
                        extra={"kind": "llm-capture", "oversight_bypassed": True}))
    rep = inc.scan(folder, log_root=log_root)
    assert any(i.klass == "oversight-bypassed" for i in rep.incidents)


def test_user_bypass_is_not_an_incident(folder, log_root):
    """The audit floor concerns AGENTIC operation; a user's own interactive
    call with oversight off is their acknowledged choice, not an incident."""
    log = _seed(folder, log_root)
    log.append(LogEvent(event="system", folder_path=str(folder),
                        pair_id="cap-2", channel="llm_answer", actor="user",
                        extra={"kind": "llm-capture", "oversight_bypassed": True}))
    rep = inc.scan(folder, log_root=log_root)
    assert not [i for i in rep.incidents if i.klass == "oversight-bypassed"]


# ── drift findings without determination ─────────────────────────────────────

def test_unresolved_drift_finding_is_an_incident(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root, catalogue_fingerprint="a")
    rep_d = dm.drift_tick(folder, log_root=log_root,
                          thresholds=dm.DriftThresholds(min_events=1),
                          catalogue_fingerprint="b")
    dm.record_findings(rep_d, log_root=log_root)
    rep = inc.scan(folder, log_root=log_root)
    assert any(i.klass == "drift-unresolved" for i in rep.incidents)


def test_determined_drift_finding_is_closed(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root, catalogue_fingerprint="a")
    rep_d = dm.drift_tick(folder, log_root=log_root,
                          thresholds=dm.DriftThresholds(min_events=1),
                          catalogue_fingerprint="b")
    dm.record_findings(rep_d, log_root=log_root)
    surf = dm.finding_surface(rep_d)
    record_choice(surf, chosen_option_id="within-envelope",
                  rationale="planned catalogue update", actor="operator",
                  folder=str(folder), log_root=log_root)
    rep = inc.scan(folder, log_root=log_root)
    assert not [i for i in rep.incidents if i.klass == "drift-unresolved"]


# ── recording + exceptions + surface ─────────────────────────────────────────

def test_record_incidents_is_idempotent(folder, log_root):
    log = _seed(folder, log_root)
    log.append(LogEvent(event="system", folder_path=str(folder),
                        pair_id="cap-1", channel="llm_answer",
                        actor="agent:nd-x",
                        extra={"oversight_bypassed": True}))
    rep = inc.scan(folder, log_root=log_root)
    first = inc.record_incidents(rep, log_root=log_root)
    assert len(first) == 1
    assert inc.record_incidents(rep, log_root=log_root) == []
    assert MutationLog(folder, log_root=log_root).verify_chain()


def test_report_exception_logs_and_never_raises(folder, log_root):
    _seed(folder, log_root)
    out = inc.report_exception(folder, DiskFullError("disk full"),
                               log_root=log_root, context="mutation_log.append")
    assert out["logged"] is True and out["detail"]["type"] == "DiskFullError"
    # Unwritable log root: still no raise.
    out2 = inc.report_exception("/nonexistent/nope", DiskFullError("x"),
                                log_root=None, context="append")
    assert out2["logged"] in (True, False)  # never raises is the contract


def test_incident_surface_round_trip(folder, log_root):
    log = _seed(folder, log_root)
    log.append(LogEvent(event="system", folder_path=str(folder),
                        pair_id="cap-1", channel="llm_answer",
                        actor="agent:nd-x",
                        extra={"oversight_bypassed": True}))
    rep = inc.scan(folder, log_root=log_root)
    surf = inc.incident_surface(rep)
    assert {o.id for o in surf.options} == {"acknowledge", "investigate", "halt"}
    rec = record_choice(surf, chosen_option_id="investigate",
                        rationale="bypass not expected on this folder",
                        actor="operator", folder=str(folder), log_root=log_root)
    assert rec.get("audit_id")


def test_incident_surface_refuses_clean_report(folder, log_root):
    _seed(folder, log_root)
    rep = inc.scan(folder, log_root=log_root)
    with pytest.raises(ValueError):
        inc.incident_surface(rep)
