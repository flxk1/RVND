# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for ``rvnd.context_resolve._target_workspace`` — mapping an absolute
path to the registered governed workspace it is at or under.

Uses a temp registry (a ``known-workspaces.json`` under a scratch log root
pointed at by ``WORKSPACE_L0_LOG_ROOT``) so these tests never depend on
whatever workspaces happen to be registered on the machine running them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rvnd import context_resolve as CR


def _write_registry(log_root: Path, roots: list[Path]) -> None:
    log_root.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "default": "",
        "workspaces": [
            {"path": str(r), "label": "", "added_at": "2026-01-01T00:00:00.000000Z"}
            for r in roots
        ],
    }
    (log_root / "known-workspaces.json").write_text(json.dumps(data))


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A temp registry with one registered workspace, ``<tmp>/ws``. Returns
    ``(ws_root, log_root)``. Every registered root and any probed path is
    created on disk so real-filesystem resolution (symlinks, case-folding)
    behaves the same way it would for a genuine workspace."""
    log_root = tmp_path / "log"
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "sub").mkdir()
    _write_registry(log_root, [ws_root])
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    return ws_root, log_root


def test_target_workspace_maps_descendant_to_registered_root(registry):
    ws_root, _ = registry
    target = ws_root / "sub" / "file.txt"
    assert CR._target_workspace(str(target)) == str(ws_root.resolve())


def test_target_workspace_maps_exact_root(registry):
    ws_root, _ = registry
    assert CR._target_workspace(str(ws_root)) == str(ws_root.resolve())


def test_target_workspace_none_outside_any_registered_workspace(registry, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert CR._target_workspace(str(outside / "f.txt")) is None


def test_target_workspace_rejects_string_prefix_lookalike(tmp_path, monkeypatch):
    """A sibling folder whose name merely EXTENDS a registered root's
    characters (``/ws`` registered, probing under ``/wsEVIL``) must not match
    — this is a component-wise descendant check, never a string prefix."""
    log_root = tmp_path / "log"
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    evil_root = tmp_path / "wsEVIL"
    evil_root.mkdir()
    _write_registry(log_root, [ws_root])
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))

    assert CR._target_workspace(str(evil_root / "secret.txt")) is None
    assert CR._target_workspace(str(evil_root)) is None


def test_target_workspace_picks_the_most_specific_nested_registration(tmp_path, monkeypatch):
    log_root = tmp_path / "log"
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    _write_registry(log_root, [outer, inner])
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))

    target = inner / "f.txt"
    assert CR._target_workspace(str(target)) == str(inner.resolve())


def test_target_workspace_none_on_empty_registry(tmp_path, monkeypatch):
    log_root = tmp_path / "log"
    _write_registry(log_root, [])
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    assert CR._target_workspace(str(tmp_path / "anything")) is None


def test_target_workspace_none_on_missing_registry_file(tmp_path, monkeypatch):
    log_root = tmp_path / "log-does-not-exist"
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    assert CR._target_workspace(str(tmp_path)) is None


def test_target_workspace_tolerates_malformed_registry_entries(tmp_path, monkeypatch):
    log_root = tmp_path / "log"
    log_root.mkdir()
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    data = {
        "version": 1, "default": "",
        "workspaces": [
            {"label": "no path key"},
            {"path": ""},
            {"path": None},
            "not even a dict",
            {"path": str(ws_root)},
        ],
    }
    (log_root / "known-workspaces.json").write_text(json.dumps(data))
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    assert CR._target_workspace(str(ws_root / "x.txt")) == str(ws_root.resolve())


def test_target_workspace_returns_none_when_workspace_registry_unavailable(monkeypatch):
    """If the adapter seam over the workspace registry cannot supply
    ``folder_hash`` (simulating the registry package being unavailable),
    this must degrade to ``None`` rather than raise."""
    import sys
    import types

    dummy = types.ModuleType("rvnd.adapters.workspace")  # no folder_hash attr
    monkeypatch.setitem(sys.modules, "rvnd.adapters.workspace", dummy)
    assert CR._target_workspace("/anywhere/at/all") is None


def test_target_workspace_never_raises_on_malformed_path():
    for bad_path in (None, 123, object(), ""):
        assert CR._target_workspace(bad_path) is None
