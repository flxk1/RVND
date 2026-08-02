# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-10: workspace-migrate mid-crash recoverability + gc safety.

migrate_workspace is a sequence of independent side effects (move → audit
event → registry update), not a transaction. Two data-loss hazards follow,
both closed here:

  1. After a SUCCESSFUL migration the carried-over events still record the
     OLD folder_path, so a naive gc that recovers the path from the first
     event would see a now-missing path and reclaim a healthy, actively-used
     migrated workspace.
  2. A crash BETWEEN the move and the audit event leaves a destination dir
     with no migration record at all — a naive gc would delete it.

A crash-window marker (written before the move, cleared after the registry
update) plus migration-aware path recovery close both.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces.mutation_log import LogEvent, MutationLog, folder_hash
from workspaces import workspace_migrate
from workspaces.workspace_migrate import (
    _MIGRATION_MARKER,
    WorkspaceMigrateError,
    gc_orphans,
    migrate_workspace,
)

pytestmark = pytest.mark.security  # destructive-op integrity


def _seed(folder: Path, log_root: Path, n: int = 3) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    log = MutationLog(folder, log_root=log_root)
    for i in range(n):
        log.append(LogEvent(event="ingest", folder_path=str(folder),
                            pair_id=f"pair-{i}"))


def test_successful_migration_is_not_reclaimed_by_gc(tmp_path):
    """After migrate, the destination's events still name the OLD path; gc
    must recover the CURRENT path from the migration record and leave the
    live workspace alone (previously it saw the gone old path → orphan)."""
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=4)
    new.mkdir()

    migrate_workspace(old, new, log_root=log_root, operator="alice")
    # Simulate the real world: the user moved the folder, so OLD is gone.
    import shutil as _sh
    _sh.rmtree(old)
    assert not old.exists() and new.exists()

    new_hash = folder_hash(new)
    candidates = gc_orphans(log_root=log_root)
    migrated = next(c for c in candidates if c.folder_hash == new_hash)
    assert migrated.action == "ok", (
        f"gc classified a healthy migrated workspace as {migrated.action!r} — "
        "it recovered the old path from the first event, not the migration "
        "target")
    assert migrated.recovered_path == str(new.resolve())


def test_gc_delete_does_not_remove_migrated_workspace(tmp_path):
    """The load-bearing safety property: even in delete mode, a successfully
    migrated workspace whose old folder is gone survives gc."""
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=3)
    new.mkdir()
    migrate_workspace(old, new, log_root=log_root)
    import shutil as _sh
    _sh.rmtree(old)

    new_hash = folder_hash(new)
    gc_orphans(log_root=log_root, delete=True)
    assert (log_root / new_hash / "events.jsonl").exists(), (
        "gc --delete removed a healthy migrated workspace log")


def test_crash_between_move_and_audit_leaves_recoverable_marker(tmp_path, monkeypatch):
    """Inject a crash right after the fs move, before the audit event. The
    destination must carry the crash-window marker so its identity is known,
    and gc must classify it as 'migrating' (never orphan/delete)."""
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=3)
    new.mkdir()
    old_hash = folder_hash(old)
    new_hash = folder_hash(new)

    # Make the migration audit event raise — a stand-in for a crash after the
    # move (step 5) and before/at the audit event (step 6).
    class _Boom(RuntimeError):
        pass

    real_append_raw = MutationLog.append_raw

    def _boom_append_raw(self, *a, **k):
        raise _Boom("simulated crash after move, before audit event")

    monkeypatch.setattr(MutationLog, "append_raw", _boom_append_raw)
    with pytest.raises(_Boom):
        migrate_workspace(old, new, log_root=log_root)
    monkeypatch.setattr(MutationLog, "append_raw", real_append_raw)

    # The move happened; the audit event did not. The destination must carry
    # the marker so its identity survives the crash.
    dst = log_root / new_hash
    assert (dst / "events.jsonl").exists(), "the move did land"
    assert not (log_root / old_hash).exists(), "source dir was moved away"
    marker = dst / _MIGRATION_MARKER
    assert marker.exists(), "crash-window marker missing — dst identity is lost"
    assert json.loads(marker.read_text())["to_path"] == str(new.resolve())

    # gc must NOT reclaim an in-flight migration, even in delete mode.
    candidates = gc_orphans(log_root=log_root, delete=True)
    migrating = next(c for c in candidates if c.folder_hash == new_hash)
    assert migrating.action == "migrating", (
        f"in-flight migration classified as {migrating.action!r}")
    assert (dst / "events.jsonl").exists(), "gc deleted an in-flight migration"


def test_marker_write_failure_refuses_before_move(tmp_path, monkeypatch):
    """A migration without its crash marker must not move the source log."""
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=2)
    new.mkdir()
    old_hash = folder_hash(old)
    new_hash = folder_hash(new)
    source_log = log_root / old_hash / "events.jsonl"
    original_write_text = Path.write_text

    def _deny_marker(self, *args, **kwargs):
        if self.name == _MIGRATION_MARKER:
            raise OSError("read-only log directory")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _deny_marker)

    with pytest.raises(WorkspaceMigrateError, match="recovery marker"):
        migrate_workspace(old, new, log_root=log_root)

    assert source_log.exists(), "source log moved without a recovery marker"
    assert not (log_root / new_hash).exists(), (
        "destination log exists despite marker write failure"
    )


def test_marker_is_cleared_after_successful_migration(tmp_path):
    """On the happy path the marker must not linger — otherwise every
    migrated dir would read as perpetually 'migrating'."""
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=2)
    new.mkdir()
    migrate_workspace(old, new, log_root=log_root)
    new_hash = folder_hash(new)
    assert not (log_root / new_hash / _MIGRATION_MARKER).exists(), (
        "migration marker not cleared after a successful migration")
