# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B7.3 (0.6.8) — mark_run_done / mark_run_failed lease ownership."""

from __future__ import annotations

import time

import pytest

from workspaces.queue import (
    LeaseStolen,
    enqueue_run,
    get_run,
    mark_run_done,
    mark_run_failed,
    take_next_run,
    _lease_path,
    _read_lease,
)


def test_take_next_run_records_worker_as_lease_holder(tmp_path):
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    enqueue_run(folder, "wf-1", log_root=log_root)
    entry = take_next_run("worker-A", log_root=log_root)
    assert entry is not None
    lease = _read_lease(entry.run_id, log_root=log_root)
    assert lease is not None
    assert lease.worker_id == "worker-A"
    assert lease.expires_at > int(time.time())


def test_mark_run_done_refuses_wrong_worker(tmp_path):
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    enqueue_run(folder, "wf-1", log_root=log_root)
    entry = take_next_run("worker-A", log_root=log_root)
    with pytest.raises(LeaseStolen):
        mark_run_done(entry.run_id, "worker-B", log_root=log_root)
    # Entry is still leased
    assert get_run(entry.run_id, log_root=log_root).state == "leased"


def test_mark_run_done_succeeds_for_correct_worker(tmp_path):
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    enqueue_run(folder, "wf-1", log_root=log_root)
    entry = take_next_run("worker-A", log_root=log_root)
    assert mark_run_done(entry.run_id, "worker-A", log_root=log_root)
    assert get_run(entry.run_id, log_root=log_root).state == "done"


def test_mark_run_failed_refuses_wrong_worker(tmp_path):
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    enqueue_run(folder, "wf-1", log_root=log_root)
    entry = take_next_run("worker-A", log_root=log_root)
    with pytest.raises(LeaseStolen):
        mark_run_failed(entry.run_id, "worker-B", "oops", log_root=log_root)


def test_lease_stolen_when_lease_expired(tmp_path, monkeypatch):
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    enqueue_run(folder, "wf-1", log_root=log_root)
    entry = take_next_run("worker-A", log_root=log_root, lease_seconds=1)
    # Expire the lease by rewriting its expires_at.
    p = _lease_path(entry.run_id, log_root=log_root)
    import json
    data = json.loads(p.read_text())
    data["expires_at"] = int(time.time()) - 10
    p.write_text(json.dumps(data))
    with pytest.raises(LeaseStolen):
        mark_run_done(entry.run_id, "worker-A", log_root=log_root)


def test_back_compat_mark_done_without_worker_id_still_works(tmp_path):
    from workspaces.queue import mark_done
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    enqueue_run(folder, "wf-1", log_root=log_root)
    entry = take_next_run("worker-A", log_root=log_root)
    # No worker_id supplied → no lease check (pre-0.6.8 behaviour).
    assert mark_done(entry.run_id, log_root=log_root)
    assert get_run(entry.run_id, log_root=log_root).state == "done"
