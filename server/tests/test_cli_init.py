# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""`workspaces init` — the first-run setup wizard.

Runs against an isolated temp home (LOG_ROOT_DEFAULT monkeypatched) and a stubbed
registry, so the tests never touch the real ~/.workspace or ~/Documents/Workspaces.
"""
from __future__ import annotations

import argparse
import io

import workspaces.cli.impl as impl
import workspaces.workspace_registry as registry


def _run(monkeypatch, tmp_path, *, stdin: str = "", yes=False, dry=False):
    home_log = tmp_path / ".workspace" / "log"
    monkeypatch.setattr(impl, "LOG_ROOT_DEFAULT", home_log)
    calls: dict = {}
    monkeypatch.setattr(registry, "bootstrap_default_workspace",
                        lambda **k: calls.update(k) or {"ok": True})
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = impl.cmd_init(argparse.Namespace(yes=yes, dry_run=dry))
    return rc, out.getvalue(), calls, home_log.parent


def test_init_yes_writes_marker_and_sets_default(monkeypatch, tmp_path):
    rc, out, calls, home = _run(monkeypatch, tmp_path, yes=True)
    assert rc == 0
    assert (home / "init.json").is_file()
    assert (home / "keys").is_dir() and (home / "log").is_dir()
    assert calls.get("target")           # the default workspace was set via the registry
    assert "Setup complete" in out
    assert "§6" in out                   # the agent-hub step is present


def test_init_dry_run_writes_nothing(monkeypatch, tmp_path):
    rc, out, calls, home = _run(monkeypatch, tmp_path, yes=True, dry=True)
    assert rc == 0
    assert not (home / "init.json").exists()
    assert calls == {}                   # dry-run never calls the registry
    assert "dry-run" in out


def test_init_promise_decline_aborts(monkeypatch, tmp_path):
    rc, out, calls, home = _run(monkeypatch, tmp_path, stdin="n\n", yes=False)
    assert rc == 1
    assert not (home / "init.json").exists()   # declined before anything was written
    assert calls == {}
    assert "Not accepted" in out
