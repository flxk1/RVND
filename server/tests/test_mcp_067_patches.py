# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the MCP-surface patches in 0.6.7.

Three MCP-side patches:
1. lock_audit_query — required `reason_for_query` arg + self-logged access
2. lock_classify_text — per-folder rate limit (100/min default)
3. local_llm_* — three new tools routing to OpenAI-compatible local endpoint
"""
from __future__ import annotations

from unittest.mock import patch



# ---------------------------------------------------------------------------
# Patch 2: lock_audit_query — reason required + self-logged
# ---------------------------------------------------------------------------


def test_lock_audit_query_refuses_empty_reason():
    """Empty / whitespace reason → refused with structured error."""
    from rvnd.mcp_server import lock_audit_query
    result = lock_audit_query(reason_for_query="", limit=10)
    assert result["ok"] is False
    assert "reason_for_query is required" in result["error"]


def test_lock_audit_query_refuses_whitespace_reason():
    from rvnd.mcp_server import lock_audit_query
    result = lock_audit_query(reason_for_query="   ", limit=10)
    assert result["ok"] is False


def test_lock_audit_query_accepts_real_reason(monkeypatch):
    """Real reason → tool proceeds (returns audit-log read result)."""
    from rvnd.mcp_server import lock_audit_query
    monkeypatch.delenv("AGENT_TOOL_LOCK_AUDIT_LOG", raising=False)
    result = lock_audit_query(
        reason_for_query="monthly compliance review for Acme",
        limit=10,
    )
    # No audit-log path configured → returns empty result, but does NOT
    # raise the "reason required" error.
    assert "reason_for_query is required" not in str(result.get("error", ""))


# ---------------------------------------------------------------------------
# Patch 4: lock_classify_text rate limit
# ---------------------------------------------------------------------------


def test_lock_classify_text_rate_limit_triggers(monkeypatch):
    """Burst beyond 100/min returns rate_limited error."""
    from rvnd import mcp_server
    from rvnd.mcp_server import lock_classify_text

    # Reset bucket for clean test + lower threshold for test speed
    monkeypatch.setattr("rvnd.mcp_impl._LOCK_CLASSIFY_RATE_LIMIT", 5)
    mcp_server._LOCK_CLASSIFY_BUCKET.clear()

    folder = "/tmp/rate-test-" + str(id(test_lock_classify_text_rate_limit_triggers))
    # First 5 should succeed
    for i in range(5):
        result = lock_classify_text(text="some text", folder_context=folder)
        assert result.get("ok") is not False or "rate_limited" not in str(result), \
            f"call {i+1} should succeed, got {result}"

    # 6th call should be rate-limited
    rate_limited = lock_classify_text(text="some text", folder_context=folder)
    assert rate_limited.get("ok") is False
    assert rate_limited.get("error") == "rate_limited"
    assert "retry_after_seconds" in rate_limited
    assert rate_limited["limit_per_minute"] == 5


def test_lock_classify_text_rate_limit_per_folder(monkeypatch):
    """Rate limits are per-folder; one folder's bucket doesn't affect another."""
    from rvnd import mcp_server
    from rvnd.mcp_server import lock_classify_text

    monkeypatch.setattr("rvnd.mcp_impl._LOCK_CLASSIFY_RATE_LIMIT", 2)
    mcp_server._LOCK_CLASSIFY_BUCKET.clear()

    folder_a = "/tmp/rate-a"
    folder_b = "/tmp/rate-b"

    # Saturate folder A
    lock_classify_text(text="x", folder_context=folder_a)
    lock_classify_text(text="x", folder_context=folder_a)
    over_a = lock_classify_text(text="x", folder_context=folder_a)
    assert over_a.get("error") == "rate_limited"

    # Folder B is independent
    ok_b = lock_classify_text(text="x", folder_context=folder_b)
    assert ok_b.get("error") != "rate_limited", \
        f"folder B should be independent of folder A's bucket; got {ok_b}"


# ---------------------------------------------------------------------------
# Local-LLM routes (new in 0.6.7)
# ---------------------------------------------------------------------------


def test_local_llm_complete_refuses_when_no_endpoint(monkeypatch):
    """Without WORKSPACE_LOCAL_LLM_URL configured, complete refuses cleanly."""
    monkeypatch.delenv("WORKSPACE_LOCAL_LLM_URL", raising=False)
    from rvnd.local_llm import complete
    result = complete("Hello", model="phi-3.5")
    assert result["ok"] is False
    assert "no local-LLM endpoint configured" in result["error"]


def test_local_llm_complete_refuses_when_no_model(monkeypatch):
    """With URL set but no model (env or arg), refuses cleanly."""
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:1234/v1")
    monkeypatch.delenv("WORKSPACE_LOCAL_LLM_MODEL", raising=False)
    from rvnd.local_llm import complete
    result = complete("Hello", model=None)
    assert result["ok"] is False
    assert "no model configured" in result["error"]


def test_local_llm_complete_handles_unreachable(monkeypatch):
    """Unreachable endpoint returns clean error, not exception."""
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_MODEL", "test-model")
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_TIMEOUT_SECS", "1")
    from rvnd.local_llm import complete
    result = complete("Hello", model="test-model")
    assert result["ok"] is False
    assert "unreachable" in result["error"].lower() or "timed out" in result["error"].lower() or "refused" in result["error"].lower()
    assert "endpoint_host" in result


def test_local_llm_complete_parses_openai_response(monkeypatch):
    """Mocked successful response parses into expected shape."""
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_MODEL", "phi-3.5-mini")

    from rvnd import local_llm

    fake_response = {
        "model": "phi-3.5-mini",
        "choices": [{
            "message": {"content": "Hello back!"},
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }

    with patch.object(local_llm, "_post_json", return_value=fake_response):
        result = local_llm.complete("Hello", model=None)

    assert result["ok"] is True
    assert result["response"] == "Hello back!"
    assert result["model_used"] == "phi-3.5-mini"
    assert "latency_ms" in result
    assert result["endpoint_host"] == "localhost"


def test_local_llm_classify_picks_from_categories(monkeypatch):
    """Classification with mocked model returns one of the provided categories."""
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_MODEL", "phi-3.5-mini")

    from rvnd import local_llm

    fake = {"model": "phi-3.5-mini", "choices": [{"message": {"content": "personal_data"}}]}
    with patch.object(local_llm, "_post_json", return_value=fake):
        result = local_llm.classify(
            text="Maria Schmidt's email is m.s\x40example.com",
            categories=["personal_data", "public_info", "confidential"],
        )

    assert result["ok"] is True
    assert result["category"] == "personal_data"
    assert result["raw_response"] == "personal_data"


def test_local_llm_classify_substring_fallback(monkeypatch):
    """If the model's response doesn't exact-match, substring fallback picks."""
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_MODEL", "phi-3.5-mini")

    from rvnd import local_llm

    fake = {"model": "phi-3.5-mini",
             "choices": [{"message": {"content": "The category is personal_data here."}}]}
    with patch.object(local_llm, "_post_json", return_value=fake):
        result = local_llm.classify(
            text="x", categories=["personal_data", "public_info"],
        )

    assert result["ok"] is True
    assert result["category"] == "personal_data"


def test_local_llm_list_available_unreachable(monkeypatch):
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_TIMEOUT_SECS", "1")
    from rvnd.local_llm import list_available
    result = list_available()
    assert result["ok"] is False
    assert result["reachable"] is False


def test_local_llm_list_available_parses_response(monkeypatch):
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:1234/v1")
    from rvnd import local_llm
    fake = {
        "object": "list",
        "data": [
            {"id": "phi-3.5-mini"},
            {"id": "qwen-2.5-3b"},
            {"id": "llama-3.2-1b"},
        ],
    }
    with patch.object(local_llm, "_get_json", return_value=fake):
        result = local_llm.list_available()
    assert result["ok"] is True
    assert result["reachable"] is True
    assert "phi-3.5-mini" in result["models"]
    assert len(result["models"]) == 3


def test_local_llm_complete_handles_malformed_response(monkeypatch):
    """If the endpoint returns unexpected shape, fail safely with diagnostic."""
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_MODEL", "phi-3.5-mini")

    from rvnd import local_llm

    malformed = {"unexpected": "shape"}
    with patch.object(local_llm, "_post_json", return_value=malformed):
        result = local_llm.complete("Hello", model=None)

    assert result["ok"] is False
    assert "unexpected response shape" in result["error"]
    assert "raw_response" in result
