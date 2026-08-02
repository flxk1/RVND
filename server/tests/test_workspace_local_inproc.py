# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""In-process local tier + multi-tier cascade assembly.

The installer pulls 2-3 GGUFs that run in-process (no daemon); these tests prove
the cascade builds an ordered local cascade from them and routes in-process vs
HTTP rungs through one dispatching completer. Generation itself needs
llama-cpp-python + a real GGUF, so the routing is exercised with fakes.
"""
import os

from workspaces import workspace_local_inproc as inproc
from workspaces.workspace_cascade import (tiers_for_workspace, write_local_config,
                                cascade_for_workspace, LOCAL_URL_ENV, LOCAL_MODEL_ENV,
                                CONFIG_PATH_ENV)

_CLOUD = ("WORKSPACE_CLOUD_LLM_URL", "WORKSPACE_CLOUD_LLM_MODEL", "WORKSPACE_CLOUD_API_KEY")


def _no_env(monkeypatch):
    for k in (LOCAL_URL_ENV, LOCAL_MODEL_ENV, "LOCAL_CODER_MODEL", *_CLOUD):
        monkeypatch.delenv(k, raising=False)


def test_is_inproc_sentinel():
    assert inproc.is_inproc("inproc")
    assert inproc.is_inproc("inproc:/models/x.gguf")
    assert not inproc.is_inproc("http://localhost:8080/v1")
    assert not inproc.is_inproc("")


def test_resolve_gguf_direct_path(tmp_path):
    g = tmp_path / "m.gguf"
    g.write_bytes(b"\x00")
    assert inproc.resolve_gguf(str(g)) == str(g)
    assert inproc.resolve_gguf(str(tmp_path / "absent.gguf")) == ""
    assert inproc.resolve_gguf("") == ""


def test_complete_inproc_missing_model_is_loud(tmp_path):
    r = inproc.complete_inproc("inproc", str(tmp_path / "nope.gguf"), "hi")
    assert r["ok"] is False and "not found" in r["error"]


def test_workspace_completer_routes(monkeypatch):
    calls = {}

    def fake_inproc(url, model, prompt, **kw):
        calls["inproc"] = (url, model)
        return {"ok": True, "response": "in", "usage": {"total_tokens": 3}}

    def fake_http(url, model, prompt, **kw):
        calls["http"] = (url, model)
        return {"ok": True, "response": "out", "usage": {"total_tokens": 3}}

    monkeypatch.setattr(inproc, "complete_inproc", fake_inproc)
    monkeypatch.setattr("workspaces.local_llm.complete_via", fake_http)

    r1 = inproc.workspace_completer("inproc", "phi.gguf", "x")
    assert r1["response"] == "in" and "inproc" in calls
    r2 = inproc.workspace_completer("http://localhost:8/v1", "qwen", "x")
    assert r2["response"] == "out" and "http" in calls


def test_config_list_builds_ordered_local_tiers(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "local-llm.json"))
    write_local_config(local_models=[
        {"model": "phi-3.5-mini-q4"},          # in-process (no url)
        {"model": "qwen-2.5-coder-3b-q4"},
        {"model": "qwen-2.5-coder-7b-q4"},
    ])
    tiers = tiers_for_workspace()
    assert [t.name for t in tiers] == [
        "local-phi-3.5-mini-q4", "local-qwen-2.5-coder-3b-q4",
        "local-qwen-2.5-coder-7b-q4"]
    assert all(t.url == "inproc" and not t.is_cloud for t in tiers)
    assert all(t.configured for t in tiers)    # inproc tiers count as configured


def test_multi_local_cascade_serves_first_passing(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "local-llm.json"))
    write_local_config(local_models=[{"model": "small"}, {"model": "big"}])

    seen = []

    def fake(url, model, prompt, **kw):
        seen.append(model)
        # small defers (empty -> nonempty_verifier rejects), big answers
        return ({"ok": True, "response": "", "usage": {"total_tokens": 1}}
                if model == "small"
                else {"ok": True, "response": "answer", "usage": {"total_tokens": 5}})

    r = cascade_for_workspace(tmp_path / "c", "q", completer=fake,
                         log_root=tmp_path / "l")
    assert r["ok"] is True
    assert r["served_by"] == "local-big"
    assert seen == ["small", "big"]            # tried cheap first, then capable
    assert r["served_is_cloud"] is False
