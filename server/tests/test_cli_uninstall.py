# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""`workspaces uninstall` — the guided removal, mirror of `init`.

Runs against an isolated temp home (LOG_ROOT_DEFAULT monkeypatched), so the
tests never touch the real ~/.workspace. The contract under test is the safety
rule: the audit chains / signing keys home is KEPT unless the user types DELETE,
and never removed under --yes; only RVND's own init marker goes without asking.
"""
from __future__ import annotations

import argparse
import io
import json

import workspaces.cli.impl as impl


def _run(monkeypatch, tmp_path, *, stdin: str = "", yes=False, dry=False):
    home = tmp_path / ".workspace"
    (home / "keys").mkdir(parents=True)
    (home / "log").mkdir(parents=True)
    ws_home = tmp_path / "MyWorkspaces"
    (home / "init.json").write_text(
        json.dumps({"initialized_at": "x", "workspaces_home": str(ws_home),
                    "promise_accepted": True}), encoding="utf-8")
    monkeypatch.setattr(impl, "LOG_ROOT_DEFAULT", home / "log")
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = impl.cmd_uninstall(argparse.Namespace(yes=yes, dry_run=dry))
    return rc, out.getvalue(), home, ws_home


def test_uninstall_yes_keeps_data_removes_marker(monkeypatch, tmp_path):
    rc, out, home, _ = _run(monkeypatch, tmp_path, yes=True)
    assert rc == 0
    assert home.is_dir()                      # keys + chains kept
    assert (home / "keys").is_dir()
    assert not (home / "init.json").exists()  # only the throwaway marker went
    assert "KEEPING it" in out
    # the disconnect guidance is always shown
    assert "claude mcp remove rvnd-governance" in out


def test_uninstall_dry_run_removes_nothing(monkeypatch, tmp_path):
    rc, out, home, _ = _run(monkeypatch, tmp_path, dry=True)
    assert rc == 0
    assert (home / "init.json").exists()      # nothing removed at all
    assert home.is_dir()
    assert "dry-run" in out


def test_uninstall_typed_delete_removes_home(monkeypatch, tmp_path):
    rc, out, home, _ = _run(monkeypatch, tmp_path, stdin="DELETE\n")
    assert rc == 0
    assert not home.exists()                   # the whole ~/.workspace is gone
    assert "removed" in out


def test_uninstall_declined_keeps_home(monkeypatch, tmp_path):
    # anything other than the exact word DELETE keeps the data
    rc, out, home, _ = _run(monkeypatch, tmp_path, stdin="yes\n")
    assert rc == 0
    assert home.is_dir()
    assert (home / "keys").is_dir()


def test_uninstall_never_deletes_user_workspaces(monkeypatch, tmp_path):
    # even the strongest confirm only removes ~/.workspace, never the user's
    # governed workspaces folder.
    ws = tmp_path / "MyWorkspaces"
    ws.mkdir()
    (ws / "keepme.txt").write_text("mine", encoding="utf-8")
    rc, out, _, ws_home = _run(monkeypatch, tmp_path, stdin="DELETE\n")
    assert rc == 0
    assert ws.is_dir() and (ws / "keepme.txt").exists()
    assert str(ws_home) in out                 # it is pointed to, not deleted
