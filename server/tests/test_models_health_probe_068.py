# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the endpoint probe in models_registry.health_check (0.6.8.2 P0).

The BYOM walkthrough implies ``workspaces models list --health`` covers server
reachability. Until this patch, only the on-disk artifact was checked. These
tests pin the new behaviour:

- No URL configured → ``endpoint_reachable=None`` with explanatory note.
- URL unreachable → ``endpoint_reachable=False`` + error string.
- URL reachable, model listed → ``endpoint_reachable=True``.
- URL reachable, model NOT listed → ``endpoint_reachable=False`` + explainer.
- CLI integration surfaces all three states.
"""

from __future__ import annotations

import io
import json as _json
import urllib.error

import pytest

from workspaces import models_registry
from workspaces.models_registry import ModelEntry, health_check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict):
        self._buf = io.BytesIO(_json.dumps(payload).encode("utf-8"))

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_entry(tmp_path, model_id="phi-3.5-mini-q4", role="validator",
                with_artifact=True):
    """Build a ModelEntry pointing at a real on-disk file so the artifact
    side of health_check returns ``ok`` and we can focus on the endpoint."""
    artifact = tmp_path / f"{model_id}.gguf"
    if with_artifact:
        artifact.write_bytes(b"GGUF\x00" * 16)
    return ModelEntry(
        id=model_id,
        artifact_path=str(artifact),
        roles=[role],
    )


# ---------------------------------------------------------------------------
# No URL configured
# ---------------------------------------------------------------------------


def test_health_check_no_url_returns_none_for_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKSPACE_LOCAL_LLM_URL", raising=False)
    entry = _make_entry(tmp_path)
    result = health_check(entry)
    assert result["status"] == "ok"
    assert result["artifact_exists"] is True
    assert result["endpoint_reachable"] is None
    assert "no endpoint configured" in result["endpoint_error"]


# ---------------------------------------------------------------------------
# URL unreachable
# ---------------------------------------------------------------------------


def test_health_check_url_unreachable_returns_false_with_error(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://127.0.0.1:1/v1")

    def _boom(req, timeout=None):  # noqa: ARG001
        raise urllib.error.URLError("Connection refused")

    # urllib is imported lazily inside _probe_endpoint, so patch the global
    # stdlib symbol — that's what the import resolves to.
    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", _boom)

    entry = _make_entry(tmp_path)
    result = health_check(entry)
    assert result["endpoint_reachable"] is False
    assert "Connection refused" in result["endpoint_error"] \
        or "unreachable" in result["endpoint_error"]


# ---------------------------------------------------------------------------
# URL reachable, model listed
# ---------------------------------------------------------------------------


def test_health_check_url_reachable_with_model_returns_true(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:1234/v1")
    payload = {
        "object": "list",
        "data": [
            {"id": "phi-3.5-mini-q4", "object": "model"},
            {"id": "qwen-2.5-coder-3b-q4", "object": "model"},
        ],
    }

    def _ok(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(payload)

    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", _ok)

    entry = _make_entry(tmp_path, model_id="phi-3.5-mini-q4")
    result = health_check(entry)
    assert result["endpoint_reachable"] is True
    assert result["endpoint_url"] == "http://localhost:1234/v1"
    assert result["endpoint_error"] == ""


# ---------------------------------------------------------------------------
# URL reachable, model NOT listed
# ---------------------------------------------------------------------------


def test_health_check_url_reachable_without_model_returns_false(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:1234/v1")
    payload = {
        "object": "list",
        "data": [
            {"id": "llama-3-8b", "object": "model"},
        ],
    }

    def _ok(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(payload)

    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", _ok)

    entry = _make_entry(tmp_path, model_id="phi-3.5-mini-q4")
    result = health_check(entry)
    assert result["endpoint_reachable"] is False
    assert "phi-3.5-mini-q4" in result["endpoint_error"]


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_workspaces_models_list_health_shows_endpoint_status(
    tmp_path, monkeypatch, capsys,
):
    """The `workspaces models list --health` table must surface artifact + endpoint."""
    # Hermetic registry under tmp.
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:1234/v1")

    # Register a model + create the artifact so artifact_exists=True.
    artifact = tmp_path / "phi.gguf"
    artifact.write_bytes(b"GGUF\x00" * 16)
    models_registry.register_model(
        "phi-3.5-mini-q4",
        role="validator",
        artifact_path=str(artifact),
    )

    payload = {
        "object": "list",
        "data": [{"id": "phi-3.5-mini-q4", "object": "model"}],
    }

    def _ok(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(payload)

    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", _ok)

    from workspaces.cli import main
    rc = main(["models", "list", "--health"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "phi-3.5-mini-q4" in out
    assert "role=validator" in out
    assert "artifact=" in out
    assert "endpoint=" in out
    # When the URL is reachable AND the model is in the listing, the column
    # should render the success glyph.
    assert "endpoint=✓" in out


def test_workspaces_models_list_health_shows_no_endpoint_when_unconfigured(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("WORKSPACE_LOCAL_LLM_URL", raising=False)

    artifact = tmp_path / "phi.gguf"
    artifact.write_bytes(b"GGUF\x00" * 16)
    models_registry.register_model(
        "phi-3.5-mini-q4",
        role="validator",
        artifact_path=str(artifact),
    )

    from workspaces.cli import main
    rc = main(["models", "list", "--health"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no endpoint configured" in out


def test_health_check_back_compat_keys_preserved(tmp_path, monkeypatch):
    """The 0.6.8.1 keys (status / detail / size_bytes / exists) MUST stay
    so older callers don't break."""
    monkeypatch.delenv("WORKSPACE_LOCAL_LLM_URL", raising=False)
    entry = _make_entry(tmp_path)
    result = health_check(entry)
    for key in ("id", "status", "detail", "size_bytes", "exists"):
        assert key in result, f"back-compat key {key!r} missing"
    assert result["status"] == "ok"
    assert result["exists"] is True
