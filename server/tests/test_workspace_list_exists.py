# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""`workspace_workspace(list)` tags each row with `exists`.

The console uses this to hide workspaces whose folder is gone (deleted temp
dirs, unmounted drives) without deleting anything from the registry. The flag
is a live, non-destructive signal computed at list time — the registry is never
mutated, so a remounted drive's workspace reappears on the next list.

These tests are hermetic: a fresh registry via WORKSPACE_L0_LOG_ROOT and a
cleared request principal, so full-suite ordering (a principal left set by an
earlier test would otherwise scope our rows out of the list) can't affect them.
"""
from __future__ import annotations

import pytest

from rvnd import mcp_server
import rvnd.mcp_serving as serving
from rvnd.registry import add_known_workspace


@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    reg.mkdir()
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(reg))
    serving.clear_request_principal()   # local-operator mode: unscoped full list
    return reg


def test_list_flags_present_and_missing_folders(tmp_path, registry):
    present = tmp_path / "here"
    present.mkdir()
    absent = tmp_path / "gone"          # deliberately never created
    add_known_workspace(present, label="present-ws", log_root=registry)
    add_known_workspace(absent, label="absent-ws", log_root=registry)

    res = mcp_server.list_known_workspaces()
    assert res["ok"] is True
    by_label = {w.get("label"): w for w in res["workspaces"]}

    assert by_label["present-ws"]["exists"] is True
    assert by_label["absent-ws"]["exists"] is False


def test_list_does_not_delete_missing_from_registry(tmp_path, registry):
    # The flag is non-destructive: a missing folder is still LISTED (just tagged
    # exists=False), never dropped from the registry. Recreating the folder
    # flips the flag back without any re-registration.
    ws = tmp_path / "roundtrip"
    ws.mkdir()
    add_known_workspace(ws, label="roundtrip-ws", log_root=registry)

    ws.rmdir()                          # folder goes away (e.g. unmounted)
    res1 = mcp_server.list_known_workspaces()
    row1 = next(w for w in res1["workspaces"] if w.get("label") == "roundtrip-ws")
    assert row1["exists"] is False      # still present in the list, tagged missing

    ws.mkdir()                          # folder comes back
    res2 = mcp_server.list_known_workspaces()
    row2 = next(w for w in res2["workspaces"] if w.get("label") == "roundtrip-ws")
    assert row2["exists"] is True       # reappears as present — no re-registration
