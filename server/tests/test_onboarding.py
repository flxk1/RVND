# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the onboarding wizard + config persistence + binary entrypoint."""

from __future__ import annotations

import io


from workspaces.lock.onboarding import (
    Config,
    default_config_path,
    load_config,
    run_wizard,
    save_config,
)
from workspaces.lock.onboarding.config import apply_config_to_env
from workspaces.lock.onboarding.wizard import (
    _detect_environment,
    _find_bundled_models,
    _find_existing_user_models,
    _smoke_test_backend,
)


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


def test_default_config_returns_mock_backend():
    cfg = Config()
    assert cfg.backend_spec == "mock"


def test_load_config_from_nonexistent_path_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.backend_spec == "mock"


def test_save_and_load_roundtrip(tmp_path):
    cfg = Config(
        backend_spec="llama_cpp:/path.gguf",
        audit_log_path="/tmp/audit.jsonl",
        default_mode="strict",
        default_oversight="supervised",
    )
    path = tmp_path / "config.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.backend_spec == "llama_cpp:/path.gguf"
    assert loaded.audit_log_path == "/tmp/audit.jsonl"
    assert loaded.default_mode == "strict"
    assert loaded.default_oversight == "supervised"


def test_load_corrupt_config_returns_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("this is not json")
    cfg = load_config(path)
    assert cfg.backend_spec == "mock"


def test_default_config_path_is_under_home():
    path = default_config_path()
    assert "agent-tool-lock" in str(path)


def test_apply_config_sets_env(monkeypatch):
    monkeypatch.delenv("AGENT_TOOL_LOCK_LLM_BACKEND", raising=False)
    cfg = Config(backend_spec="mock", default_mode="standard")
    apply_config_to_env(cfg)
    import os
    assert os.environ.get("AGENT_TOOL_LOCK_LLM_BACKEND") == "mock"


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------


def test_detect_environment_returns_expected_keys():
    env = _detect_environment()
    assert "is_pyinstaller" in env
    assert "runtime_dir" in env
    assert "python_version" in env
    assert "platform" in env


def test_environment_is_not_pyinstaller_in_dev():
    env = _detect_environment()
    assert env["is_pyinstaller"] is False  # we're running under pytest, not the binary


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------


def test_find_bundled_models_empty_when_no_dir(tmp_path):
    found = _find_bundled_models(tmp_path)
    assert found == []


def test_find_bundled_models_finds_gguf(tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "phi-3.5-mini.gguf").touch()
    (models_dir / "llama-3.2-3b.gguf").touch()
    (models_dir / "not-a-model.txt").touch()
    found = _find_bundled_models(tmp_path)
    assert len(found) == 2
    assert all(f.suffix == ".gguf" for f in found)


def test_find_existing_user_models_returns_list():
    # We can't reliably set up user dirs in a sandbox; just confirm it returns a list
    found = _find_existing_user_models()
    assert isinstance(found, list)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def test_smoke_test_mock_backend_passes():
    result = _smoke_test_backend("mock")
    assert result["ok"] is True
    assert len(result["results"]) == 3


def test_smoke_test_unavailable_backend_fails():
    result = _smoke_test_backend("llama_cpp:/nonexistent.gguf")
    assert result["ok"] is False
    assert "unavailable" in result["reason"].lower() or "not available" in result["reason"].lower()


def test_smoke_test_invalid_spec_fails():
    result = _smoke_test_backend("nonexistent:foo")
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# Full wizard run
# ---------------------------------------------------------------------------


def test_wizard_runs_with_mock_backend_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.json"

    stdout = io.StringIO()
    # Accept all defaults (recommended = mock since no bundled GGUF in tmp_path);
    # then leave audit log empty
    result = run_wizard(
        stdin=io.StringIO("\n\n"),
        stdout=stdout,
        config_path=config_path,
    )

    assert result.completed is True
    assert result.smoke_test_passed is True
    assert config_path.exists()
    assert result.config.backend_spec == "mock"

    # Output contains the expected section headers
    out = stdout.getvalue()
    assert "agent-tool-lock setup wizard" in out
    assert "Stage 2 — model discovery" in out
    assert "Stage 3 — choose backend" in out
    assert "Stage 4 — smoke test" in out
    assert "Stage 5 — persist config" in out
    assert "setup complete" in out


def test_wizard_with_audit_log_path(tmp_path):
    config_path = tmp_path / "config.json"
    audit_path = str(tmp_path / "audit.jsonl")

    result = run_wizard(
        stdin=io.StringIO(f"\n{audit_path}\n"),  # accept backend, set audit log
        stdout=io.StringIO(),
        config_path=config_path,
    )

    assert result.config.audit_log_path == audit_path


def test_wizard_with_skip_smoke_test(tmp_path):
    config_path = tmp_path / "config.json"
    result = run_wizard(
        stdin=io.StringIO("\n\n"),
        stdout=io.StringIO(),
        config_path=config_path,
        skip_smoke_test=True,
    )
    assert result.completed is True


def test_wizard_with_auto_answers(tmp_path):
    config_path = tmp_path / "config.json"
    result = run_wizard(
        stdout=io.StringIO(),
        config_path=config_path,
        auto_answers=["", ""],  # backend=default, audit=skip
    )
    assert result.completed is True
    assert result.config.backend_spec == "mock"


def test_wizard_falls_back_to_mock_on_unavailable_backend(tmp_path):
    config_path = tmp_path / "config.json"
    # Try to set llama_cpp with a nonexistent path; smoke test should fail; falls back to mock
    result = run_wizard(
        stdin=io.StringIO("llama_cpp:/nonexistent.gguf\n\n"),
        stdout=io.StringIO(),
        config_path=config_path,
    )
    # The wizard's smoke test catches the unavailable backend and rewrites spec to mock
    assert result.config.backend_spec == "mock"


def test_wizard_persists_setup_timestamp(tmp_path):
    config_path = tmp_path / "config.json"
    result = run_wizard(
        stdin=io.StringIO("\n\n"),
        stdout=io.StringIO(),
        config_path=config_path,
    )
    assert result.config.setup_completed_at
    # ISO-8601 format
    assert "T" in result.config.setup_completed_at


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------


def test_main_help_returns_zero():
    from workspaces.lock.__main__ import main
    rc = main(["--help"])
    assert rc == 0


def test_main_unknown_subcommand_returns_one():
    from workspaces.lock.__main__ import main
    rc = main(["nonexistent-subcommand"])
    assert rc == 1


def test_main_no_argv_returns_one():
    from workspaces.lock.__main__ import main
    rc = main([])
    assert rc == 1


def test_main_doctor_runs_and_exits_zero(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from workspaces.lock.__main__ import main
    rc = main(["doctor"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "agent-tool-lock doctor" in captured.out
    assert "Tier C status" in captured.out
