# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Calibration sampling ledger — built to answer the whitepaper's
two harshest objections:

  * calibration regress (#1): the safety of reuse must be validated by an
    INDEPENDENT signal, not by the same automated confidence that chose to
    reuse. Here that signal is a human RE-JUDGING a sampled reuse — ground
    truth, not self-report.
  * gameable metric (#2): the integrity number must measure QUALITY, not
    activity. Here it is the DISAGREEMENT rate on sampled reuses — a rising
    trend is oversight catching decay, which a rubber stamp cannot fake
    without lying on the chain.

Also closes objection #8 (leverage only 'potential'): reuse events are now
logged, so reuse is realised and measurable. All events ride the signed
append-only chain; report is a pure replay projection.

Invariants (written BEFORE the logic):
  K1  log_reuse appends a reuse event; the report counts it
  K2  a human sample judgment (agree) is recorded; sampling rate computed
  K3  disagreements drive a disagreement rate; above threshold → decay flag
  K4  under-sampling (reuse with too few human judgments) → under-sampled flag
  K5  a sample judgment needs an actor AND (on disagree) a rationale; an
      AGENT or non-active actor cannot supply ground truth — fail-closed
  K6  pure, additive replay projection; deterministic
  K7  adequately sampled + low disagreement → 'calibrated'
"""
from __future__ import annotations

import os

import pytest

from rvnd.calibration import calibration_report, judge_sample, log_reuse
from rvnd.parties import register_party, set_party_status

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    lr = str(tmp_path / "logs")
    register_party(str(ws), "alex", "human", name="Alex", log_root=lr)
    register_party(str(ws), "runner", "agent", owner="alex", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _fp(itype="liability_cap"):
    return {"issue_type": itype, "profile": "legal-de", "rooms": ["§ 309"]}


def test_log_reuse_is_counted(env):                               # K1
    rid = log_reuse(env["ws"], fingerprint=_fp(), solver="skill:liab",
                    log_root=env["lr"])
    assert rid
    rep = calibration_report(env["ws"], log_root=env["lr"])
    assert rep["reuse_count"] == 1


def test_sample_judgment_agree_sets_rate(env):                    # K2
    rid = log_reuse(env["ws"], fingerprint=_fp(), solver="s", log_root=env["lr"])
    judge_sample(env["ws"], reuse_id=rid, actor="alex", agreed=True,
                 log_root=env["lr"])
    rep = calibration_report(env["ws"], log_root=env["lr"])
    assert rep["sampled"] == 1
    assert rep["sampling_rate"] == 1.0
    assert rep["disagreement_rate"] == 0.0


def test_disagreement_drives_decay_flag(env):                     # K3
    ids = [log_reuse(env["ws"], fingerprint=_fp(), solver="s",
                     log_root=env["lr"]) for _ in range(4)]
    judge_sample(env["ws"], reuse_id=ids[0], actor="alex", agreed=False,
                 rationale="reused cap wrong for this counterparty",
                 log_root=env["lr"])
    judge_sample(env["ws"], reuse_id=ids[1], actor="alex", agreed=False,
                 rationale="precedent superseded by new clause",
                 log_root=env["lr"])
    judge_sample(env["ws"], reuse_id=ids[2], actor="alex", agreed=True,
                 log_root=env["lr"])
    rep = calibration_report(env["ws"], decay_threshold=0.2, log_root=env["lr"])
    assert rep["disagreement_rate"] > 0.2
    assert rep["flag"] == "calibration-decay"
    assert rep["responsible"] is False


def test_undersampling_is_flagged(env):                           # K4
    for _ in range(100):
        log_reuse(env["ws"], fingerprint=_fp(), solver="s", log_root=env["lr"])
    rep = calibration_report(env["ws"], sampling_floor=0.05, log_root=env["lr"])
    assert rep["flag"] == "under-sampled"
    assert rep["responsible"] is False


def test_judgment_failclosed(env):                                # K5
    rid = log_reuse(env["ws"], fingerprint=_fp(), solver="s", log_root=env["lr"])
    with pytest.raises(ValueError):                 # no actor
        judge_sample(env["ws"], reuse_id=rid, actor="", agreed=True,
                     log_root=env["lr"])
    with pytest.raises(ValueError):                 # disagree without rationale
        judge_sample(env["ws"], reuse_id=rid, actor="alex", agreed=False,
                     rationale="", log_root=env["lr"])
    with pytest.raises(ValueError):                 # agent can't supply truth
        judge_sample(env["ws"], reuse_id=rid, actor="runner", agreed=True,
                     log_root=env["lr"])
    set_party_status(env["ws"], "alex", "suspended", actor="alex",
                     log_root=env["lr"])
    with pytest.raises(ValueError):                 # non-active party
        judge_sample(env["ws"], reuse_id=rid, actor="alex", agreed=True,
                     log_root=env["lr"])


def test_projection_pure_and_deterministic(env):                 # K6
    log_reuse(env["ws"], fingerprint=_fp(), solver="s", log_root=env["lr"])
    a = calibration_report(env["ws"], log_root=env["lr"])
    b = calibration_report(env["ws"], log_root=env["lr"])
    assert a == b


def test_calibrated_when_sampled_and_low_disagreement(env):      # K7
    ids = [log_reuse(env["ws"], fingerprint=_fp(), solver="s",
                     log_root=env["lr"]) for _ in range(10)]
    for rid in ids[:2]:
        judge_sample(env["ws"], reuse_id=rid, actor="alex", agreed=True,
                     log_root=env["lr"])
    rep = calibration_report(env["ws"], sampling_floor=0.1,
                             decay_threshold=0.2, log_root=env["lr"])
    assert rep["flag"] is None
    assert rep["responsible"] is True
