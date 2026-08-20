# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Drift monitor — baseline, deterministic tick, findings, decision surface.

C0 item 2 of the conformity-runtime design. Gate requirements:
deterministic tick (same log ⇒ same report), baseline/tick/finding round-trip
on a seeded folder, terminal-for-machine discipline (the monitor surfaces,
never decides).
"""

import time

import pytest

from workspaces import drift_monitor as dm
from workspaces.drift_monitor import DriftThresholds
from workspaces.decisions.surface import record_choice
from workspaces.mutation_log import LogEvent, MutationLog
from workspaces.pinned_skills import PinnedSkill, load_pinned_skills, save_pinned_skills
from workspaces.policy import load_policy, save_policy

THIN = DriftThresholds(share_shift=0.15, min_events=5)


@pytest.fixture()
def folder(tmp_path):
    f = tmp_path / "ws"
    f.mkdir()
    return f


@pytest.fixture()
def log_root(tmp_path):
    return tmp_path / "logroot"


def _seed(folder, log_root, *, n_user=10, n_agent=0, skill="nd-x"):
    log = MutationLog(folder, log_root=log_root)
    for i in range(n_user):
        log.append(LogEvent(event="ingest", folder_path=str(folder),
                            pair_id=f"p{i}", channel="document", actor="user"))
    for i in range(n_agent):
        log.append(LogEvent(event="system", folder_path=str(folder),
                            pair_id="workflow-event", channel="system",
                            actor=f"agent:{skill}",
                            extra={"kind": "workflow-event", "skill_id": skill,
                                   "state": "done", "run_id": "r", "workflow": "w",
                                   "step_index": i}))
    return log


# ── baseline ──────────────────────────────────────────────────────────────────

def test_baseline_writes_signed_event_and_state(folder, log_root):
    _seed(folder, log_root)
    rec = dm.baseline(folder, log_root=log_root, catalogue_fingerprint="cat-1")
    assert rec["audit_id"]
    st = rec["state"]
    assert st["catalogue_fingerprint"] == "cat-1"
    assert st["behaviour"]["n"] == 10
    assert st["policy"]["oversight_default_level"]
    # The event is in the chain and the chain still verifies.
    log = MutationLog(folder, log_root=log_root)
    assert log.verify_chain()


def test_tick_without_baseline_says_so(folder, log_root):
    _seed(folder, log_root)
    rep = dm.drift_tick(folder, log_root=log_root)
    assert rep.no_baseline and not rep.ok


# ── determinism ───────────────────────────────────────────────────────────────

def test_tick_is_deterministic(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root)
    _seed(folder, log_root, n_user=2, n_agent=6)
    as_of = time.time() + 1
    r1 = dm.drift_tick(folder, log_root=log_root, thresholds=THIN, as_of=as_of)
    r2 = dm.drift_tick(folder, log_root=log_root, thresholds=THIN, as_of=as_of)
    assert r1.to_dict() == r2.to_dict()


def test_no_change_is_ok(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root)
    # Same mix continues: 10 more user ingests.
    _seed(folder, log_root, n_user=10)
    rep = dm.drift_tick(folder, log_root=log_root, thresholds=THIN)
    assert rep.ok and not rep.findings and not rep.too_thin


def test_thin_window_is_surfaced_not_compared(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root)
    _seed(folder, log_root, n_user=2)
    rep = dm.drift_tick(folder, log_root=log_root,
                        thresholds=DriftThresholds(min_events=20))
    assert rep.too_thin and not rep.findings


# ── structural drift ──────────────────────────────────────────────────────────

def test_policy_change_is_structural_finding(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root)
    pol = load_policy(folder)
    pol.oversight_default_level = "autonomous"
    save_policy(folder, pol)
    rep = dm.drift_tick(folder, log_root=log_root, thresholds=THIN)
    metrics = [f["metric"] for f in rep.structural]
    assert "policy:oversight_default_level" in metrics
    assert not rep.ok


def test_pinned_skill_change_is_structural_finding(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root)
    store = load_pinned_skills(folder, log_root=log_root)
    store.skills.append(PinnedSkill(id="nd-new"))
    save_pinned_skills(folder, store, log_root=log_root)
    rep = dm.drift_tick(folder, log_root=log_root, thresholds=THIN)
    pin = [f for f in rep.structural if f["metric"] == "pinned_skills"]
    assert pin and pin[0]["added"] == ["nd-new"]


def test_catalogue_fingerprint_change_is_structural_finding(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root, catalogue_fingerprint="cat-1")
    rep = dm.drift_tick(folder, log_root=log_root, thresholds=THIN,
                        catalogue_fingerprint="cat-2")
    assert any(f["metric"] == "catalogue_fingerprint" for f in rep.structural)


# ── behavioural drift ─────────────────────────────────────────────────────────

def test_actor_mix_shift_fires_behavioural_finding(folder, log_root):
    _seed(folder, log_root, n_user=20)          # baseline: 100% user
    dm.baseline(folder, log_root=log_root)
    _seed(folder, log_root, n_user=0, n_agent=20)  # window: 100% agent
    rep = dm.drift_tick(folder, log_root=log_root, thresholds=THIN)
    metrics = [f["metric"] for f in rep.behavioural]
    assert "by_actor_kind:agent" in metrics
    assert not rep.ok


def test_new_dominant_skill_fires_finding(folder, log_root):
    _seed(folder, log_root, n_agent=10, skill="nd-a")
    dm.baseline(folder, log_root=log_root)
    _seed(folder, log_root, n_user=0, n_agent=10, skill="nd-b")
    rep = dm.drift_tick(folder, log_root=log_root, thresholds=THIN)
    assert any(m["metric"] == "by_skill:nd-b" for m in rep.behavioural)


# ── recording: idempotent, replayable ────────────────────────────────────────

def test_record_findings_is_idempotent(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root, catalogue_fingerprint="cat-1")
    rep = dm.drift_tick(folder, log_root=log_root, thresholds=THIN,
                        catalogue_fingerprint="cat-2")
    first = dm.record_findings(rep, log_root=log_root)
    assert len(first) == len(rep.findings) >= 1
    second = dm.record_findings(rep, log_root=log_root)
    assert second == []                            # same (baseline, metric) keys
    log = MutationLog(folder, log_root=log_root)
    assert log.verify_chain()


# ── decision surface: terminal-for-machine ───────────────────────────────────

def test_finding_surface_offers_three_options_none_recommended(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root, catalogue_fingerprint="a")
    rep = dm.drift_tick(folder, log_root=log_root, thresholds=THIN,
                        catalogue_fingerprint="b")
    surf = dm.finding_surface(rep)
    assert {o.id for o in surf.options} == {"within-envelope", "reassess", "halt"}
    assert surf.residual is True
    # Art. 3(23) round-trip: human originates the determination with rationale.
    rec = record_choice(surf, chosen_option_id="within-envelope",
                        rationale="catalogue change was the planned 0.7 tool addition",
                        actor="operator", folder=str(folder), log_root=log_root)
    assert rec.get("audit_id") and rec["rationale"]


def test_finding_surface_refuses_clean_report(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root)
    _seed(folder, log_root, n_user=10)
    rep = dm.drift_tick(folder, log_root=log_root, thresholds=THIN)
    with pytest.raises(ValueError):
        dm.finding_surface(rep)


def test_choice_without_rationale_is_refused(folder, log_root):
    _seed(folder, log_root)
    dm.baseline(folder, log_root=log_root, catalogue_fingerprint="a")
    rep = dm.drift_tick(folder, log_root=log_root, thresholds=THIN,
                        catalogue_fingerprint="b")
    surf = dm.finding_surface(rep)
    rec = record_choice(surf, chosen_option_id="halt", rationale="  ",
                        actor="operator", folder=str(folder), log_root=log_root)
    assert "error" in rec                          # origination, not ratification
