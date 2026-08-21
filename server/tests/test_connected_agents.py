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
