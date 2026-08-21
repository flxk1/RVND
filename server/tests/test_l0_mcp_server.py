# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Smoke tests for the workspace-l0-memory MCP server.

These tests bypass the MCP transport and call the underlying tool functions
directly. They verify that:

1. The module imports without errors (FastMCP registration works).
2. Each tool returns a transport-friendly dict matching its documented shape.
3. End-to-end: capture -> search/by_id retrieves the captured pair.
4. Policy snapshot reflects acknowledged opt-outs.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def server_module(monkeypatch, tmp_path):
    """Import the server module with an isolated log root via env var."""
    log_root = tmp_path / "logs"
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    # Re-import so the env var is picked up by _log_root() on each call.
    mod = importlib.import_module("rvnd.mcp_server")
    importlib.reload(mod)
    return mod


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "vault"
    f.mkdir()
    return f


# ===========================================================================
# Server module sanity
# ===========================================================================


def test_module_imports_and_registers_tools():
    """The MCP server module imports cleanly and the FastMCP instance exists."""
    mod = importlib.import_module("rvnd.mcp_server")
    assert mod.mcp is not None
    # The tools are bound on the module as callable functions.
    for name in ("capture_llm", "capture_web", "policy_snapshot",
                 "search", "by_id", "recent"):
        assert callable(getattr(mod, name)), f"missing tool: {name}"


# ===========================================================================
# capture_llm
# ===========================================================================


def test_capture_llm_agentic_full(server_module, folder):
    out = server_module.capture_llm(
        folder_context=str(folder),
        model="claude-sonnet-4-6",
        prompt_context="Summarise GDPR Art. 28",
        response="A processor processes personal data on behalf of...",
        cited_sources=["https://eur-lex.europa.eu/eli/reg/2016/679"],
        cost_estimate_cents=0.4,
        request_id="req-1",
        mode="agentic",
        oversight="approve",
    )
    assert out["captured"] is True
    assert isinstance(out["pair_id"], str) and out["pair_id"]
    assert out["verbosity"] == "full"
    assert out["mode"] == "agentic"
    assert out["oversight_bypassed"] is False


def test_capture_llm_agentic_autonomous_is_metadata_only(server_module, folder):
    out = server_module.capture_llm(
        folder_context=str(folder),
        model="claude-sonnet-4-6",
        prompt_context="secret prompt",
        response="secret response",
        mode="agentic",
        oversight="autonomous",
    )
    assert out["captured"] is True
    assert out["verbosity"] == "metadata"


def test_capture_llm_interactive_autonomous_skips(server_module, folder):
    out = server_module.capture_llm(
        folder_context=str(folder),
        model="claude-sonnet-4-6",
        prompt_context="hello",
        response="hi",
        mode="interactive",
        oversight="autonomous",
    )
    assert out["captured"] is False
    assert out["verbosity"] == "none"
    assert out["skipped_reason"]


# ===========================================================================
# capture_web
# ===========================================================================


def test_capture_web_agentic_review_has_snippets(server_module, folder):
    out = server_module.capture_web(
        folder_context=str(folder),
        query="GDPR Art. 28 sub-processor enforcement",
        engine="ddg",
        results=[
            {"url": "https://example.com/a", "title": "A", "snippet": "snip A",
             "full_text": "full A", "rank": 1},
            {"url": "https://example.com/b", "title": "B", "snippet": "snip B",
             "full_text": "full B", "rank": 2},
        ],
        mode="agentic",
        oversight="review",
    )
    assert out["captured"] is True
    assert out["verbosity"] == "preview+citations"


def test_capture_web_results_missing_keys_default(server_module, folder):
    """Missing optional keys in result dicts must not crash."""
    out = server_module.capture_web(
        folder_context=str(folder),
        query="q",
        engine="ddg",
        results=[{"url": "https://example.com/only-url"}],
        mode="agentic",
        oversight="notify",
    )
    assert out["captured"] is True


# ===========================================================================
# policy_snapshot
# ===========================================================================


def test_policy_snapshot_defaults(server_module, folder):
    out = server_module.policy_snapshot(folder_context=str(folder))
    assert out["lock_is_active"] is True
    assert out["oversight_is_active"] is True
    assert out["acknowledgements"] == {}
    assert out["folder_context"].endswith("vault")


def test_policy_snapshot_after_lock_disable(server_module, folder):
    from rvnd import disable_lock
    # disable_lock writes to the same log_root the server reads.
    import os
    log_root = os.environ["WORKSPACE_L0_LOG_ROOT"]
    disable_lock(folder, accepted_by="alex", log_root=log_root)
    out = server_module.policy_snapshot(folder_context=str(folder))
    assert out["lock_is_active"] is False
    assert "lock_disable" in out["acknowledgements"]
    assert out["acknowledgements"]["lock_disable"]["accepted_by"] == "alex"
    # Oversight untouched.
    assert out["oversight_is_active"] is True


# ===========================================================================
# search + by_id + recent
# ===========================================================================


def test_search_finds_captured_llm_exchange(server_module, folder):
    captured = server_module.capture_llm(
        folder_context=str(folder),
        model="claude-sonnet-4-6",
        prompt_context="What is the AI Act Art. 6 high-risk test?",
        response="Annex III lists eight categories of high-risk AI systems.",
        mode="agentic",
        oversight="approve",
    )
    pair_id = captured["pair_id"]

    hits = server_module.search(
        folder_context=str(folder),
        query="AI Act Annex III high-risk",
        k=5,
    )
    assert hits["results"], "expected at least one hit"
    # by_id is the deterministic check; search ordering is fuzzy.
    by_id_out = server_module.by_id(
        folder_context=str(folder),
        pair_id=pair_id,
    )
    assert by_id_out["found"] is True
    assert "problem" in by_id_out["pair"]


def test_by_id_not_found_returns_false(server_module, folder):
    out = server_module.by_id(
        folder_context=str(folder),
        pair_id="sha256:nope",
    )
    assert out["found"] is False
    assert out["pair_id"] == "sha256:nope"


def test_l0_recent_returns_live_pairs(server_module, folder):
    for i in range(3):
        server_module.capture_llm(
            folder_context=str(folder),
            model="m",
            prompt_context=f"prompt {i}",
            response=f"resp {i}",
            mode="agentic",
            oversight="approve",
        )
    out = server_module.recent(folder_context=str(folder), limit=10)
    assert out["count"] >= 3
