# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the QoL pack (multi-skill-builder proposals #4a, #7, #8, 2026-05-22).

- ``pin_skills_to_folder(folder, skill_ids[])`` — bulk pin.
- ``dispatch_skill_dry_run`` — resolves+returns body without writing audit.
- ``list_plugin_skills(plugin_id)`` — surface a whole skill family without
  needing folder pins.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path


from workspaces.mutation_log import MutationLog
from workspaces.pinned_skills import pin_skill


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _fresh_mcp(monkeypatch, log_root: Path):
    import workspaces.mcp_server as srv
    importlib.reload(srv)
    monkeypatch.setattr("workspaces.mcp_serving._log_root", lambda: log_root)
    return srv


# ---------------------------------------------------------------------------
# pin_skills_to_folder (proposal #4a)
# ---------------------------------------------------------------------------


def test_bulk_pin_three_skills(tmp_path, monkeypatch):
    """Three valid skill ids pinned in one call."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.pin_skills_to_folder(
        folder_context=str(folder),
        skill_ids=["p:a", "p:b", "p:c"],
        pinned_by="install",
    )
    assert out["ok"] is True, out
    assert out["pinned_count"] == 3
    assert out["total_pinned"] == 3
    assert all(r["ok"] for r in out["per_skill"])

    # Re-listing via list_pinned_skills must show all three
    listed = srv.list_pinned_skills(folder_context=str(folder))
    pinned_ids = sorted(s["id"] for s in listed["skills"])
    assert pinned_ids == ["p:a", "p:b", "p:c"]


def test_bulk_pin_idempotent_at_per_skill_level(tmp_path, monkeypatch):
    """Re-pinning an already-pinned id is not an error — last-write-wins on metadata."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    srv.pin_skills_to_folder(
        folder_context=str(folder), skill_ids=["p:a", "p:b"],
        pinned_by="install",
    )
    out2 = srv.pin_skills_to_folder(
        folder_context=str(folder), skill_ids=["p:a", "p:c"],
        pinned_by="install-2",
    )
    assert out2["ok"]
    # total_pinned reflects union {a, b, c}, not 2
    assert out2["total_pinned"] == 3


def test_bulk_pin_empty_skill_id_in_list_is_per_row_failure(tmp_path, monkeypatch):
    """An empty id in the list is reported per-row; batch continues."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.pin_skills_to_folder(
        folder_context=str(folder),
        skill_ids=["", "p:b"],
    )
    assert out["ok"] is True
    assert out["pinned_count"] == 1
    assert out["per_skill"][0]["ok"] is False
    assert out["per_skill"][1]["ok"] is True


def test_bulk_pin_empty_list_rejected(tmp_path, monkeypatch):
    """An empty list is a batch-level error."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.pin_skills_to_folder(
        folder_context=str(folder), skill_ids=[],
    )
    assert out["ok"] is False
    assert "non-empty" in out["error"]


# ---------------------------------------------------------------------------
# dispatch_skill_dry_run (proposal #7)
# ---------------------------------------------------------------------------


def test_dry_run_resolves_workspace_skill_without_writing_audit(tmp_path, monkeypatch):
    """Dry-run a workspace:* skill — no event lands in the mutation log."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.dispatch_skill_dry_run(
        folder_context=str(folder),
        skill_id="workspace:workspace-policy",
        query="show",
    )
    assert out["ok"] is True, out
    assert out["dry_run"] is True
    assert out["audit_id"] == ""
    assert out["provenance"] == "system"
    if not out["body"]:
        import pytest
        pytest.skip("plugin/ archived to companion; workspace skill SKILL.md not in core")
    assert out["body"]  # non-empty body for a real workspace skill

    # No skill-dispatch events should be on disk
    log = MutationLog(folder, log_root=log_root)
    dispatches = [e for e in log.replay() if e.pair_id == "skill-dispatch"]
    assert dispatches == [], dispatches


def test_dry_run_respects_pinned_set_for_external_skills(tmp_path, monkeypatch):
    """Dry-run of a non-workspace unpinned skill returns the same error as live dispatch."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.dispatch_skill_dry_run(
        folder_context=str(folder),
        skill_id="other:thing",
    )
    assert out["ok"] is False
    assert "not in resolved pinned set" in out["error"]
    assert out["dry_run"] is True


def test_dry_run_does_not_pollute_audit_log_even_when_pinned(tmp_path, monkeypatch):
    """A pinned non-workspace skill dry-run also writes nothing."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    pin_skill(str(folder), "test:plug", log_root=log_root)
    out = srv.dispatch_skill_dry_run(
        folder_context=str(folder),
        skill_id="test:plug",
        query="hello",
    )
    assert out["ok"] is True
    assert out["audit_id"] == ""
    log = MutationLog(folder, log_root=log_root)
    dispatches = [e for e in log.replay() if e.pair_id == "skill-dispatch"]
    assert dispatches == []


def test_dry_run_empty_skill_id_rejected(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.dispatch_skill_dry_run(
        folder_context=str(folder), skill_id="",
    )
    assert out["ok"] is False
    assert "required" in out["error"]


# ---------------------------------------------------------------------------
# list_plugin_skills (proposal #8)
# ---------------------------------------------------------------------------


def test_list_plugin_skills_returns_workspace_family(tmp_path, monkeypatch):
    """Asking for `workspace` returns the full workspace:* family."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.list_plugin_skills(plugin_id="workspace")
    if not out.get("ok"):
        import pytest
        pytest.skip("plugin/ archived to companion; workspace skill catalogue not in core")
    assert out["ok"] is True, out
    assert out["plugin_id"] == "workspace"
    # Every returned id is prefixed with workspace:
    for sid in out["skills"]:
        assert sid.startswith("workspace:"), sid
    # At least 4 skills (workspace has policy, workspace, capture, lock...)
    assert len(out["skills"]) >= 4


def test_list_plugin_skills_unknown_plugin(tmp_path, monkeypatch):
    """Unknown plugin id surfaces a clean error."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.list_plugin_skills(plugin_id="not-a-real-plugin-9999")
    assert out["ok"] is False
    assert "not found in skill catalogue" in out["error"]


def test_list_plugin_skills_empty_plugin_id(tmp_path, monkeypatch):
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.list_plugin_skills(plugin_id="")
    assert out["ok"] is False
    assert "required" in out["error"]


# ---------------------------------------------------------------------------
# All three listed in server_info
# ---------------------------------------------------------------------------


def test_qol_tools_advertised_by_server_info(tmp_path, monkeypatch):
    """0.6.6+: the QoL tools collapsed into the workspace_dispatch facade
    (ops pin_many / dry_run / list_plugin); facade declared, ops reachable."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    tools = set(srv.server_info()["tools"])
    assert "workspace_dispatch" in tools, tools
    ops = {o["op"] for o in srv.workspace_dispatch("help")["ops"]}
    assert {"pin_many", "dry_run", "list_plugin"} <= ops, ops
