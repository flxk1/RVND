# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the backend-factory pattern + mock backend + degraded paths."""

from __future__ import annotations

import os

import pytest

from workspaces.lock import BackendError, make_local_llm
from workspaces.lock.backends import MockBackend
from workspaces.lock.backends.llama_cpp import LlamaCppBackend
from workspaces.lock.backends.onnx_genai import OnnxGenaiBackend
from workspaces.lock.tier_c import (
    is_tier_c_available,
    describe_tier_c,
    reset_backend_cache,
    tier_c_check_semantic,
)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_empty_spec_raises():
    with pytest.raises(BackendError):
        make_local_llm("")


def test_factory_unknown_prefix_raises():
    with pytest.raises(BackendError):
        make_local_llm("nonexistent:foo")


def test_factory_missing_argument_raises():
    with pytest.raises(BackendError):
        make_local_llm("llama_cpp")  # no path


def test_factory_mock():
    backend = make_local_llm("mock")
    assert isinstance(backend, MockBackend)
    assert backend.is_available() is True


def test_factory_llama_cpp_with_missing_path_still_constructs():
    # The backend can be constructed; it just isn't available at classify-time
    backend = make_local_llm("llama_cpp:/nonexistent/path.gguf")
    assert isinstance(backend, LlamaCppBackend)
    assert backend.is_available() is False


def test_factory_ollama_removed_raises():
    """Ollama backend was removed in 0.6.5 per project policy. Spec must raise."""
    with pytest.raises(BackendError, match="removed"):
        make_local_llm("ollama:llama3.2:3b")


def test_factory_onnx_genai_with_missing_dir_still_constructs():
    # The backend can be constructed; it just isn't available at classify-time
    backend = make_local_llm("onnx_genai:/nonexistent")
    assert isinstance(backend, OnnxGenaiBackend)
    assert backend.is_available() is False


# ---------------------------------------------------------------------------
# Mock backend behaviour
# ---------------------------------------------------------------------------


def test_mock_classifies_health():
    backend = MockBackend()
    result = backend.classify("patient prescribed chemotherapy")
    assert result["contains_pii"] is True
    assert result["type"] == "health"


def test_mock_classifies_name():
    backend = MockBackend()
    result = backend.classify("Maria Schmidt approved the request")
    assert result["contains_pii"] is True
    assert result["type"] == "name"


def test_mock_classifies_financial():
    backend = MockBackend()
    result = backend.classify("They owe a significant mortgage")
    assert result["contains_pii"] is True
    assert result["type"] == "financial"


def test_mock_clean_text_no_finding():
    backend = MockBackend()
    result = backend.classify("aggregate metrics for the team this quarter")
    assert result["contains_pii"] is False
    assert result["type"] == "none"


def test_mock_empty_input():
    backend = MockBackend()
    result = backend.classify("")
    assert result["contains_pii"] is False


def test_mock_deterministic_same_input_same_output():
    backend = MockBackend()
    r1 = backend.classify("Maria Schmidt")
    r2 = backend.classify("Maria Schmidt")
    assert r1 == r2


# ---------------------------------------------------------------------------
# llama_cpp backend — degraded paths only (don't require model installed)
# ---------------------------------------------------------------------------


def test_llama_cpp_unavailable_when_path_missing():
    backend = LlamaCppBackend("/this/path/does/not/exist.gguf")
    assert backend.is_available() is False
    # FAIL-CLOSED (0.6.8.2): when the backend can't run, the privacy gate must
    # NOT silently pass the text — it flags it so upstream policy gates the
    # cloud call rather than leaking unscanned content.
    result = backend.classify("Maria Schmidt approved")
    assert result["contains_pii"] is True
    assert "fail-closed" in result["reason"]
    assert "backend unavailable" in result["reason"]


def test_llama_cpp_describe_unavailable():
    backend = LlamaCppBackend("/nonexistent.gguf")
    assert "UNAVAILABLE" in backend.describe()


# ---------------------------------------------------------------------------
# ONNX GenAI backend — degraded paths only (don't require model installed)
# ---------------------------------------------------------------------------


def test_onnx_genai_unavailable_when_dir_missing():
    backend = OnnxGenaiBackend("/nonexistent")
    # Even if onnxruntime_genai installed, the dir doesn't exist
    assert backend.is_available() is False
    # FAIL-CLOSED: when the backend can't run, the privacy gate must NOT
    # silently pass the text — it flags it so upstream policy gates the
    # cloud call rather than leaking unscanned content. Same contract as
    # the llama_cpp backend.
    result = backend.classify("Maria Schmidt")
    assert result["contains_pii"] is True
    assert "fail-closed" in result["reason"]
    assert "backend unavailable" in result["reason"]


def test_onnx_genai_describe_unavailable():
    backend = OnnxGenaiBackend("/nonexistent")
    assert "UNAVAILABLE" in backend.describe()


def test_onnx_genai_empty_input_short_circuits():
    # Empty input answers "clean" without touching the model — no fail-closed.
    backend = OnnxGenaiBackend("/nonexistent")
    result = backend.classify("   ")
    assert result["contains_pii"] is False
    assert result["reason"] == "empty"


