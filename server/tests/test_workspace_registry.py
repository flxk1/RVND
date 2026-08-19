# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the workspace registry + default-workspace bootstrap (#134, #154)."""

from __future__ import annotations



from rvnd.workspace_registry import (
    add_known_workspace,
    bootstrap_default_workspace,
    list_known_workspaces,
    load_registry,
    remove_known_workspace,
)


def test_add_known_workspace_creates_record(tmp_path):
    log = tmp_path / "log"
    folder = tmp_path / "wks"; folder.mkdir()
    r = add_known_workspace(str(folder), label="contracts", log_root=log)
    assert r["total"] == 1
    ws = list_known_workspaces(log_root=log)
    assert len(ws) == 1
    assert ws[0]["path"] == str(folder.resolve())
    assert ws[0]["label"] == "contracts"


def test_add_known_workspace_idempotent(tmp_path):
    log = tmp_path / "log"
    folder = tmp_path / "wks"; folder.mkdir()
    add_known_workspace(str(folder), label="a", log_root=log)
    add_known_workspace(str(folder), label="b", log_root=log)
    ws = list_known_workspaces(log_root=log)
    assert len(ws) == 1
    assert ws[0]["label"] == "b"   # last write wins


def test_remove_known_workspace(tmp_path):
    log = tmp_path / "log"
    folder = tmp_path / "wks"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log)
    assert remove_known_workspace(str(folder), log_root=log) is True
    assert list_known_workspaces(log_root=log) == []
    # Removing again returns False
    assert remove_known_workspace(str(folder), log_root=log) is False


def test_list_when_no_registry_file(tmp_path):
    log = tmp_path / "log"
    assert list_known_workspaces(log_root=log) == []
    data = load_registry(log_root=log)
    assert data["default"] == ""


def test_bootstrap_creates_target_when_missing(tmp_path):
    log = tmp_path / "log"
    target = tmp_path / "Workspaces"
    assert not target.exists()
    out = bootstrap_default_workspace(target=str(target), log_root=log)
    assert out["ok"] is True
    assert out["created"] is True
    assert target.exists()
    # Registry now lists it as default
    data = load_registry(log_root=log)
    assert data["default"] == str(target.resolve())
    assert any(w["label"] == "default" for w in data["workspaces"])


def test_bootstrap_idempotent_when_target_exists(tmp_path):
    log = tmp_path / "log"
    target = tmp_path / "Workspaces"; target.mkdir()
    # Target already exists at first call — created flag is False both times
    out1 = bootstrap_default_workspace(target=str(target), log_root=log)
    out2 = bootstrap_default_workspace(target=str(target), log_root=log)
    assert out1["created"] is False
    assert out2["created"] is False
    # Single entry in workspaces (idempotent registry side too)
    ws = list_known_workspaces(log_root=log)
    paths = [w["path"] for w in ws]
    assert paths.count(str(target.resolve())) == 1


def test_bootstrap_records_default_pointer(tmp_path):
    log = tmp_path / "log"
    target = tmp_path / "Workspaces"
    out = bootstrap_default_workspace(target=str(target), log_root=log)
    data = load_registry(log_root=log)
    assert data["default"] == out["path"]


def test_remove_clears_default_pointer(tmp_path):
    log = tmp_path / "log"
    target = tmp_path / "Workspaces"
    bootstrap_default_workspace(target=str(target), log_root=log)
    remove_known_workspace(str(target), log_root=log)
    data = load_registry(log_root=log)
    assert data["default"] == ""
