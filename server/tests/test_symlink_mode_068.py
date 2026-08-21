# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B6.3 (0.6.8) — WORKSPACE_SYMLINK_MODE env var.

follow (default) — Path.resolve(): symlink dereferenced; two paths to the
                   same target share workspace identity.
isolate          — Path.absolute(): symlink kept distinct; two paths to
                   the same target get two workspace identities.
"""

from __future__ import annotations


import pytest

from rvnd.folder_context import (
    _resolve_with_symlink_policy,
    symlink_mode,
)
from rvnd.mutation_log import folder_hash


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("WORKSPACE_SYMLINK_MODE", raising=False)
    yield


def _make_symlink_pair(tmp_path):
    """Create real-dir + symlink-dir on the same target. Returns (real, link)."""
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/filesystem")
    return target, link


def test_symlink_mode_follow_resolves_to_target(tmp_path, monkeypatch):
    real, link = _make_symlink_pair(tmp_path)
    # default = follow
    assert symlink_mode() == "follow"
    resolved_real = _resolve_with_symlink_policy(real)
    resolved_link = _resolve_with_symlink_policy(link)
    assert resolved_real == resolved_link
    # And both produce the same folder_hash.
    assert folder_hash(real) == folder_hash(link)


def test_symlink_mode_isolate_keeps_symlink_path_distinct_from_target(
    tmp_path, monkeypatch,
):
    real, link = _make_symlink_pair(tmp_path)
    monkeypatch.setenv("WORKSPACE_SYMLINK_MODE", "isolate")
    assert symlink_mode() == "isolate"
    resolved_real = _resolve_with_symlink_policy(real)
    resolved_link = _resolve_with_symlink_policy(link)
    assert resolved_real != resolved_link
    # And the two paths now key to DIFFERENT workspace identities.
    assert folder_hash(real) != folder_hash(link)


def test_symlink_mode_status_displays_current_setting(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORKSPACE_SYMLINK_MODE", "isolate")
    monkeypatch.setenv("WORKSPACE_FOLDER_CONTEXT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "_home"))
    (tmp_path / "_home").mkdir(exist_ok=True)

    # Run `workspaces status --json` against a clean tmp workspace.
    from rvnd.cli import main
    rc = main(["--log-root", str(tmp_path / "_log_root"),
               "status", "--folder", str(tmp_path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    import json as _json
    snap = _json.loads(out)
    assert snap["symlink_mode"] == "isolate"
