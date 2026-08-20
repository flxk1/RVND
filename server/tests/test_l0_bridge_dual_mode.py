# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the dual-transport L0 bridge in :mod:`workspaces.lock.l0_bridge`.

Covers:

- Transport selection (env var override, auto mode, no-op fallback).
- In-process path still works when no env var is set and workspaces
  is importable.
- MCP path works end-to-end against a real ``workspace-l0-mcp`` subprocess.
- No-op path when neither is reachable.
- ``_set_l0_available`` test hook still gates the in-process path.

The MCP-path test depends on ``workspace-l0-memory`` being installed (which it
is in this test environment via the editable install). It also requires
``workspace-l0-mcp`` to be on PATH — which the pyproject's ``[project.scripts]``
guarantees once the package is installed.
"""

from __future__ import annotations

import shutil

import pytest

from workspaces.lock import l0_bridge


# ===========================================================================
# Transport selection
# ===========================================================================


def test_select_transport_explicit_inprocess(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "inprocess")
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_MCP_CMD", raising=False)
    l0_bridge._set_l0_available(True)
    assert l0_bridge._select_transport() == "inprocess"


def test_select_transport_explicit_mcp(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "mcp")
    assert l0_bridge._select_transport() == "mcp"


def test_select_transport_explicit_noop(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "noop")
    assert l0_bridge._select_transport() == "noop"


def test_select_transport_auto_prefers_mcp_when_cmd_set(monkeypatch):
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_TRANSPORT", raising=False)
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_MCP_CMD", "workspace-l0-mcp")
    l0_bridge._set_l0_available(True)
    assert l0_bridge._select_transport() == "mcp"


def test_select_transport_auto_falls_back_to_inprocess(monkeypatch):
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_TRANSPORT", raising=False)
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_MCP_CMD", raising=False)
    l0_bridge._set_l0_available(True)
    assert l0_bridge._select_transport() == "inprocess"


def test_select_transport_auto_noop_when_nothing_available(monkeypatch):
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_TRANSPORT", raising=False)
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_MCP_CMD", raising=False)
    l0_bridge._set_l0_available(False)
    try:
        assert l0_bridge._select_transport() == "noop"
    finally:
        l0_bridge._set_l0_available(None)


# ===========================================================================
# In-process path (default when in same venv)
# ===========================================================================


def test_inprocess_capture_llm_records_pair(monkeypatch, tmp_path):
    """In-process: capture lands a real pair, bridge result reports transport."""
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_TRANSPORT", raising=False)
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_MCP_CMD", raising=False)
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    l0_bridge._set_l0_available(True)

    folder = tmp_path / "vault"
    folder.mkdir()

    result = l0_bridge.try_capture_llm(
        folder_context=folder,
        model="claude-sonnet-4-6",
        prompt_context="ground these claims",
        response="grounded.",
        oversight_level="approve",
        mode="agentic",
    )
    assert result.attempted is True
    assert result.captured is True
    assert result.pair_id and result.pair_id.startswith("sha256:")
    assert result.verbosity in ("full", "preview+citations", "preview", "metadata", "full+trace")
    assert result.transport == "inprocess"


def test_inprocess_load_policy_default(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_TRANSPORT", raising=False)
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_MCP_CMD", raising=False)
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    l0_bridge._set_l0_available(True)

    folder = tmp_path / "vault"
    folder.mkdir()
    snap = l0_bridge.try_load_policy(folder)
    assert snap.lock_is_active is True
    assert snap.oversight_is_active is True
    assert snap.source == "policy_file"


def test_set_l0_available_test_hook_disables_inprocess(monkeypatch, tmp_path):
    """When the in-process is force-disabled and no MCP is configured → no-op."""
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_TRANSPORT", raising=False)
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_MCP_CMD", raising=False)
    l0_bridge._set_l0_available(False)
    try:
        result = l0_bridge.try_capture_llm(
            folder_context=tmp_path,
            model="m",
            prompt_context="p",
            response="r",
        )
        assert result.attempted is False
        assert result.skipped_reason == "l0_unavailable"
    finally:
        l0_bridge._set_l0_available(None)


# ===========================================================================
# No-op path
# ===========================================================================


def test_noop_path_when_folder_context_none():
    result = l0_bridge.try_capture_llm(
        folder_context=None,
        model="m",
        prompt_context="p",
        response="r",
    )
    assert result.attempted is False
    assert result.skipped_reason == "no_folder_context"


def test_noop_path_load_policy_returns_safe_defaults():
    snap = l0_bridge.try_load_policy(None)
    assert snap.lock_is_active is True
    assert snap.oversight_is_active is True
    assert snap.source == "default"


# ===========================================================================
# MCP path (real subprocess)
# ===========================================================================


_HAS_MCP_CLIENT = False
try:
    from mcp import ClientSession  # noqa: F401
    _HAS_MCP_CLIENT = True
except ImportError:
    pass

_HAS_MCP_SERVER = shutil.which("workspace-l0-mcp") is not None

_skip_mcp = pytest.mark.skipif(
    not (_HAS_MCP_CLIENT and _HAS_MCP_SERVER),
    reason="workspace-l0-mcp on PATH and mcp SDK both required for end-to-end MCP tests",
)


@_skip_mcp
def test_mcp_load_policy_end_to_end(monkeypatch, tmp_path):
    """Spawn workspace-l0-mcp, read a default policy, verify safe-defaults."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "mcp")
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_MCP_CMD", "workspace-l0-mcp")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))

    folder = tmp_path / "vault"
    folder.mkdir()

    snap = l0_bridge.try_load_policy(folder)
    assert snap.lock_is_active is True
    assert snap.oversight_is_active is True
    assert snap.source == "policy_file_via_mcp"


