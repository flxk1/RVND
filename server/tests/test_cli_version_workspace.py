# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Mac-gate findings 2026-06-11: `workspaces --version` (DoD ship gate 1),
`workspaces workspace add/remove/list` (doctor's hint must name a real command),
and `workspaces-mcp --help` answering instead of serving."""
from __future__ import annotations

import pytest

from workspaces.cli import main


def test_version_flag_prints_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("workspaces ")
    assert "unknown" not in out  # installed package must resolve metadata


def test_workspace_add_list_remove_round_trip(tmp_path, capsys):
    log_root = tmp_path / "log"
    folder = tmp_path / "ws"
    folder.mkdir()

    rc = main(["--log-root", str(log_root),
               "workspace", "add", str(folder), "--label", "test"])
    assert rc == 0
    assert "registered workspace:" in capsys.readouterr().out

    rc = main(["--log-root", str(log_root), "workspace", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(folder.resolve()) in out and "[test]" in out

    rc = main(["--log-root", str(log_root),
               "workspace", "remove", str(folder)])
    assert rc == 0

    rc = main(["--log-root", str(log_root), "workspace", "list"])
    assert rc == 0
    assert "no workspaces registered." in capsys.readouterr().out


def test_workspace_remove_unregistered_returns_one(tmp_path, capsys):
    rc = main(["--log-root", str(tmp_path / "log"),
               "workspace", "remove", str(tmp_path / "never-added")])
    assert rc == 1


def test_mcp_help_answers_without_serving(monkeypatch, capsys):
    """workspaces-mcp --help must return immediately (doctor probes it with a
    10 s timeout); before this fix mcp.run() served stdio and hung."""
    from workspaces import mcp_server

    monkeypatch.setattr("sys.argv", ["workspaces-mcp", "--help"])
    mcp_server.main()  # must return, not block
    out = capsys.readouterr().out
    assert "workspaces-mcp" in out and "declared tools" in out
