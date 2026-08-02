# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tier C backend resolution — the 'auto' spec ties Tier C to the pulled model.

Default stays "mock" (fast, no model loaded per ingest). 'auto' resolves a real
local GGUF from the workspace models registry so the same model the cascade pulls
serves Tier C too. String resolution only — no model is loaded here.
"""
from workspaces.lock import tier_c


def _iso_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", str(tmp_path / "models"))
    tier_c.reset_backend_cache()


def test_unset_defaults_to_mock(monkeypatch, tmp_path):
    _iso_registry(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_TOOL_LOCK_LLM_BACKEND", raising=False)
    assert tier_c.tier_c_spec() == "mock"


def test_explicit_spec_passes_through(monkeypatch, tmp_path):
    _iso_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "llama_cpp:/models/x.gguf")
    assert tier_c.tier_c_spec() == "llama_cpp:/models/x.gguf"


def test_auto_with_empty_registry_falls_back_to_mock(monkeypatch, tmp_path):
    _iso_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "auto")
    assert tier_c.tier_c_spec() == "mock"


def test_auto_prefers_lock_role_model(monkeypatch, tmp_path):
    _iso_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "auto")
    from workspaces import models_registry
    reg = tmp_path / "models"
    # a drafter model (big) and a lock-role model (small) both on disk
    for mid, role, n in (("qwen2_5-coder-7b-q4", "drafter", 8),
                         ("phi-3_5-mini-q4", "lock-c", 2)):
        d = reg / mid; d.mkdir(parents=True)
        g = d / f"{mid}.gguf"; g.write_bytes(b"\x00" * n)
        models_registry.register_model(mid, role, artifact_path=str(g), via="pull")
    spec = tier_c.tier_c_spec()
    assert spec == f"llama_cpp:{reg / 'phi-3_5-mini-q4' / 'phi-3_5-mini-q4.gguf'}"


def test_auto_falls_back_to_any_gguf(monkeypatch, tmp_path):
    _iso_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "auto")
    from workspaces import models_registry
    reg = tmp_path / "models"
    d = reg / "qwen2_5-coder-3b-q4"; d.mkdir(parents=True)
    g = d / "qwen2_5-coder-3b-q4.gguf"; g.write_bytes(b"\x00\x00")
    models_registry.register_model("qwen2_5-coder-3b-q4", "drafter",
                                   artifact_path=str(g), via="pull")
    assert tier_c.tier_c_spec() == f"llama_cpp:{g}"
