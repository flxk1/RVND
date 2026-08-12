# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The pin picker's source is host-INSTALLED plugins (Claude Code / Codex),
not a static catalogue. Skills are authored per plugin repo and installed via
the marketplaces; `discover_installed_skills` reads the host install manifest
and scans each install's skills/ dir. WORKSPACE_HOST_PLUGIN_DIRS isolates the
search root so tests never touch the real ~/.claude/plugins.
"""
from __future__ import annotations

import argparse
import io
import json

import workspaces.pinned_skills as ps
import workspaces.cli.impl as impl


def _make_host(tmp_path, plugins: dict[str, list[str]]) -> str:
    """plugins = {"<plugin>@<marketplace>": ["skillA", ...]}. Build a fake
    host plugins dir (installed_plugins.json + cache tree) and return it."""
    root = tmp_path / ".claude" / "plugins"
    root.mkdir(parents=True)
    manifest: dict = {"version": 2, "plugins": {}}
    for key, skills in plugins.items():
        plugin, mkt = key.split("@", 1)
        inst = root / "cache" / mkt / plugin / "1.0.0"
        for s in skills:
            (inst / "skills" / s).mkdir(parents=True)
            (inst / "skills" / s / "SKILL.md").write_text("# " + s, encoding="utf-8")
        manifest["plugins"][key] = [{
            "scope": "user", "installPath": str(inst), "version": "1.0.0",
            "installedAt": "2026-01-01T00:00:00Z",
            "lastUpdated": "2026-01-01T00:00:00Z"}]
    (root / "installed_plugins.json").write_text(json.dumps(manifest), encoding="utf-8")
    return str(root)


def test_discover_installed_skills_shape_and_ids(monkeypatch, tmp_path):
    root = _make_host(tmp_path, {
        "loomground-solver@loomground": ["analyse-risks", "probability-tracker"]})
    monkeypatch.setenv("WORKSPACE_HOST_PLUGIN_DIRS", root)
    fams = ps.discover_installed_skills()
    assert "loomground-solver" in fams
    assert fams["loomground-solver"]["label"] == "loomground-solver@loomground"
    # canonical <plugin>:<skill> ids, sorted
    assert fams["loomground-solver"]["skills"] == [
        "loomground-solver:analyse-risks", "loomground-solver:probability-tracker"]


def test_discover_skips_dirs_without_skill_md(monkeypatch, tmp_path):
    root = _make_host(tmp_path, {"p@m": ["good"]})
    ip = json.loads((tmp_path / ".claude" / "plugins" / "installed_plugins.json"
                     ).read_text())["plugins"]["p@m"][0]["installPath"]
    from pathlib import Path
    (Path(ip) / "skills" / "notaskill").mkdir()   # a skills dir with no SKILL.md
    monkeypatch.setenv("WORKSPACE_HOST_PLUGIN_DIRS", root)
    assert ps.discover_installed_skills()["p"]["skills"] == ["p:good"]


def test_discover_empty_when_no_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_HOST_PLUGIN_DIRS", str(tmp_path / "absent"))
    assert ps.discover_installed_skills() == {}


def test_discover_multiple_hosts_via_pathsep(monkeypatch, tmp_path):
    import os
    a = _make_host(tmp_path / "hostA", {"pa@m": ["x"]})
    b = _make_host(tmp_path / "hostB", {"pb@m": ["y"]})
    monkeypatch.setenv("WORKSPACE_HOST_PLUGIN_DIRS", a + os.pathsep + b)
    fams = ps.discover_installed_skills()
    assert set(fams) == {"pa", "pb"}


def test_pin_picker_lists_and_pins_installed_skill(monkeypatch, tmp_path):
    root = _make_host(tmp_path, {"loomground-versum@loomground": ["loomground-curate"]})
    monkeypatch.setenv("WORKSPACE_HOST_PLUGIN_DIRS", root)
    monkeypatch.setattr(ps, "load_companion_catalogue", lambda: {})   # no static catalogue
    monkeypatch.setattr(impl, "LOG_ROOT_DEFAULT", tmp_path / "log")
    monkeypatch.setattr(ps, "list_pinned", lambda *a, **k: [])
    pinned: list = []
    monkeypatch.setattr(ps, "pin_skill", lambda folder, sid, **k: pinned.append(sid))
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    ws = tmp_path / "ws"
    ws.mkdir()
    rc = impl._cmd_pin_interactive(
        ws, argparse.Namespace(filter=None, by="test", note="", log_root=None))
    assert rc == 0
    assert pinned == ["loomground-versum:loomground-curate"]
    assert "loomground-versum:loomground-curate" in out.getvalue()


def test_pin_picker_empty_when_nothing_installed(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_HOST_PLUGIN_DIRS", str(tmp_path / "absent"))
    monkeypatch.setattr(ps, "load_companion_catalogue", lambda: {})
    monkeypatch.setattr(impl, "LOG_ROOT_DEFAULT", tmp_path / "log")
    err = io.StringIO()
    monkeypatch.setattr("sys.stderr", err)
    ws = tmp_path / "ws"
    ws.mkdir()
    rc = impl._cmd_pin_interactive(
        ws, argparse.Namespace(filter=None, by="test", note="", log_root=None))
    assert rc == 2
    assert "No installed skills found" in err.getvalue()
