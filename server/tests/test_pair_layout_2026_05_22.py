# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the KG-viz layout-persistence MCP tools (2026-05-22).

The Workspace dashboard's force-directed pair graph lets the user drag
nodes into hand-curated positions. Two new tools persist and hydrate
those positions via the folder's mutation log:

- ``set_pair_layout(pair_id, folder_context, x, y)`` writes one
  ``system`` LogEvent carrying ``extra.layout = {x, y, at}``.
- ``get_pair_layouts(folder_context)`` walks the log and returns
  latest-wins coordinates keyed by ``pair_id``.

Latest-wins means a user can drag a node, move it again, and only the
final position is hydrated on the next session.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path



UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _fresh_mcp(monkeypatch, log_root: Path):
    """Reload mcp_server and patch _log_root to the test log."""
    import workspaces.mcp_server as srv
    importlib.reload(srv)
    monkeypatch.setattr("workspaces.mcp_serving._log_root", lambda: log_root)
    return srv


# ---------------------------------------------------------------------------
# set_pair_layout
# ---------------------------------------------------------------------------


def test_set_pair_layout_writes_event_and_returns_audit_id(tmp_path, monkeypatch):
    """set_pair_layout returns a UUID audit_id and persists the coordinates."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.set_pair_layout(
        pair_id="sha256:p1",
        folder_context=str(folder),
        x=123.4,
        y=56.7,
    )
    assert out["ok"] is True, out
    assert UUID_RE.match(out["audit_id"]), out["audit_id"]
    assert out["x"] == 123.4
    assert out["y"] == 56.7
    assert out["pair_id"] == "sha256:p1"
    assert out["folder_context"] == str(folder.resolve())


def test_set_pair_layout_requires_pair_id(tmp_path, monkeypatch):
    """Empty pair_id is rejected cleanly."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.set_pair_layout(
        pair_id="",
        folder_context=str(folder),
        x=0.0,
        y=0.0,
    )
    assert out["ok"] is False
    assert "pair_id is required" in out["error"]


def test_set_pair_layout_requires_folder_context(tmp_path, monkeypatch):
    """Empty folder_context is rejected cleanly."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.set_pair_layout(
        pair_id="sha256:p1",
        folder_context="",
        x=0.0,
        y=0.0,
    )
    assert out["ok"] is False
    assert "folder_context is required" in out["error"]


def test_set_pair_layout_rejects_non_numeric_coords(tmp_path, monkeypatch):
    """Non-numeric x/y surfaces a clean error."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.set_pair_layout(
        pair_id="sha256:p1",
        folder_context=str(folder),
        x="not-a-number",
        y=10.0,
    )
    assert out["ok"] is False
    assert "numeric" in out["error"]


# ---------------------------------------------------------------------------
# get_pair_layouts
# ---------------------------------------------------------------------------


def test_get_pair_layouts_empty_when_no_events(tmp_path, monkeypatch):
    """A folder with no layout events returns an empty mapping."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.get_pair_layouts(folder_context=str(folder))
    assert out["ok"] is True, out
    assert out["count"] == 0
    assert out["layouts"] == {}


def test_get_pair_layouts_returns_single_position(tmp_path, monkeypatch):
    """One set_pair_layout call → one entry in get_pair_layouts."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    srv.set_pair_layout(
        pair_id="sha256:alpha",
        folder_context=str(folder),
        x=50.0,
        y=60.0,
    )
    out = srv.get_pair_layouts(folder_context=str(folder))
    assert out["ok"] is True, out
    assert out["count"] == 1
    assert "sha256:alpha" in out["layouts"]
    pos = out["layouts"]["sha256:alpha"]
    assert pos["x"] == 50.0
    assert pos["y"] == 60.0
    assert pos["at"]  # ISO timestamp populated
    assert UUID_RE.match(pos["audit_id"])


def test_get_pair_layouts_latest_wins(tmp_path, monkeypatch):
    """A second set_pair_layout for the same pair supersedes the first."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    srv.set_pair_layout(pair_id="sha256:beta",
                          folder_context=str(folder), x=10.0, y=10.0)
    srv.set_pair_layout(pair_id="sha256:beta",
                          folder_context=str(folder), x=200.0, y=300.0)

    out = srv.get_pair_layouts(folder_context=str(folder))
    assert out["count"] == 1
    pos = out["layouts"]["sha256:beta"]
    assert pos["x"] == 200.0
    assert pos["y"] == 300.0


def test_get_pair_layouts_independent_pairs(tmp_path, monkeypatch):
    """Two different pairs each keep their own position."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    srv.set_pair_layout(pair_id="sha256:a",
                          folder_context=str(folder), x=1.0, y=2.0)
    srv.set_pair_layout(pair_id="sha256:b",
                          folder_context=str(folder), x=10.0, y=20.0)

    out = srv.get_pair_layouts(folder_context=str(folder))
    assert out["count"] == 2
    assert out["layouts"]["sha256:a"]["x"] == 1.0
    assert out["layouts"]["sha256:a"]["y"] == 2.0
    assert out["layouts"]["sha256:b"]["x"] == 10.0
    assert out["layouts"]["sha256:b"]["y"] == 20.0


def test_get_pair_layouts_requires_folder_context(tmp_path, monkeypatch):
    """Empty folder_context is rejected cleanly."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.get_pair_layouts(folder_context="")
    assert out["ok"] is False
    assert "folder_context is required" in out["error"]


def test_get_pair_layouts_ignores_non_layout_events(tmp_path, monkeypatch):
    """Unrelated mutation-log events don't surface as layout entries."""
    from workspaces.mutation_log import LogEvent, MutationLog
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    # Write an unrelated event directly to the log
    log = MutationLog(str(folder), log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(folder),
        pair_id="sha256:unrelated",
        actor="test",
        extra={"note": "something else entirely"},
    ))

    out = srv.get_pair_layouts(folder_context=str(folder))
    assert out["ok"] is True
    assert out["count"] == 0


def test_get_pair_layouts_skips_malformed_layout_extras(tmp_path, monkeypatch):
    """A layout extra with non-numeric coords is silently skipped."""
    from workspaces.mutation_log import LogEvent, MutationLog
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    log = MutationLog(str(folder), log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(folder),
        pair_id="sha256:bad",
        actor="test",
        extra={"layout": {"x": "oops", "y": None}},
    ))
    # And a valid one
    srv.set_pair_layout(pair_id="sha256:good",
                          folder_context=str(folder), x=5.0, y=5.0)

    out = srv.get_pair_layouts(folder_context=str(folder))
    assert out["count"] == 1
    assert "sha256:good" in out["layouts"]
    assert "sha256:bad" not in out["layouts"]


# ---------------------------------------------------------------------------
# server_info registration
# ---------------------------------------------------------------------------


def test_set_pair_layout_listed_in_server_info(tmp_path, monkeypatch):
    """0.6.6+: set_pair_layout lives behind the workspace_memory facade
    (op layout_set); the facade is declared, the op reachable."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    info = srv.server_info()
    assert "workspace_memory" in info["tools"], info["tools"]
    ops = {o["op"] for o in srv.workspace_memory("help")["ops"]}
    assert "layout_set" in ops, ops


def test_get_pair_layouts_listed_in_server_info(tmp_path, monkeypatch):
    """0.6.6+: get_pair_layouts lives behind the workspace_memory facade
    (op layout_get); the facade is declared, the op reachable."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    info = srv.server_info()
    assert "workspace_memory" in info["tools"], info["tools"]
    ops = {o["op"] for o in srv.workspace_memory("help")["ops"]}
    assert "layout_get" in ops, ops
