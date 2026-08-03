# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""`workspaces backup` / `restore` — capture and recover ~/.workspace.

Runs against an isolated temp home (impl.LOG_ROOT_DEFAULT monkeypatched), so the
tests never touch the real ~/.workspace. Covers a plaintext round-trip, an
encrypted round-trip, the non-clobber guard, dry-run, and the archive
path-traversal defense (tested against the module directly).
"""
from __future__ import annotations

import argparse
import io
import shutil
import tarfile
from pathlib import Path

import pytest

import workspaces.cli.impl as impl
import workspaces.backup as backup


def _make_home(tmp_path):
    home = tmp_path / ".workspace"
    (home / "keys" / "HOSTID").mkdir(parents=True)
    (home / "keys" / "HOSTID" / "identity.priv").write_text("SECRET-KEY", encoding="utf-8")
    (home / "log" / "folderA").mkdir(parents=True)
    (home / "log" / "folderA" / "chain.jsonl").write_text("genesis\nlink1\n", encoding="utf-8")
    (home / "log" / "known-workspaces.json").write_text('{"workspaces":[]}', encoding="utf-8")
    return home


def _run(fn, ns, monkeypatch, home_log):
    monkeypatch.setattr(impl, "LOG_ROOT_DEFAULT", home_log)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = fn(ns)
    return rc, out.getvalue()


def test_backup_restore_roundtrip_plaintext(monkeypatch, tmp_path):
    home = _make_home(tmp_path)
    archive = tmp_path / "bk.tar.gz"

    rc, out = _run(impl.cmd_backup,
                   argparse.Namespace(encrypt=False, out=str(archive)),
                   monkeypatch, home / "log")
    assert rc == 0 and archive.is_file()
    assert "encrypted: NO" in out
    assert not backup.is_encrypted_archive(archive)

    shutil.rmtree(home)                       # simulate a lost machine

    rc, out = _run(impl.cmd_restore,
                   argparse.Namespace(archive=str(archive), force=False, dry_run=False),
                   monkeypatch, home / "log")
    assert rc == 0, out
    assert (home / "keys" / "HOSTID" / "identity.priv").read_text() == "SECRET-KEY"
    assert (home / "log" / "folderA" / "chain.jsonl").read_text() == "genesis\nlink1\n"
    # restored private key must stay owner-only
    mode = (home / "keys" / "HOSTID" / "identity.priv").stat().st_mode & 0o777
    assert mode == 0o600


def test_backup_restore_roundtrip_encrypted(monkeypatch, tmp_path):
    home = _make_home(tmp_path)
    archive = tmp_path / "bk.rvndbackup"
    monkeypatch.setenv("RVND_BACKUP_PASSPHRASE", "correct horse battery staple")

    rc, out = _run(impl.cmd_backup,
                   argparse.Namespace(encrypt=True, out=str(archive)),
                   monkeypatch, home / "log")
    assert rc == 0 and archive.is_file()
    assert "AES-256-GCM" in out
    assert backup.is_encrypted_archive(archive)      # really encrypted on disk

    shutil.rmtree(home)
    rc, out = _run(impl.cmd_restore,
                   argparse.Namespace(archive=str(archive), force=False, dry_run=False),
                   monkeypatch, home / "log")
    assert rc == 0, out
    assert (home / "keys" / "HOSTID" / "identity.priv").read_text() == "SECRET-KEY"


def test_restore_refuses_clobber_without_force(monkeypatch, tmp_path):
    home = _make_home(tmp_path)
    archive = tmp_path / "bk.tar.gz"
    _run(impl.cmd_backup, argparse.Namespace(encrypt=False, out=str(archive)),
         monkeypatch, home / "log")

    # home still exists and is non-empty → restore must refuse without force
    rc, out = _run(impl.cmd_restore,
                   argparse.Namespace(archive=str(archive), force=False, dry_run=False),
                   monkeypatch, home / "log")
    assert rc == 1
    assert "not empty" in out.lower()
    # untouched
    assert (home / "keys" / "HOSTID" / "identity.priv").read_text() == "SECRET-KEY"

    # with force: existing is moved aside, not deleted
    rc, out = _run(impl.cmd_restore,
                   argparse.Namespace(archive=str(archive), force=True, dry_run=False),
                   monkeypatch, home / "log")
    assert rc == 0, out
    baks = list(tmp_path.glob(".workspace.bak-*"))
    assert baks, "existing home should have been moved to a .bak, not deleted"


def test_restore_dry_run_writes_nothing(monkeypatch, tmp_path):
    home = _make_home(tmp_path)
    archive = tmp_path / "bk.tar.gz"
    _run(impl.cmd_backup, argparse.Namespace(encrypt=False, out=str(archive)),
         monkeypatch, home / "log")
    shutil.rmtree(home)

    rc, out = _run(impl.cmd_restore,
                   argparse.Namespace(archive=str(archive), force=False, dry_run=True),
                   monkeypatch, home / "log")
    assert rc == 0
    assert "dry-run" in out.lower()
    assert not home.exists()                  # nothing written


def test_restore_rejects_path_traversal(tmp_path):
    # A malicious archive whose member escapes the target must be refused.
    import json
    evil = tmp_path / "evil.tar.gz"
    manifest = json.dumps({"schema": "rvnd-backup/1"}).encode()
    with tarfile.open(str(evil), "w:gz") as tar:
        mi = tarfile.TarInfo("MANIFEST.json"); mi.size = len(manifest)
        tar.addfile(mi, io.BytesIO(manifest))
        payload = b"pwned"
        ev = tarfile.TarInfo("workspace/../../escape.txt"); ev.size = len(payload)
        tar.addfile(ev, io.BytesIO(payload))

    with pytest.raises(backup.BackupError, match="unsafe path"):
        backup.restore_backup(evil, tmp_path / "target", force=True)
    assert not (tmp_path / "escape.txt").exists()
