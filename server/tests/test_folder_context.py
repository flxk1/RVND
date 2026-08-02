# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the skill-runtime folder_context injection layer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from workspaces import (
    WorkspaceMemory,
    NoFolderContextError,
    UNSCOPED_SENTINEL,
    current_folder,
    folder_context,
    reset_folder,
    resolve_folder_context,
    set_folder,
    with_folder_context,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with no env var and no contextvar set."""
    monkeypatch.delenv("WORKSPACE_FOLDER_CONTEXT", raising=False)
    yield


# ===========================================================================
# current_folder + set_folder
# ===========================================================================


def test_current_folder_returns_none_by_default():
    assert current_folder() is None


def test_set_folder_returns_token(tmp_path):
    token = set_folder(tmp_path)
    try:
        assert current_folder() == str(tmp_path.resolve())
    finally:
        reset_folder(token)
    assert current_folder() is None


def test_set_folder_resolves_path(tmp_path, monkeypatch):
    """A relative path is resolved against the current working directory."""
    monkeypatch.chdir(tmp_path)
    token = set_folder(".")
    try:
        assert current_folder() == str(tmp_path.resolve())
    finally:
        reset_folder(token)


def test_set_folder_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    token = set_folder("~/subfolder")
    try:
        assert current_folder() == str((tmp_path / "subfolder").resolve())
    finally:
        reset_folder(token)


# ===========================================================================
# env-var fallback
# ===========================================================================


def test_env_var_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_FOLDER_CONTEXT", str(tmp_path))
    assert current_folder() == str(tmp_path)


def test_contextvar_overrides_env(monkeypatch, tmp_path):
    """Explicit contextvar wins over the env var fallback."""
    env_path = tmp_path / "env"
    var_path = tmp_path / "var"
    env_path.mkdir()
    var_path.mkdir()
    monkeypatch.setenv("WORKSPACE_FOLDER_CONTEXT", str(env_path))
    token = set_folder(var_path)
    try:
        assert current_folder() == str(var_path.resolve())
    finally:
        reset_folder(token)
    # After reset, env-var fallback kicks back in.
    assert current_folder() == str(env_path)


# ===========================================================================
# Context manager
# ===========================================================================


def test_context_manager_sets_and_resets(tmp_path):
    assert current_folder() is None
    with folder_context(tmp_path):
        assert current_folder() == str(tmp_path.resolve())
    assert current_folder() is None


def test_context_manager_nested(tmp_path):
    outer = tmp_path / "outer"
    inner = tmp_path / "inner"
    outer.mkdir()
    inner.mkdir()
    with folder_context(outer):
        assert current_folder() == str(outer.resolve())
        with folder_context(inner):
            assert current_folder() == str(inner.resolve())
        # Inner exit restores outer.
        assert current_folder() == str(outer.resolve())
    assert current_folder() is None


def test_context_manager_exit_on_exception(tmp_path):
    """An exception inside the with block still resets the context."""
    try:
        with folder_context(tmp_path):
            raise ValueError("boom")
    except ValueError:
        pass
    assert current_folder() is None


# ===========================================================================
# Decorator
# ===========================================================================


def test_decorator_sets_context_during_call(tmp_path):
    captured = {}

    @with_folder_context("folder")
    def my_skill(*, folder):
        captured["seen"] = current_folder()

    my_skill(folder=tmp_path)
    assert captured["seen"] == str(tmp_path.resolve())
    # Outside the call, the context is back to default.
    assert current_folder() is None


def test_decorator_noop_when_arg_absent(tmp_path):
    captured = {}

    @with_folder_context("folder")
    def my_skill(*, folder=None):
        captured["seen"] = current_folder()

    my_skill()
    assert captured["seen"] is None


def test_decorator_custom_arg_name(tmp_path):
    captured = {}

    @with_folder_context("vault")
    def my_skill(*, vault):
        captured["seen"] = current_folder()

    my_skill(vault=tmp_path)
    assert captured["seen"] == str(tmp_path.resolve())


# ===========================================================================
# resolve_folder_context — the helper WorkspaceMemory uses
# ===========================================================================


def test_resolve_explicit_wins(tmp_path):
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    with folder_context(tmp_path / "from-context-var"):
        result = resolve_folder_context(explicit)
    assert result == str(explicit.resolve())


def test_resolve_falls_back_to_contextvar(tmp_path):
    with folder_context(tmp_path):
        result = resolve_folder_context(None)
    assert result == str(tmp_path.resolve())


def test_resolve_falls_back_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_FOLDER_CONTEXT", str(tmp_path))
    result = resolve_folder_context(None)
    assert result == str(tmp_path)


def test_resolve_raises_when_unset():
    with pytest.raises(NoFolderContextError):
        resolve_folder_context(None)


def test_resolve_unscoped_when_allowed():
    with pytest.warns(RuntimeWarning, match="unscoped"):
        result = resolve_folder_context(None, allow_unscoped=True)
    assert "unscoped" in result
    assert str(Path.home() / ".workspace") in result


# ===========================================================================
# WorkspaceMemory integration with folder_context
# ===========================================================================


def test_l0memory_uses_contextvar(tmp_path):
    log_root = tmp_path / "logs"
    folder = tmp_path / "vault"
    folder.mkdir()
    with folder_context(folder):
        mem = WorkspaceMemory(log_root=log_root)
        assert mem.folder_context == str(folder.resolve())


def test_l0memory_explicit_overrides_context(tmp_path):
    log_root = tmp_path / "logs"
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    with folder_context(a):
        mem = WorkspaceMemory(b, log_root=log_root)
        assert mem.folder_context == str(b.resolve())


def test_l0memory_raises_without_context(tmp_path):
    log_root = tmp_path / "logs"
    with pytest.raises(NoFolderContextError):
        WorkspaceMemory(log_root=log_root)


def test_l0memory_unscoped_emits_warning(tmp_path):
    log_root = tmp_path / "logs"
    with pytest.warns(RuntimeWarning, match="unscoped"):
        mem = WorkspaceMemory(log_root=log_root, allow_unscoped=True)
    assert "unscoped" in mem.folder_context


def test_l0memory_env_var_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_FOLDER_CONTEXT", str(tmp_path))
    mem = WorkspaceMemory(log_root=tmp_path / "logs")
    assert mem.folder_context == str(tmp_path)


# ===========================================================================
# End-to-end: scoped skill via context manager
# ===========================================================================


def test_scoped_skill_writes_to_correct_folder(tmp_path):
    """A skill wrapped in `with folder_context(...)` writes to that folder."""
    log_root = tmp_path / "logs"
    hr = tmp_path / "HR"
    hr.mkdir()
    eng = tmp_path / "Engineering"
    eng.mkdir()

    def my_skill(query: str) -> str:
        mem = WorkspaceMemory(log_root=log_root)
        return mem.remember({
            "id": f"sha256:p-{query}",
            "problem": {"id": f"sha256:problem-{query}", "scope": "test",
                        "type": "test", "summary": query, "facets": {}},
            "solution": {"id": f"sha256:p-{query}", "problem_id": f"sha256:problem-{query}",
                         "body": "x", "body_format": "prose",
                         "authority_tier": 3, "confidence": 0.9},
        })

    with folder_context(hr):
        my_skill("hr query")
    with folder_context(eng):
        my_skill("eng query")

    # HR sees only its own pair. Engineering sees only its own.
    with folder_context(hr):
        hr_mem = WorkspaceMemory(log_root=log_root)
        hr_summaries = {p["problem"]["summary"] for p in hr_mem.all_pairs()}
    with folder_context(eng):
        eng_mem = WorkspaceMemory(log_root=log_root)
        eng_summaries = {p["problem"]["summary"] for p in eng_mem.all_pairs()}

    assert hr_summaries == {"hr query"}
    assert eng_summaries == {"eng query"}


def test_scoped_skill_via_decorator(tmp_path):
    """Same end-to-end test, but via the decorator instead of the context manager."""
    log_root = tmp_path / "logs"
    hr = tmp_path / "HR"
    hr.mkdir()

    @with_folder_context("folder")
    def my_skill(query: str, *, folder: str) -> str:
        mem = WorkspaceMemory(log_root=log_root)
        return mem.remember({
            "id": f"sha256:p-{query}",
            "problem": {"id": f"sha256:p-{query}", "scope": "test",
                        "type": "test", "summary": query, "facets": {}},
            "solution": {"id": f"sha256:p-{query}", "problem_id": f"sha256:p-{query}",
                         "body": "x", "body_format": "prose",
                         "authority_tier": 3, "confidence": 0.9},
        })

    my_skill("hr query", folder=str(hr))

    with folder_context(hr):
        mem = WorkspaceMemory(log_root=log_root)
        summaries = {p["problem"]["summary"] for p in mem.all_pairs()}
    assert summaries == {"hr query"}


def test_nested_scopes_write_to_correct_folder(tmp_path):
    """An inner context overrides the outer; the inner's write goes to the inner."""
    log_root = tmp_path / "logs"
    outer = tmp_path / "outer"
    inner = tmp_path / "outer" / "inner"
    outer.mkdir()
    inner.mkdir()

    with folder_context(outer):
        with folder_context(inner):
            mem = WorkspaceMemory(log_root=log_root)
            mem.remember({
                "id": "sha256:p1",
                "problem": {"id": "sha256:p1", "scope": "test", "type": "test",
                            "summary": "inner pair", "facets": {}},
                "solution": {"id": "sha256:p1", "problem_id": "sha256:p1",
                             "body": "x", "body_format": "prose",
                             "authority_tier": 3, "confidence": 0.9},
            })

    # Outer's view sees the descendant. Inner's view sees itself.
    with folder_context(outer):
        outer_mem = WorkspaceMemory(log_root=log_root)
        outer_summaries = {p["problem"]["summary"] for p in outer_mem.all_pairs()}
    with folder_context(inner):
        inner_mem = WorkspaceMemory(log_root=log_root)
        inner_summaries = {p["problem"]["summary"] for p in inner_mem.all_pairs()}

    assert "inner pair" in outer_summaries
    assert "inner pair" in inner_summaries
