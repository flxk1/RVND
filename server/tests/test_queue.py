# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the background-runner queue + lease primitive.

Covers:
- enqueue happy path + concurrency-v1 rejection (already_queued/running)
- take_next_run claims the oldest pending and writes a lease
- Stale-lease auto-revoke restores pending state on next take
- renew_lease extends expiry; missing lease returns False
- mark_done / mark_failed / cancel_run move to terminal state and drop the lease
- list_queue filters by state and folder
- Resume across process restarts (state file rebuild from queue.jsonl)
"""

from __future__ import annotations

import os
import time

import pytest

from rvnd.queue import (
    _lease_path,
    _queue_lock,
    _read_lease,
    _replay_queue,
    _state_path,
    _write_lease,
    cancel_run,
    enqueue_run,
    get_run,
    list_queue,
    mark_done,
    mark_failed,
    renew_lease,
    take_next_run,
)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_queue_lock_is_owner_only_and_repairs_legacy_mode(tmp_path):
    log = tmp_path / "log"
    log.mkdir()
    lock = log / ".queue.lock"
    lock.write_text("")
    os.chmod(lock, 0o644)

    with _queue_lock(log):
        assert lock.stat().st_mode & 0o777 == 0o600

    assert lock.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------


def test_enqueue_creates_pending_entry(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"

    e = enqueue_run(str(folder), "intake", enqueued_by="alex", log_root=log)
    assert e.state == "pending"
    assert e.workflow_name == "intake"
    assert e.enqueued_by == "alex"
    assert e.run_id.startswith("wfrun:")

    # State file written + queue.jsonl appended
    assert _state_path(log).exists()
    assert (log / "queue.jsonl").exists()


def test_enqueue_rejects_already_queued(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "intake", log_root=log)
    with pytest.raises(ValueError) as ei:
        enqueue_run(str(folder), "intake", log_root=log)
    assert "already_queued" in str(ei.value)


def test_enqueue_allows_different_workflow_on_same_folder(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    a = enqueue_run(str(folder), "intake", log_root=log)
    b = enqueue_run(str(folder), "review", log_root=log)
    assert a.run_id != b.run_id


def test_enqueue_allows_same_workflow_after_terminal(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    a = enqueue_run(str(folder), "intake", log_root=log)
    # Simulate worker completing the run
    take_next_run("w1", log_root=log)
    assert mark_done(a.run_id, log_root=log) is True
    # New enqueue OK after terminal state
    b = enqueue_run(str(folder), "intake", log_root=log)
    assert b.run_id != a.run_id
    assert b.state == "pending"


# ---------------------------------------------------------------------------
# take_next_run
# ---------------------------------------------------------------------------


def test_take_next_returns_oldest_pending(tmp_path):
    f1 = tmp_path / "f1"; f1.mkdir()
    f2 = tmp_path / "f2"; f2.mkdir()
    log = tmp_path / "log"

    first = enqueue_run(str(f1), "a", log_root=log)
    time.sleep(0.01)  # ensure ordering by enqueued_at
    enqueue_run(str(f2), "b", log_root=log)

    claimed = take_next_run("worker-1", log_root=log)
    assert claimed is not None
    assert claimed.run_id == first.run_id
    assert claimed.state == "leased"
    assert claimed.leased_to == "worker-1"
    # Lease file exists
    assert _lease_path(claimed.run_id, log).exists()


def test_take_next_returns_none_when_empty(tmp_path):
    log = tmp_path / "log"
    assert take_next_run("worker-1", log_root=log) is None


def test_take_next_skips_already_leased(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    enqueue_run(str(folder), "b", log_root=log)

    first = take_next_run("w1", lease_seconds=60, log_root=log)
    second = take_next_run("w2", lease_seconds=60, log_root=log)
    assert first.run_id != second.run_id


# ---------------------------------------------------------------------------
# Stale-lease auto-revoke (the crash-recovery property)
# ---------------------------------------------------------------------------


def test_stale_lease_revokes_on_next_take(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    first = take_next_run("dead-worker", lease_seconds=60, log_root=log)
    assert first is not None

    # Forge a stale lease — write expires_at in the past
    lease = _read_lease(first.run_id, log)
    lease.expires_at = int(time.time()) - 10
    _write_lease(lease, log)

    # Next take should revoke the stale lease and re-claim
    reclaimed = take_next_run("live-worker", lease_seconds=60, log_root=log)
    assert reclaimed is not None
    assert reclaimed.run_id == first.run_id
    assert reclaimed.leased_to == "live-worker"


def test_missing_lease_file_treated_as_stale(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    first = take_next_run("worker", log_root=log)

    # Delete lease file but leave entry as "leased" in state
    _lease_path(first.run_id, log).unlink()

    # next take must auto-recover
    reclaimed = take_next_run("worker-2", log_root=log)
    assert reclaimed is not None
    assert reclaimed.run_id == first.run_id


# ---------------------------------------------------------------------------
# renew / mark / cancel
# ---------------------------------------------------------------------------


def test_renew_extends_expiry(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    e = take_next_run("worker", lease_seconds=10, log_root=log)

    before = _read_lease(e.run_id, log).expires_at
    time.sleep(0.05)
    assert renew_lease(e.run_id, additional_seconds=120, log_root=log) is True
    after = _read_lease(e.run_id, log).expires_at
    assert after > before


def test_renew_returns_false_when_missing(tmp_path):
    log = tmp_path / "log"
    assert renew_lease("wfrun:nope", log_root=log) is False


def test_mark_done_clears_lease(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    e = take_next_run("worker", log_root=log)

    assert mark_done(e.run_id, log_root=log) is True
    assert not _lease_path(e.run_id, log).exists()
    assert get_run(e.run_id, log).state == "done"

    # Idempotent on terminal — returns False
    assert mark_done(e.run_id, log_root=log) is False


def test_mark_failed_records_error(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    e = take_next_run("worker", log_root=log)
    assert mark_failed(e.run_id, error="boom", log_root=log) is True
    after = get_run(e.run_id, log)
    assert after.state == "failed"
    assert after.error == "boom"


def test_cancel_pending(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    e = enqueue_run(str(folder), "a", log_root=log)
    assert cancel_run(e.run_id, actor="user", log_root=log) is True
    assert get_run(e.run_id, log).state == "cancelled"


def test_cancel_leased_drops_lease(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    e = take_next_run("worker", log_root=log)
    assert cancel_run(e.run_id, log_root=log) is True
    assert not _lease_path(e.run_id, log).exists()


# ---------------------------------------------------------------------------
# list_queue + filtering
# ---------------------------------------------------------------------------


def test_list_queue_filters_by_state(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    a = enqueue_run(str(folder), "a", log_root=log)
    b = enqueue_run(str(folder), "b", log_root=log)
    take_next_run("w", log_root=log)  # leases a

    pending = list_queue(state_filter="pending", log_root=log)
    leased = list_queue(state_filter="leased", log_root=log)
    assert [e.run_id for e in pending] == [b.run_id]
    assert [e.run_id for e in leased]  == [a.run_id]


def test_list_queue_filters_by_folder(tmp_path):
    f1 = tmp_path / "f1"; f1.mkdir()
    f2 = tmp_path / "f2"; f2.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(f1), "a", log_root=log)
    enqueue_run(str(f2), "b", log_root=log)
    out = list_queue(folder_path=str(f1), log_root=log)
    assert len(out) == 1
    assert out[0].workflow_name == "a"


# ---------------------------------------------------------------------------
# Replay across "process restart"
# ---------------------------------------------------------------------------


def test_inspect_stuck_runs_finds_leased_stale(tmp_path):
    from rvnd.queue import inspect_stuck_runs
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    e = take_next_run("dead-worker", lease_seconds=60, log_root=log)
    # Forge expiry into the past
    lease = _read_lease(e.run_id, log)
    lease.expires_at = int(time.time()) - 10
    _write_lease(lease, log)

    stuck = inspect_stuck_runs(log_root=log)
    assert len(stuck) == 1
    assert stuck[0]["kind"] == "leased-stale"
    assert stuck[0]["entry"]["run_id"] == e.run_id


def test_inspect_stuck_runs_finds_missing_lease(tmp_path):
    from rvnd.queue import inspect_stuck_runs
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    e = take_next_run("worker", log_root=log)
    _lease_path(e.run_id, log).unlink()

    stuck = inspect_stuck_runs(log_root=log)
    assert any(s["kind"] == "leased-stale" and s["lease"] is None
                for s in stuck)


def test_inspect_stuck_runs_finds_pending_stale(tmp_path):
    from rvnd.queue import inspect_stuck_runs
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    e = enqueue_run(str(folder), "a", log_root=log)
    # Backdate enqueued_at by forging the state file
    from rvnd.queue import _load_state, _save_state
    state = _load_state(log)
    state[e.run_id].enqueued_at = "2020-01-01T00:00:00.000000Z"
    _save_state(state, log)

    stuck = inspect_stuck_runs(stale_pending_seconds=60, log_root=log)
    assert any(s["kind"] == "pending-stale" for s in stuck)


def test_inspect_stuck_runs_ignores_fresh_pending(tmp_path):
    from rvnd.queue import inspect_stuck_runs
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    stuck = inspect_stuck_runs(stale_pending_seconds=300, log_root=log)
    # Fresh enqueue should not appear
    assert stuck == []


def test_resume_run_flips_leased_to_pending(tmp_path):
    from rvnd.queue import resume_run
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    e = take_next_run("dead", log_root=log)
    assert resume_run(e.run_id, log_root=log) is True
    after = get_run(e.run_id, log)
    assert after.state == "pending"
    assert not _lease_path(e.run_id, log).exists()
    # And the next take_next picks it back up
    again = take_next_run("alive", log_root=log)
    assert again.run_id == e.run_id


def test_resume_returns_false_for_non_leased(tmp_path):
    from rvnd.queue import resume_run
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    e = enqueue_run(str(folder), "a", log_root=log)
    # Pending is not resumeable (it's already pending)
    assert resume_run(e.run_id, log_root=log) is False
    # Terminal is not resumeable
    take_next_run("w", log_root=log)
    mark_done(e.run_id, log_root=log)
    assert resume_run(e.run_id, log_root=log) is False


def test_concurrent_takers_get_disjoint_runs(tmp_path):
    """Two threads racing on take_next_run must never grab the same run.

    Validates the fcntl.flock cross-process lock holds against in-process
    threading concurrency (a strict subset of the cross-process case).
    """
    import threading
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    # Enqueue 12 runs
    for i in range(12):
        enqueue_run(str(folder), f"wf-{i}", log_root=log)

    grabbed: list[str] = []
    grabbed_lock = threading.Lock()

    def worker_loop(worker_id):
        for _ in range(4):
            e = take_next_run(worker_id, log_root=log)
            if e is None:
                break
            with grabbed_lock:
                grabbed.append(e.run_id)

    threads = [threading.Thread(target=worker_loop, args=(f"w-{i}",))
                for i in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert len(grabbed) == 12, f"expected 12 takes, got {len(grabbed)}"
    assert len(set(grabbed)) == len(grabbed), \
        "duplicate run_id taken — lock didn't hold under concurrency"


def test_state_file_rebuilds_from_jsonl(tmp_path):
    """If the materialised state file is deleted (cold restart, corruption),
    the queue can still rebuild its state from queue.jsonl."""
    folder = tmp_path / "wks"; folder.mkdir()
    log = tmp_path / "log"
    enqueue_run(str(folder), "a", log_root=log)
    enqueue_run(str(folder), "b", log_root=log)
    # Delete the materialised view
    _state_path(log).unlink()
    # Replay must reconstruct both entries
    replayed = _replay_queue(log)
    assert len(replayed) == 2
    # Subsequent list_queue also works
    listed = list_queue(log_root=log)
    assert len(listed) == 2
