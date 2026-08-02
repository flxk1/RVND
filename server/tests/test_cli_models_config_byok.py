# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""``workspaces models config`` BYOK paths and brokered cloud credentials.

Workspaces ships the governed cascade, not the model: a user wires their own
OpenAI-compatible local endpoint (Ollama/LM Studio/vLLM) and/or their own cloud
credentials supplied by the active track's egress connector.
"""
import json

from workspaces import cli
from workspaces.workspace_cascade import tiers_for_workspace


def _cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_LLM_CONFIG", str(tmp_path / "local-llm.json"))
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", str(tmp_path / "models"))  # empty registry
    for k in ("WORKSPACE_LOCAL_LLM_URL", "WORKSPACE_LOCAL_LLM_MODEL", "LOCAL_CODER_MODEL",
              "WORKSPACE_CLOUD_LLM_URL", "WORKSPACE_CLOUD_LLM_MODEL", "WORKSPACE_CLOUD_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_byok_local_endpoint(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    rc = cli.main(["models", "config", "--local-url", "http://localhost:11434/v1",
                   "--local-model", "qwen2.5-coder:7b"])
    assert rc == 0
    tiers = tiers_for_workspace()
    assert len(tiers) == 1
    assert tiers[0].url == "http://localhost:11434/v1"
    assert tiers[0].model == "qwen2.5-coder:7b"
    assert tiers[0].is_cloud is False


def test_byok_local_plus_cloud_ignores_deprecated_raw_key(
    monkeypatch, tmp_path, capsys,
):
    _cfg(monkeypatch, tmp_path)
    rc = cli.main(["models", "config",
                   "--local-url", "http://localhost:11434/v1", "--local-model", "qwen",
                   "--cloud-url", "https://api.example.com/v1",
                   "--cloud-model", "big", "--cloud-api-key",
                   "test-credential-value"])
    assert rc == 0
    output = capsys.readouterr()
    assert "deprecated and ignored" in output.err
    assert "test-credential-value" not in output.out + output.err
    names = [(t.name, t.is_cloud) for t in tiers_for_workspace()]
    assert names == [("local", False), ("cloud", True)]
    cloud = tiers_for_workspace()[-1]
    assert cloud.configured is False
    cfg = json.loads((tmp_path / "local-llm.json").read_text())
    assert "api_key" not in cfg["cloud"]


def test_nothing_to_configure_is_loud(monkeypatch, tmp_path, capsys):
    _cfg(monkeypatch, tmp_path)
    rc = cli.main(["models", "config"])          # no flags, empty registry
    assert rc == 2
    assert "nothing to configure" in capsys.readouterr().err


def test_config_show_redacts_legacy_raw_key(monkeypatch, tmp_path, capsys):
    _cfg(monkeypatch, tmp_path)
    config = tmp_path / "local-llm.json"
    config.write_text(json.dumps({
        "cloud": {
            "url": "https://cloud.example/v1",
            "model": "model-a",
            "api_key": "legacy-credential-value",
        },
    }), encoding="utf-8")

    assert cli.main(["models", "config-show"]) == 0
    output = capsys.readouterr().out
    assert "legacy-credential-value" not in output
    assert "api_key" not in output
    assert "https://cloud.example/v1" in output
