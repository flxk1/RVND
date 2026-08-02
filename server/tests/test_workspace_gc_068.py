# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B7.2 (0.6.8) — workspaces workspace gc --orphans."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from workspaces.mutation_log import LogEvent, MutationLog, folder_hash
from workspaces.workspace_migrate import gc_orphans


def _seed(folder: Path, log_root: Path, n: int = 2) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    log = MutationLog(folder, log_root=log_root)
    for i in range(n):
        log.append(LogEvent(event="ingest", folder_path=str(folder),
                            pair_id=f"pair-{i}"))
    return folder_hash(folder)


def test_gc_lists_live_and_orphan_dirs(tmp_path):
    log_root = tmp_path / "_log_root"
    live = tmp_path / "live"
    gone = tmp_path / "gone"
    live_hash = _seed(live, log_root)
    gone_hash = _seed(gone, log_root)
    # Now remove the source dir for `gone` so it becomes an orphan.
    shutil.rmtree(gone)

    results = gc_orphans(log_root=log_root)
    by_hash = {r.folder_hash: r for r in results}
    assert by_hash[live_hash].action == "ok"
    assert by_hash[gone_hash].action == "orphan"
    assert by_hash[live_hash].event_count == 2
    assert by_hash[gone_hash].recovered_path == str(gone.resolve())


def test_gc_archive_moves_orphans(tmp_path):
    log_root = tmp_path / "_log_root"
    gone = tmp_path / "gone"
    gone_hash = _seed(gone, log_root)
    shutil.rmtree(gone)

    results = gc_orphans(log_root=log_root, archive=True)
    by_hash = {r.folder_hash: r for r in results}
    assert by_hash[gone_hash].action == "archived"
    # Original orphan dir is gone; an archived copy exists.
    assert not (log_root / gone_hash).exists()
    archived = list((log_root / "_archived").glob(f"{gone_hash}.*"))
    assert len(archived) == 1


def test_gc_delete_removes_orphans(tmp_path):
    log_root = tmp_path / "_log_root"
    gone = tmp_path / "gone"
    gone_hash = _seed(gone, log_root)
    shutil.rmtree(gone)

    results = gc_orphans(log_root=log_root, delete=True)
    by_hash = {r.folder_hash: r for r in results}
    assert by_hash[gone_hash].action == "deleted"
    assert not (log_root / gone_hash).exists()


def test_gc_cli_requires_orphans_flag(tmp_path, capsys):
    log_root = tmp_path / "_log_root"
    log_root.mkdir()
    from workspaces.cli import main
    rc = main(["--log-root", str(log_root), "workspace", "gc"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "--orphans" in err


def test_gc_cli_delete_requires_yes_i_mean_it(tmp_path, capsys):
    log_root = tmp_path / "_log_root"
    log_root.mkdir()
    from workspaces.cli import main
    rc = main(["--log-root", str(log_root), "workspace", "gc",
               "--orphans", "--delete"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "yes-i-mean-it" in err


def test_gc_cli_json_output(tmp_path, capsys):
    log_root = tmp_path / "_log_root"
    gone = tmp_path / "gone"
    _seed(gone, log_root)
    shutil.rmtree(gone)

    from workspaces.cli import main
    rc = main(["--log-root", str(log_root), "workspace", "gc",
               "--orphans", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert any(r["action"] == "orphan" for r in data)