@_skip_mcp
def test_mcp_capture_llm_end_to_end(monkeypatch, tmp_path):
    """Spawn workspace-l0-mcp, capture an LLM exchange, verify pair_id returned."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "mcp")
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_MCP_CMD", "workspace-l0-mcp")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))

    folder = tmp_path / "vault"
    folder.mkdir()

    result = l0_bridge.try_capture_llm(
        folder_context=folder,
        model="claude-sonnet-4-6",
        prompt_context="via mcp",
        response="response via mcp",
        oversight_level="approve",
        mode="agentic",
    )
    assert result.attempted is True
    assert result.captured is True
    assert result.transport == "mcp"
    assert result.pair_id and result.pair_id.startswith("sha256:")


@_skip_mcp
def test_mcp_capture_web_end_to_end(monkeypatch, tmp_path):
    """Spawn workspace-l0-mcp, capture a web exchange, verify pair lands."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "mcp")
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_MCP_CMD", "workspace-l0-mcp")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))

    folder = tmp_path / "vault"
    folder.mkdir()

    result = l0_bridge.try_capture_web(
        folder_context=folder,
        query="GDPR Art. 28",
        engine="ddg",
        results=[{"url": "https://example.com", "title": "t", "snippet": "s", "rank": 1}],
        oversight_level="approve",
        mode="agentic",
    )
    assert result.attempted is True
    assert result.captured is True
    assert result.transport == "mcp"


def test_mcp_path_falls_back_when_command_missing(monkeypatch, tmp_path):
    """MCP forced but no command on PATH → falls back to in-process (if available)."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "mcp")
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_MCP_CMD", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")  # no workspace-l0-mcp on PATH
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    l0_bridge._set_l0_available(True)

    folder = tmp_path / "vault"
    folder.mkdir()

    result = l0_bridge.try_capture_llm(
        folder_context=folder,
        model="m",
        prompt_context="p",
        response="r",
        oversight_level="approve",
    )
    # MCP couldn't be reached → fell back to in-process.
    assert result.transport == "inprocess"
    assert result.captured is True
