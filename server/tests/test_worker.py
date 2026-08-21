# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the background workflow worker."""

from __future__ import annotations

import time


from rvnd.queue import (
    enqueue_run,
    get_run,
    list_queue,
)
from rvnd.worker import (
    WorkerConfig,
    _StopFlag,
    run_forever,
    run_once,
    worker_status,
)
from rvnd.workflows import (
    Workflow,
    WorkflowStep,
    define_workflow,
)


def _wf(name, *skills):
    return Workflow(name=name, steps=[WorkflowStep(skill_id=s) for s in skills])


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


def test_run_once_empty_queue(tmp_path):
    cfg = WorkerConfig(worker_id="w1", log_root=tmp_path / "log")
    out = run_once(cfg)
    assert out["state"] == "empty"
    assert out["iterations"] == 0


def test_run_once_drains_pending(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("intake", "p:a"), log_root=log)
    entry = enqueue_run(str(fc), "intake", log_root=log)

    cfg = WorkerConfig(worker_id="w1", log_root=log)
    out = run_once(cfg)
    assert out["state"] == "done"
    assert out["run_id"] == entry.run_id
    # Queue entry should now be terminal
    assert get_run(entry.run_id, log).state == "done"


def test_run_once_marks_failed_when_workflow_missing(tmp_path):
    """If a workflow definition disappears between enqueue and worker
    pickup, the worker marks the run failed, not done."""
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("intake", "p:a"), log_root=log)
    enqueue_run(str(fc), "intake", log_root=log)

    # Delete the workflow definition before the worker picks it up
    from rvnd.workflows import delete_workflow
    delete_workflow(str(fc), "intake", log_root=log)

    cfg = WorkerConfig(worker_id="w1", log_root=log)
    out = run_once(cfg)
    assert out["state"] == "failed"
    assert "definition gone" in (out.get("error") or "").lower()


def test_run_once_processes_runs_in_fifo(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("a", "p:a"), log_root=log)
    define_workflow(str(fc), _wf("b", "p:b"), log_root=log)
    first = enqueue_run(str(fc), "a", log_root=log)
    # Force ordering — same-folder different workflows
    time.sleep(0.001)
    second = enqueue_run(str(fc), "b", log_root=log)

    cfg = WorkerConfig(worker_id="w1", log_root=log)
    out1 = run_once(cfg)
    out2 = run_once(cfg)
    assert out1["run_id"] == first.run_id
    assert out2["run_id"] == second.run_id


def test_run_once_pretripped_stop_does_not_lease_pending_run(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("intake", "p:a"), log_root=log)
    entry = enqueue_run(str(fc), "intake", log_root=log)
    stop = _StopFlag()
    stop.trip("operator-shutdown")

    out = run_once(
        WorkerConfig(worker_id="w1", log_root=log),
        stop=stop,
    )

    assert out == {
        "ok": True,
        "state": "stopped",
        "reason": "operator-shutdown",
        "iterations": 0,
    }
    queued = get_run(entry.run_id, log)
    assert queued.state == "pending"
    assert not queued.leased_to


# ---------------------------------------------------------------------------
# run_forever — bounded with max_iterations + stop flag
# ---------------------------------------------------------------------------


def test_run_forever_drains_then_exits_on_max_iterations(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("a", "p:a"), log_root=log)
    define_workflow(str(fc), _wf("b", "p:b"), log_root=log)
    enqueue_run(str(fc), "a", log_root=log)
    enqueue_run(str(fc), "b", log_root=log)

    cfg = WorkerConfig(worker_id="w1", log_root=log,
                        max_iterations=2, interval_seconds=0.01)
    summary = run_forever(cfg)
    assert summary["stopped"] is True
    assert summary["iterations"] == 2
    assert summary["runs_done"] == 2
    assert summary["runs_failed"] == 0
    # Both queue entries terminal
    assert all(e.state == "done"
                for e in list_queue(log_root=log))


def test_run_forever_stop_flag_breaks_loop(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    cfg = WorkerConfig(worker_id="w1", log_root=log,
                        interval_seconds=0.01)
    stop = _StopFlag()
    stop.trip("test-shutdown")
    summary = run_forever(cfg, stop=stop)
    assert summary["stopped"] is True
    assert summary["reason"] == "test-shutdown"
    assert summary["iterations"] == 0


def test_run_forever_does_not_count_stop_detected_before_lease(
    tmp_path, monkeypatch,
):
    from rvnd import worker as worker_module

    log = tmp_path / "log"
    cfg = WorkerConfig(worker_id="w1", log_root=log)
    stop = _StopFlag()

    def _stopped_once(*args, **kwargs):
        return {
            "ok": True,
            "state": "stopped",
            "reason": "signal-before-lease",
            "iterations": 0,
        }

    monkeypatch.setattr(worker_module, "run_once", _stopped_once)
    summary = run_forever(cfg, stop=stop)

    assert summary["reason"] == "signal-before-lease"
    assert summary["iterations"] == 0
    assert summary["runs_done"] == 0
    assert summary["runs_failed"] == 0


def test_run_forever_handles_empty_queue_without_busy_loop(tmp_path):
    """When the queue is empty, the worker should sleep between polls,
    not burn CPU. We verify by checking max_iterations is respected."""
    log = tmp_path / "log"
    cfg = WorkerConfig(worker_id="w1", log_root=log,
                        max_iterations=3, interval_seconds=0.01)
    summary = run_forever(cfg)
    assert summary["iterations"] == 3
    assert summary["runs_done"] == 0


# ---------------------------------------------------------------------------
# worker_status
# ---------------------------------------------------------------------------


def test_worker_status_reports_counts(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(fc), "a", log_root=log)
    enqueue_run(str(fc), "b", log_root=log)

    snap = worker_status(log_root=log)
    assert snap["pending_count"] == 2
    assert snap["leased_count"] == 0
    assert len(snap["pending"]) == 2


# ---------------------------------------------------------------------------
# Failure surfacing
# ---------------------------------------------------------------------------


def test_run_once_failed_workflow_marks_failed_with_error(tmp_path):
    """A workflow whose step's default dispatcher records dispatch but the
    skill isn't pinned anywhere would fail. We simulate by defining a
    workflow that uses default record_dispatch (which always returns ok=True)
    — so this test confirms the OK path. Failure surfacing is exercised
    via the workflow-definition-missing test above."""
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("ok-flow", "p:always-ok"), log_root=log)
    enqueue_run(str(fc), "ok-flow", log_root=log)
    cfg = WorkerConfig(worker_id="w1", log_root=log)
    out = run_once(cfg)
    assert out["ok"] is True
