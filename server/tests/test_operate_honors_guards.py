# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Option 2 — the live run-path HONOURS the reservation's authored `when` guard,
using the loomground engine's own _guard_holds (one guard authority). Before this,
operate() matched reservations by kind only and ignored the guard, so a conditional
reserve (incl. `when tags contains <tag>`) reserved unconditionally.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from workspaces import mcp_server as M


@pytest.fixture
def ws(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "w"; f.mkdir()
    M.workspace_workspace("add", {"folder_context": str(f)})
    M.workspace_policy("party_register", {"folder_context": str(f), "party_id": "bot", "kind": "agent", "actor": "x"})
    return str(f)


def _tags_reserve(ws):
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\nhuman boss role approver\ngate ship risk low grant bot\n"
        "cord bot -> ship\ncord ship -> master\nreserve ship by approver when tags contains non_eu\n"})


def _operate(ws, tags=None):
    issue = {"issue_id": "i1", "issue_type": "ship", "completeness": "high"}
    if tags is not None:
        issue["tags"] = tags
    r = M.workspace_workflow("operate", {"folder_context": ws, "use_case_id": "ship",
        "agent_id": "bot", "issues": [issue], "now_epoch": 1_750_000_000})
    return (r.get("steps") or [{}])[0].get("disposition")


def test_tag_guarded_reserve_fires_only_when_tag_present(ws):
    _tags_reserve(ws)
    assert _operate(ws, tags=["non_eu"]) == "reserved"


def test_tag_guarded_reserve_does_not_fire_without_the_tag(ws):
    """The confirmed bug: before Option 2 this reserved anyway. Now the guard holds it."""
    _tags_reserve(ws)
    assert _operate(ws, tags=["eu"]) != "reserved"
    assert _operate(ws, tags=None) != "reserved"


def test_risk_guarded_reserve_is_honored_too(ws):
    """Option 2 fixes ALL guards, not just tags: a `when risk >= high` reserve on a
    low-risk act does not fire."""
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\nhuman boss role approver\ngate ship risk low grant bot\n"
        "cord bot -> ship\ncord ship -> master\nreserve ship by approver when risk >= high\n"})
    assert _operate(ws) != "reserved"      # low-risk act, guard requires high


def test_unconditional_reserve_still_always_fires(ws):
    """No guard ⇒ binds unconditionally (backward-compatible)."""
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\nhuman boss role approver\ngate ship risk low grant bot\n"
        "cord bot -> ship\ncord ship -> master\nreserve ship by approver\n"})
    assert _operate(ws) == "reserved"
