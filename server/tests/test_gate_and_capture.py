# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Integration tests for the ``gate_and_capture_llm`` / ``gate_and_capture_web``
wrappers in :mod:`rvnd.lock.gate_and_capture`.

The wrappers compose:

1. ``gate_for_cloud`` (Privacy Lock) — minimisation gate on the outgoing text.
2. ``try_capture_llm`` / ``try_capture_web`` (L0 bridge) — audit-floor capture
   into the folder's memory.

Tests cover:

- Allow-path: gate allows, capture lands the pair.
- Refuse-path: gate refuses, capture is still attempted (the agentic audit
  floor doesn't depend on the gate verdict — refused calls are forensically
  important).
- Folder-policy short-circuit: when the folder has lock disabled, the gate
  passes through.
- Both transports (in-process and MCP) are exercised.
"""

from __future__ import annotations

import shutil

import pytest

from rvnd.lock import l0_bridge
from rvnd.lock.gate_and_capture import (
    GateAndCaptureResult,
    gate_and_capture_llm,
    gate_and_capture_web,
)


_HAS_MCP_SERVER = shutil.which("workspace-l0-mcp") is not None


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """Isolated vault folder + log root, in-process transport forced."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "inprocess")
    monkeypatch.delenv("AGENT_TOOL_LOCK_L0_MCP_CMD", raising=False)
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    l0_bridge._set_l0_available(True)
    f = tmp_path / "vault"
    f.mkdir()
    yield f
    l0_bridge._set_l0_available(None)


# ===========================================================================
# gate_and_capture_llm — happy path
# ===========================================================================


def test_llm_allow_path_captures_and_returns_both(folder):
    """A clean prompt: gate allows (or minimises), capture lands the pair."""
    result = gate_and_capture_llm(
        prompt="Explain the structure of a regulation in plain prose.",
        response="A regulation has articles and recitals.",
        model="claude-sonnet-4-6",
        folder_context=folder,
    )
    assert isinstance(result, GateAndCaptureResult)
    # Allow or minimise both mean the gate didn't refuse — both are pass-through.
    assert result.gate.action in ("allow", "minimise")
    assert result.capture.attempted is True
    assert result.capture.captured is True
    assert result.capture.pair_id and result.capture.pair_id.startswith("sha256:")
    assert result.capture.transport == "inprocess"


def test_llm_refuse_path_still_captures(folder):
    """A prompt with PII: gate may refuse / ask_user, but capture still happens.

    The agentic audit floor is independent of the gate's verdict — even refused
    calls should land in memory so a reviewer can see what was attempted.
    """
    result = gate_and_capture_llm(
        prompt="Email her at alice\x40example.com about her IBAN DE89370400440532013000.",
        response="Will not be sent because the gate refused.",
        model="claude-sonnet-4-6",
        folder_context=folder,
    )
    # Action depends on lock mode; under default STANDARD + APPROVE oversight
    # the gate either minimises or escalates to ask_user. Both are non-allow.
    assert result.gate.action in ("minimise", "ask_user", "refuse")
    # Capture happens regardless — audit-floor floor.
    assert result.capture.attempted is True
    assert result.capture.captured is True


def test_llm_folder_lock_disabled_allows_benign(folder, monkeypatch):
    """With lock disabled, benign text still passes (allow). Detection now runs
    even under lock-off (CL2) — but clean text has nothing to bypass, so the
    outcome is a plain allow. (A would-be refuse under lock-off is covered in
    test_lock_off_human_cl2.py.) Capture still lands — lock is about egress, not memory.
    """
    from rvnd import disable_lock_for_deployment
    log_root = monkeypatch.delenv("WORKSPACE_L0_LOG_ROOT", raising=False) or str(folder.parent / "logs")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", log_root)
    disable_lock_for_deployment(accepted_by="alex", log_root=log_root)

    result = gate_and_capture_llm(
        prompt="Plain text that would normally pass.",
        response="Response.",
        model="claude-sonnet-4-6",
        folder_context=folder,
    )
    assert result.gate.action == "allow"
    assert "lock disabled" in result.gate.reason
    assert result.capture.captured is True


# ===========================================================================
# gate_and_capture_web — happy path
# ===========================================================================


def test_web_allow_path_captures_and_returns_both(folder):
    """A neutral query: gate doesn't refuse, capture lands the search."""
    result = gate_and_capture_web(
        query="Article structure of a regulation",
        engine="ddg",
        results=[
            {"url": "https://example.com/a", "title": "A", "snippet": "summary A", "rank": 1},
            {"url": "https://example.com/b", "title": "B", "snippet": "summary B", "rank": 2},
        ],
        folder_context=folder,
    )
    assert result.gate.action in ("allow", "minimise")
    assert result.capture.captured is True
    assert result.capture.transport == "inprocess"


def test_web_refuse_path_still_captures(folder):
    """A query containing a client-name-shaped pattern: gate may refuse, capture lands anyway."""
    result = gate_and_capture_web(
        query="legal options for John Doe john.doe\x40acme.example.com after IBAN DE89370400440532013000 leak",
        engine="ddg",
        results=[],
        folder_context=folder,
    )
    assert result.gate.action in ("minimise", "ask_user", "refuse", "allow")
    # Whether the gate fires or not, the audit floor records the query.
    assert result.capture.attempted is True


# ===========================================================================
# Transport coverage — MCP path (skipped if workspace-l0-mcp not on PATH)
# ===========================================================================


@pytest.mark.skipif(not _HAS_MCP_SERVER, reason="workspace-l0-mcp required for MCP transport test")
def test_llm_via_mcp_transport(folder, monkeypatch):
    """Same happy path but routed through the MCP subprocess transport."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "mcp")
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_MCP_CMD", "workspace-l0-mcp")

    result = gate_and_capture_llm(
        prompt="Explain regulation structure in plain prose.",
        response="A regulation has articles and recitals.",
        model="claude-sonnet-4-6",
        folder_context=folder,
    )
    assert result.gate.action in ("allow", "minimise")
    assert result.capture.captured is True
    assert result.capture.transport == "mcp"


@pytest.mark.skipif(not _HAS_MCP_SERVER, reason="workspace-l0-mcp required for MCP transport test")
def test_web_via_mcp_transport(folder, monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "mcp")
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_MCP_CMD", "workspace-l0-mcp")

    result = gate_and_capture_web(
        query="Article structure in regulations",
        engine="ddg",
        results=[{"url": "https://example.com/a", "title": "A", "snippet": "s", "rank": 1}],
        folder_context=folder,
    )
    assert result.gate.action in ("allow", "minimise")
    assert result.capture.captured is True
    assert result.capture.transport == "mcp"
