# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Shadow-workflow classification over recorded cross-workspace crossings."""
from pathlib import Path

from workspaces.mutation_log import LogEvent, MutationLog
from workspaces.shadow_workflow import (classify_shadow_workflows,
                                   SHADOW)
from workspaces.workflows import Workflow, WorkflowStep, define_workflow


def _crossing(folder, lr, source, verdict, pair_ids=("p1",), role="source"):
    log = MutationLog(str(folder), log_root=lr)
    log.append(LogEvent(
        event="system", folder_path=str(Path(folder).resolve()),
        pair_id=f"xworkspace:{source}", channel="system", actor="companion",
        extra={"kind": "cross-workspace-read", "source": source, "role": role,
               "verdict": verdict, "source_pair_ids": list(pair_ids)}))


def test_no_crossings(tmp_path):
    r = classify_shadow_workflows(tmp_path / "c", log_root=tmp_path / "l")
    assert r["edges"] == []
    assert "no cross-workspace crossings" in r["summary"]


def test_go_crossing_without_workflow_is_shadow(tmp_path):
    c = tmp_path / "c"; lr = tmp_path / "l"
    _crossing(c, lr, "/data/src-a", "GO")
    r = classify_shadow_workflows(c, log_root=lr)
    assert len(r["shadow"]) == 1
    assert r["shadow"][0]["source"] == "/data/src-a"
    assert r["declared_workflows"] == []


def test_go_crossing_with_declared_workflow_is_review(tmp_path):
    c = tmp_path / "c"; lr = tmp_path / "l"
    _crossing(c, lr, "/data/src-a", "GO")
    define_workflow(str(c), Workflow(name="intake",
                    steps=[WorkflowStep(skill_id="regulatory-validator")]),
                    log_root=lr)
    r = classify_shadow_workflows(c, log_root=lr)
    assert r["shadow"] == []
    assert len(r["review"]) == 1
    assert "intake" in r["declared_workflows"]


def test_conditional_and_nogo(tmp_path):
    c = tmp_path / "c"; lr = tmp_path / "l"
    _crossing(c, lr, "/data/cond", "CONDITIONAL")
    _crossing(c, lr, "/data/blocked", "NO-GO")
    r = classify_shadow_workflows(c, log_root=lr)
    assert [e["source"] for e in r["needs_signoff"]] == ["/data/cond"]
    assert [e["source"] for e in r["blocked"]] == ["/data/blocked"]


def test_latest_verdict_wins_per_edge(tmp_path):
    c = tmp_path / "c"; lr = tmp_path / "l"
    _crossing(c, lr, "/data/x", "CONDITIONAL")
    _crossing(c, lr, "/data/x", "GO")          # later crossing upgrades to GO
    r = classify_shadow_workflows(c, log_root=lr)
    assert len(r["edges"]) == 1
    assert r["edges"][0]["count"] == 2
    assert r["edges"][0]["last_verdict"] == "GO"
    assert r["edges"][0]["class"] == SHADOW     # GO, no workflow


def test_high_fan_in_flagged(tmp_path):
    c = tmp_path / "c"; lr = tmp_path / "l"
    for s in ("a", "b", "c", "d"):
        _crossing(c, lr, f"/data/{s}", "GO")
    r = classify_shadow_workflows(c, log_root=lr, high_fan_in=3)
    assert r["fan_in"] == 4
    assert r["high_fan_in"] is True
    assert "high fan-in" in r["summary"]