@pytest.mark.skipif(
    not os.environ.get("RVND_TEST_ONNX_MODEL_DIR"),
    reason=(
        "live ONNX inference: set RVND_TEST_ONNX_MODEL_DIR to an ONNX GenAI "
        "model directory (containing genai_config.json) to run"
    ),
)
def test_onnx_genai_live_classify_round_trip():
    """Loads the real model and checks the classify() plumbing end to end:
    prompt in, parsed classification dict out, no fail-closed degradation.
    Covers plumbing, not model quality — verdict correctness is benchmarked
    separately."""
    backend = OnnxGenaiBackend(os.environ["RVND_TEST_ONNX_MODEL_DIR"])
    assert backend.is_available() is True, backend.describe()
    result = backend.classify("Maria Schmidt approved the request")
    assert set(result) == {"contains_pii", "type", "confidence", "reason"}
    assert isinstance(result["contains_pii"], bool)
    assert not result["reason"].startswith("fail-closed")


# ---------------------------------------------------------------------------
# Tier C dispatcher
# ---------------------------------------------------------------------------


def test_tier_c_default_is_mock(monkeypatch):
    monkeypatch.delenv("AGENT_TOOL_LOCK_LLM_BACKEND", raising=False)
    reset_backend_cache()
    assert is_tier_c_available() is True
    assert "mock" in describe_tier_c().lower()


def test_tier_c_uses_env_var(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    findings = tier_c_check_semantic("Maria Schmidt approved the request")
    assert len(findings) == 1
    assert findings[0].tier == "C"


def test_tier_c_invalid_spec_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "nonexistent:foo")
    reset_backend_cache()
    # Should not raise; should fall back to mock
    assert is_tier_c_available() is True


def test_tier_c_health_is_high_severity(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    findings = tier_c_check_semantic("patient prescribed chemotherapy")
    assert findings[0].severity == "high"


def test_tier_c_empty_text_no_findings(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    assert tier_c_check_semantic("") == []
    assert tier_c_check_semantic("   ") == []


def test_tier_c_clean_text_no_findings(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    findings = tier_c_check_semantic("aggregate metrics for the team")
    assert findings == []


def test_tier_c_backend_cache_invalidation(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    assert "mock" in describe_tier_c()
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "llama_cpp:/nonexistent.gguf")
    reset_backend_cache()
    assert "llama_cpp" in describe_tier_c() or "UNAVAILABLE" in describe_tier_c()


# ---------------------------------------------------------------------------
# Confidential-context path (KG-sourced terms passed through `context`)
# ---------------------------------------------------------------------------


def test_mock_backend_flags_confidential_term_from_context():
    """Mock backend treats any context line that appears in the text as confidential."""
    backend = MockBackend()
    text = "Project Atlas will ship Phase 3 in June."
    context = "- Acme Corp\n- Project Atlas\n- Northwind"
    result = backend.classify(text, context=context)
    assert result["contains_pii"] is True
    assert result["type"] == "confidential"
    assert result["confidence"] >= 0.9
    assert "Project Atlas" in result["reason"]


def test_mock_backend_no_confidential_match_when_context_empty():
    """Backwards-compat: same input without context = same answer as before."""
    backend = MockBackend()
    # Lowercase-only text avoids the mock's two-capitalised-words name regex.
    text = "the project atlas repository ships phase 3 in june."
    result = backend.classify(text)  # no context
    assert result["contains_pii"] is False
    assert result["type"] == "none"


def test_mock_backend_no_match_when_context_term_absent_from_text():
    """If the confidential term isn't in the text, no false flag."""
    backend = MockBackend()
    text = "The build pipeline runs in 12 minutes on the new runner."
    context = "- Workspaceversum\n- Brain"
    result = backend.classify(text, context=context)
    assert result["contains_pii"] is False
    assert result["type"] == "none"


def test_mock_backend_confidential_outranks_pii():
    """Confidential check runs first — a text matching both still flags confidential."""
    backend = MockBackend()
    text = "Maria Schmidt is the new lead on Workspaceversum."
    context = "Workspaceversum"
    result = backend.classify(text, context=context)
    assert result["contains_pii"] is True
    assert result["type"] == "confidential"


def test_mock_backend_short_context_terms_ignored():
    """3-char minimum stops a context line like 'a' from matching half the alphabet."""
    backend = MockBackend()
    text = "ab cd ef"
    context = "a\nb\ncd"  # all <3 chars after stripping
    result = backend.classify(text, context=context)
    assert result["contains_pii"] is False


def test_tier_c_passes_context_to_backend(monkeypatch):
    """tier_c_check_semantic must forward `context` to backend.classify."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    findings = tier_c_check_semantic(
        "the workspaceversum build ships phase 3 in june.",
        context="- workspaceversum",
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.tier == "C"
    assert f.severity == "high"  # confidential maps to high severity
    assert "confidential" in f.detail.lower()


def test_tier_c_no_context_no_confidential_match(monkeypatch):
    """Same text without context = no confidential finding from mock."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    findings = tier_c_check_semantic("the workspaceversum build ships phase 3 in june.")
    assert findings == []  # "workspaceversum" not in any PII pattern
