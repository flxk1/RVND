# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The lock onboarding ops on the workspace_lock facade.

``setup_status`` reads the persisted onboarding config (the drawer's setup-CTA
signal); ``setup`` runs the CLI wizard headlessly. The loosening guard is the
load-bearing part: a real backend never degrades to mock — by request or by
smoke-test fallback — without accepted_by + reason.
"""
from __future__ import annotations

import json

import pytest

from workspaces import mcp_server as srv
from workspaces.lock.onboarding.config import Config, load_config, save_config
from workspaces.lock.tier_c import reset_backend_cache

_WIZARD_ENV = ("AGENT_TOOL_LOCK_LLM_BACKEND", "AGENT_TOOL_LOCK_AUDIT_LOG",
               "AGENT_TOOL_LOCK_DEFAULT_MODE", "AGENT_TOOL_LOCK_DEFAULT_OVERSIGHT")


@pytest.fixture(autouse=True)
def _isolate_wizard_env(monkeypatch):
    """The wizard applies its config to the process env; keep that from leaking
    into other tests (Tier C reads these vars)."""
    import os
    saved = {k: os.environ.get(k) for k in _WIZARD_ENV}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    reset_backend_cache()


def _status(path):
    return srv.workspace_lock("setup_status", {"config_path": str(path)})


def _setup(path, **kw):
    return srv.workspace_lock("setup", {"config_path": str(path), **kw})


def test_setup_status_unconfigured(tmp_path):
    out = _status(tmp_path / "config.json")
    assert out["ok"] is True and out["configured"] is False
    assert out["config_exists"] is False
    assert out["backend_spec"] == "mock"          # the default, not a completed setup


def test_setup_runs_headlessly_and_status_flips(tmp_path):
    cfg = tmp_path / "config.json"
    out = _setup(cfg, skip_smoke_test=True)
    assert out["ok"] is True
    assert out["config_path"] == str(cfg)
    assert "setup complete" in out["transcript"]
    status = _status(cfg)
    assert status["configured"] is True and status["config_exists"] is True


def test_setup_accepts_explicit_backend_spec(tmp_path):
    cfg = tmp_path / "config.json"
    out = _setup(cfg, backend_spec="mock", skip_smoke_test=True)
    assert out["ok"] is True and out["backend_spec"] == "mock"
    assert load_config(cfg).backend_spec == "mock"


def test_downgrade_real_to_mock_refused_without_ack(tmp_path):
    cfg = tmp_path / "config.json"
    save_config(Config(backend_spec="llama_cpp:/models/x.gguf",
                       setup_completed_at="2026-07-01T00:00:00Z"), path=cfg)
    out = _setup(cfg, backend_spec="mock", skip_smoke_test=True)
    assert out["ok"] is False and "accepted_by" in out["error"]
    # prior config untouched
    assert load_config(cfg).backend_spec == "llama_cpp:/models/x.gguf"


def test_downgrade_real_to_mock_allowed_with_ack(tmp_path):
    cfg = tmp_path / "config.json"
    save_config(Config(backend_spec="llama_cpp:/models/x.gguf",
                       setup_completed_at="2026-07-01T00:00:00Z"), path=cfg)
    out = _setup(cfg, backend_spec="mock", skip_smoke_test=True,
                 accepted_by="ann", reason="no GGUF on this laptop")
    assert out["ok"] is True and out["backend_spec"] == "mock"
    assert out["accepted_by"] == "ann"
    assert load_config(cfg).backend_spec == "mock"


def test_smoke_fallback_to_mock_keeps_prior_real_config(tmp_path):
    cfg = tmp_path / "config.json"
    save_config(Config(backend_spec="llama_cpp:/models/x.gguf",
                       setup_completed_at="2026-07-01T00:00:00Z"), path=cfg)
    # A GGUF path that cannot load → the wizard's smoke test fails and falls
    # back to mock; without an acknowledgement the op must keep the prior config.
    out = _setup(cfg, backend_spec="llama_cpp:/nonexistent/model.gguf")
    assert out["ok"] is False and "prior config kept" in out["error"]
    assert load_config(cfg).backend_spec == "llama_cpp:/models/x.gguf"


def test_setup_result_is_json_serialisable_and_secret_free(tmp_path):
    out = _setup(tmp_path / "config.json", skip_smoke_test=True)
    json.dumps(out)                                # MCP boundary shape
    assert "passphrase" not in json.dumps(out).lower()


def test_help_lists_the_new_ops():
    ops = {o["op"] for o in srv.workspace_lock("help")["ops"]}
    assert {"setup", "setup_status"} <= ops
