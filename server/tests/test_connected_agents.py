# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""connected_agents — the SERVER-LEVEL registry of agents that completed the MCP
handshake, independent of any workspace (presence, not per-folder authority)."""
import os
import subprocess
import sys

from rvnd import connected_agents as ca


def test_register_list_deregister(tmp_path):
    root = str(tmp_path / "agents-root")
    cid = ca.register_connection(agent="claude-code", transport="stdio",
                                 pid=os.getpid(), root=root)
    assert cid
    agents = ca.list_connected(root=root)
    assert len(agents) == 1
    assert agents[0]["agent"] == "claude-code"
    assert agents[0]["transport"] == "stdio"
    ca.deregister_connection(cid, root=root)
    assert ca.list_connected(root=root) == []


def test_empty_agent_name_defaults(tmp_path):
    root = str(tmp_path / "r")
    ca.register_connection(agent="", pid=os.getpid(), root=root)
    assert ca.list_connected(root=root)[0]["agent"] == "unnamed-agent"


def test_stale_pid_self_heals(tmp_path):
    # A record whose process is gone must self-heal out of the list (crash cleanup).
    root = str(tmp_path / "r")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()  # its PID is now a dead process
    ca.register_connection(agent="ghost", pid=dead.pid, root=root)
    assert ca.list_connected(root=root) == []


def test_ttl_drops_old(tmp_path):
    root = str(tmp_path / "r")
    ca.register_connection(agent="old", pid=os.getpid(), now=0.0, root=root)
    assert ca.list_connected(now=1e12, root=root, ttl_seconds=10) == []


def test_op_reads_default_dir(tmp_path, monkeypatch):
    # The server-level op returns the connected agents from the default registry dir.
    monkeypatch.setenv("WORKSPACE_AGENTS_DIR", str(tmp_path / "adir"))
    ca.register_connection(agent="claude-code", pid=os.getpid())
    from rvnd import mcp_server
    r = mcp_server.workspace_workflow("connected_agents", {})
    assert r["ok"] and r["count"] >= 1
    assert any(a["agent"] == "claude-code" for a in r["agents"])


# ── session_id: the join key to the signed chain (the actor the PreToolUse hook
# records IS this id). Captured from the param, else CLAUDE_CODE_SESSION_ID —
# never fabricated. ─────────────────────────────────────────────────────────

def test_session_id_from_param_is_recorded(tmp_path):
    root = str(tmp_path / "r")
    ca.register_connection(agent="claude-code", pid=os.getpid(),
                           session_id="sess-abc123", root=root)
    assert ca.list_connected(root=root)[0]["session_id"] == "sess-abc123"


def test_session_id_falls_back_to_env(tmp_path, monkeypatch):
    # No explicit param → the host's CLAUDE_CODE_SESSION_ID is the join key.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-sid-9")
    root = str(tmp_path / "r")
    ca.register_connection(agent="claude-code", pid=os.getpid(), root=root)
    assert ca.list_connected(root=root)[0]["session_id"] == "env-sid-9"


def test_session_id_absent_is_empty_never_faked(tmp_path, monkeypatch):
    # Host sets no id and none passed → empty string, never an invented value.
    # Neutralise the live-process backfill (which would legitimately read the
    # real pid's CLAUDE_CODE_SESSION_ID) to isolate the register-time contract.
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(ca, "_pid_session_id", lambda pid: None)
    root = str(tmp_path / "r")
    ca.register_connection(agent="claude-code", pid=os.getpid(), root=root)
    assert ca.list_connected(root=root)[0]["session_id"] == ""


# ── clientInfo: DESCRIPTIVE MCP presence, filled lazily on first tool call.
# Idempotent, never overwrites, empty-name is a no-op (fail closed to empty). ──

def test_update_client_info_fills_once(tmp_path):
    root = str(tmp_path / "r")
    cid = ca.register_connection(agent="a", pid=os.getpid(), root=root)
    assert ca.update_client_info(cid, name="cursor", version="1.4.2", root=root)
    rec = ca.list_connected(root=root)[0]
    assert rec["client_name"] == "cursor" and rec["client_version"] == "1.4.2"


def test_update_client_info_never_overwrites(tmp_path):
    root = str(tmp_path / "r")
    cid = ca.register_connection(agent="a", pid=os.getpid(), root=root)
    ca.update_client_info(cid, name="cursor", version="1", root=root)
    # A second, different clientInfo must NOT clobber the first-captured value.
    assert ca.update_client_info(cid, name="vscode", version="2", root=root) is False
    assert ca.list_connected(root=root)[0]["client_name"] == "cursor"


def test_update_client_info_empty_name_is_noop(tmp_path):
    root = str(tmp_path / "r")
    cid = ca.register_connection(agent="a", pid=os.getpid(), root=root)
    assert ca.update_client_info(cid, name="", version="9", root=root) is False
    assert ca.list_connected(root=root)[0]["client_name"] == ""
