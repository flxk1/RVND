# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the axis-B extension of ``rvnd.context_resolve.resolve_contexts``:
unioning cwd with any DISTINCT foreign registered workspace a structured
file-write targets.

``server/tests/test_resolve_contexts.py`` (pre-existing) pins the historical
singleton-only behaviour for tools this resolver does not read a target from.
These tests cover the new behaviour a ``Write``/``Edit``/``MultiEdit``/
``NotebookEdit`` call adds on top of that, using a temp registry so nothing
here depends on whatever workspaces happen to be registered on the machine
running it.
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
def two_workspaces(tmp_path, monkeypatch):
    """Two registered, DISTINCT workspaces: ``<tmp>/ws-a`` (cwd lives here) and
    ``<tmp>/ws-b`` (a foreign workspace some write could target)."""
    log_root = tmp_path / "log"
    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    (ws_a / "sub").mkdir(parents=True)
    ws_b.mkdir()
    _write_registry(log_root, [ws_a, ws_b])
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    return ws_a, ws_b


def test_write_within_cwd_own_registered_workspace_adds_nothing(two_workspaces):
    ws_a, _ws_b = two_workspaces
    cwd = str(ws_a / "sub")
    target = str(ws_a / "sub" / "file.txt")
    ctxs = CR.resolve_contexts(cwd, "Write", {"file_path": target})
    assert ctxs == (cwd,)


def test_write_to_unregistered_target_adds_nothing(tmp_path):
    cwd = str(tmp_path)  # no registry at all -> everything is unregistered
    target = str(tmp_path / "unregistered" / "file.txt")
    ctxs = CR.resolve_contexts(cwd, "Write", {"file_path": target})
    assert ctxs == (cwd,)


def test_write_to_foreign_registered_workspace_adds_it_after_cwd(two_workspaces):
    ws_a, ws_b = two_workspaces
    cwd = str(ws_a)
    target = str(ws_b / "elsewhere.txt")
    ctxs = CR.resolve_contexts(cwd, "Write", {"file_path": target})
    assert ctxs == (cwd, str(ws_b.resolve()))


@pytest.mark.parametrize("tool_name,field", [
    ("Write", "file_path"), ("Edit", "file_path"),
    ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path"),
])
def test_every_structured_write_tool_reaches_a_foreign_workspace(two_workspaces, tool_name, field):
    ws_a, ws_b = two_workspaces
    cwd = str(ws_a)
    target = str(ws_b / "notebook.ipynb")
    ctxs = CR.resolve_contexts(cwd, tool_name, {field: target})
    assert ctxs == (cwd, str(ws_b.resolve()))


def test_bash_never_reaches_a_target_workspace_even_with_a_path_argument(two_workspaces):
    """Bash is out of scope for resolve_targets; a path-shaped command string
    must not accidentally widen the context set."""
    ws_a, ws_b = two_workspaces
    cwd = str(ws_a)
    ctxs = CR.resolve_contexts(cwd, "Bash", {"command": f"cat {ws_b}/secret.txt"})
    assert ctxs == (cwd,)


def test_resolve_contexts_fails_safe_to_singleton_on_injected_error(two_workspaces, monkeypatch):
    ws_a, ws_b = two_workspaces
    cwd = str(ws_a)

    def boom(*a, **k):
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(CR, "resolve_targets", boom)
    ctxs = CR.resolve_contexts(cwd, "Write", {"file_path": str(ws_b / "x.txt")})
    assert ctxs == (cwd,)


def test_resolve_contexts_fails_safe_when_target_workspace_lookup_errors(two_workspaces, monkeypatch):
    ws_a, ws_b = two_workspaces
    cwd = str(ws_a)

    def boom(path):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(CR, "_target_workspace", boom)
    ctxs = CR.resolve_contexts(cwd, "Write", {"file_path": str(ws_b / "x.txt")})
    assert ctxs == (cwd,)


def test_resolve_contexts_never_raises_on_malformed_tool_input(two_workspaces):
    ws_a, _ws_b = two_workspaces
    cwd = str(ws_a)
    for malformed in (None, "not a dict", ["nope"], 123):
        assert CR.resolve_contexts(cwd, "Write", malformed) == (cwd,)


def test_resolve_contexts_returns_a_tuple_with_cwd_first(two_workspaces):
    ws_a, ws_b = two_workspaces
    cwd = str(ws_a)
    ctxs = CR.resolve_contexts(cwd, "Write", {"file_path": str(ws_b / "x.txt")})
    assert isinstance(ctxs, tuple)
    assert ctxs[0] == cwd
