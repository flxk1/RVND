# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Mac-gate findings 2026-06-11: `workspaces --version` (DoD ship gate 1),
`workspaces workspace add/remove/list` (doctor's hint must name a real command),
and `workspaces-mcp --help` answering instead of serving."""
from __future__ import annotations

import pytest

from rvnd.cli import main


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


def test_add_alias_registers_same_as_workspace_add(tmp_path, capsys):
    """`workspaces add <dir>` (the top-level alias) must register a folder
    exactly like `workspaces workspace add <dir>` — both spellings the
    getting-started docs and the init wizard point at have to work."""
    log_root = tmp_path / "log"
    folder = tmp_path / "ws-alias"
    folder.mkdir()

    rc = main(["--log-root", str(log_root), "add", str(folder), "--label", "via-alias"])
    assert rc == 0
    assert "registered workspace:" in capsys.readouterr().out

    rc = main(["--log-root", str(log_root), "workspace", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(folder.resolve()) in out and "[via-alias]" in out


def test_add_alias_and_workspace_add_share_one_registry(tmp_path, capsys):
    """Registering via the alias and via the nested form both land in the
    same known-workspaces registry — no parallel registration path."""
    log_root = tmp_path / "log"
    via_alias = tmp_path / "ws-a"
    via_nested = tmp_path / "ws-b"
    via_alias.mkdir()
    via_nested.mkdir()

    assert main(["--log-root", str(log_root), "add", str(via_alias)]) == 0
    capsys.readouterr()
    assert main(["--log-root", str(log_root),
                 "workspace", "add", str(via_nested)]) == 0
    capsys.readouterr()

    rc = main(["--log-root", str(log_root), "workspace", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(via_alias.resolve()) in out
    assert str(via_nested.resolve()) in out


def test_unregistered_folder_error_names_workspace_add(tmp_path, capsys, monkeypatch):
    """The allowlist error a newcomer hits on the FIRST governed action
    (`ask` on an unregistered folder) must name a command that actually
    exists — `workspaces workspace add` — not the upstream package's
    internal Python function name (`add_known_workspace`).

    The suite defaults WORKSPACES_ALLOW_UNREGISTERED=1 (conftest.py) so
    existing tests don't need to register a workspace first; this test
    deletes it to exercise the real A6 allowlist refusal, the same pattern
    ``tests/security/test_attack_folder_context_traversal.py`` uses.
    """
    monkeypatch.delenv("WORKSPACES_ALLOW_UNREGISTERED", raising=False)
    log_root = tmp_path / "log"
    folder = tmp_path / "unregistered"
    folder.mkdir()

    rc = main(["--log-root", str(log_root), "ask", "--folder", str(folder), "hello"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "workspaces workspace add" in err
    assert "add_known_workspace" not in err


def test_audit_tail_on_unregistered_folder_names_workspace_add(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("WORKSPACES_ALLOW_UNREGISTERED", raising=False)
    log_root = tmp_path / "log"
    folder = tmp_path / "unregistered2"
    folder.mkdir()

    rc = main(["--log-root", str(log_root), "audit-tail", "--folder", str(folder)])
    assert rc == 3
    err = capsys.readouterr().err
    assert "workspaces workspace add" in err
    assert "add_known_workspace" not in err


def test_mcp_help_answers_without_serving(monkeypatch, capsys):
    """workspaces-mcp --help must return immediately (doctor probes it with a
    10 s timeout); before this fix mcp.run() served stdio and hung."""
    from rvnd import mcp_server

    monkeypatch.setattr("sys.argv", ["workspaces-mcp", "--help"])
    mcp_server.main()  # must return, not block
    out = capsys.readouterr().out
    assert "workspaces-mcp" in out and "declared tools" in out
