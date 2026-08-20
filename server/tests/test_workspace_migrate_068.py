# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B7.1 (0.6.8) — workspaces workspace migrate."""

from __future__ import annotations

from pathlib import Path

import pytest

from workspaces.mutation_log import LogEvent, MutationLog, folder_hash
from workspaces.workspace_migrate import (
    WorkspaceMigrateError,
    migrate_workspace,
)


def _seed(folder: Path, log_root: Path, n: int = 3) -> MutationLog:
    folder.mkdir(parents=True, exist_ok=True)
    log = MutationLog(folder, log_root=log_root)
    for i in range(n):
        log.append(LogEvent(event="ingest", folder_path=str(folder),
                            pair_id=f"pair-{i}"))
    return log


def test_migrate_moves_log_dir_and_writes_audit_event(tmp_path):
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=4)
    new.mkdir()

    old_hash = folder_hash(old)
    new_hash = folder_hash(new)
    assert (log_root / old_hash / "events.jsonl").exists()

    result = migrate_workspace(old, new, log_root=log_root, operator="alice")

    assert result.from_hash == old_hash
    assert result.to_hash == new_hash
    assert result.event_count == 4
    assert result.strategy == "move"
    assert result.audit_id

    # OLD dir is gone; NEW has events.
    assert not (log_root / old_hash).exists()
    assert (log_root / new_hash / "events.jsonl").exists()

    # The new log contains 4 carried-over events PLUS the migrate
    # system event.
    new_log = MutationLog(new, log_root=log_root)
    events = list(new_log.replay())
    assert len(events) == 5
    sys_event = events[-1]
    assert sys_event.event == "system"
    assert sys_event.extra.get("kind") == "workspace_migrated"
    assert sys_event.extra.get("from_hash") == old_hash
    assert sys_event.extra.get("to_hash") == new_hash
    assert sys_event.actor == "operator:alice"


def test_migrate_refuses_when_target_exists(tmp_path):
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=2)
    _seed(new, log_root, n=1)

    with pytest.raises(WorkspaceMigrateError, match="already exists"):
        migrate_workspace(old, new, log_root=log_root)


def test_migrate_merge_appends_events(tmp_path):
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=3)
    _seed(new, log_root, n=2)

    result = migrate_workspace(old, new, on_collision="merge",
                                log_root=log_root)
    assert result.strategy == "merge"
    new_log = MutationLog(new, log_root=log_root)
    # 2 (existing) + 3 (merged) + 1 (system migration event) = 6
    assert len(list(new_log.replay())) == 6


def test_migrate_archive_existing_moves_target_out_of_way(tmp_path):
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=3)
    _seed(new, log_root, n=2)
    new_hash = folder_hash(new)

    migrate_workspace(old, new, on_collision="archive_existing",
                       log_root=log_root)
    archived = list((log_root / "_archived").glob(f"{new_hash}.*"))
    assert len(archived) == 1
    new_log = MutationLog(new, log_root=log_root)
    # OLD 3 events + 1 migration event
    assert len(list(new_log.replay())) == 4


def test_migrate_updates_workspace_registry(tmp_path):
    from workspaces.workspace_registry import (
        add_known_workspace,
        list_known_workspaces,
    )
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=2)
    new.mkdir()
    add_known_workspace(old, log_root=log_root)
    assert any(w["path"] == str(old.resolve())
               for w in list_known_workspaces(log_root=log_root))

    migrate_workspace(old, new, log_root=log_root)

    paths = [w["path"] for w in list_known_workspaces(log_root=log_root)]
    assert str(old.resolve()) not in paths
    assert str(new.resolve()) in paths


def test_migrate_refuses_identical_hash(tmp_path):
    log_root = tmp_path / "_log_root"
    folder = tmp_path / "same"
    _seed(folder, log_root, n=1)
    with pytest.raises(WorkspaceMigrateError, match="identical"):
        migrate_workspace(folder, folder, log_root=log_root)


def test_migrate_cli_smoke(tmp_path, capsys):
    log_root = tmp_path / "_log_root"
    old = tmp_path / "old"
    new = tmp_path / "new"
    _seed(old, log_root, n=2)
    new.mkdir()

    from workspaces.cli import main
    rc = main([
        "--log-root", str(log_root),
        "workspace", "migrate",
        "--from", str(old),
        "--to", str(new),
        "--operator", "alex",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "migrated workspace log" in out
