# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governed workspace cascade: loud when no tier, local-first served + recorded,
and configurable once via the installer-written config file."""
import json

import pytest

from rvnd.cascade_binding import (cascade_for_workspace, tiers_for_workspace,
                                write_local_config, config_path, _local_config,
                                LOCAL_URL_ENV, LOCAL_MODEL_ENV, CONFIG_PATH_ENV)

_CLOUD = ("WORKSPACE_CLOUD_LLM_URL", "WORKSPACE_CLOUD_LLM_MODEL", "WORKSPACE_CLOUD_API_KEY")
_CODER = "LOCAL_CODER_MODEL"


def _no_env(monkeypatch, tmp_path=None):
    for k in (LOCAL_URL_ENV, LOCAL_MODEL_ENV, _CODER, *_CLOUD):
        monkeypatch.delenv(k, raising=False)
    # isolate the models registry so the local-first auto-discovery fallback
    # can't pick up a real ~/.workspace/models registry on the test machine.
    empty = (tmp_path or __import__("pathlib").Path("/nonexistent")) / "empty-registry"
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", str(empty))


def _fake_ok(url, model, prompt, *, api_key="", temperature=0.0,
             max_tokens=512, timeout=30.0):
    return {"ok": True, "response": "def f(): return 1",
            "usage": {"total_tokens": 7}, "latency_ms": 2}


def test_no_tier_is_loud(tmp_path, monkeypatch):
    _no_env(monkeypatch, tmp_path)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "absent.json"))  # no config
    r = cascade_for_workspace(tmp_path / "c", "hi", log_root=tmp_path / "l")
    assert r["ok"] is False
    assert "no model tier" in r["error"]
    assert r["advice"]


def test_config_file_supplies_a_local_tier(tmp_path, monkeypatch):
    _no_env(monkeypatch, tmp_path)
    cfg = tmp_path / "local-llm.json"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(cfg))
    assert config_path() == cfg
    written = write_local_config(local_url="http://localhost:8080/v1",
                                 local_model="qwen2.5-coder")
    assert written == cfg and cfg.exists()
    tiers = tiers_for_workspace()                       # no envs — config drives it
    assert len(tiers) == 1 and tiers[0].name == "local"
    assert tiers[0].model == "qwen2.5-coder"
    # end to end: served locally with no env set
    r = cascade_for_workspace(tmp_path / "c", "x", completer=_fake_ok,
                         log_root=tmp_path / "l")
    assert r["ok"] is True and r["served_by"] == "local"


def test_env_wins_over_config(tmp_path, monkeypatch):
    _no_env(monkeypatch, tmp_path)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "local-llm.json"))
    write_local_config(local_url="http://config:1/v1", local_model="from-config")
    monkeypatch.setenv(LOCAL_URL_ENV, "http://env:2/v1")
    monkeypatch.setenv(LOCAL_MODEL_ENV, "from-env")
    tiers = tiers_for_workspace()
    assert tiers[0].url == "http://env:2/v1" and tiers[0].model == "from-env"


def test_write_merges_without_clobbering(tmp_path, monkeypatch):
    _no_env(monkeypatch, tmp_path)
    config = tmp_path / "local-llm.json"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config))
    config.write_text(json.dumps({
        "cloud": {
            "url": "http://c/v1",
            "model": "big",
            "price_per_1k": 0.4,
            "api_key": "legacy-credential-value",
        },
    }), encoding="utf-8")
    assert "api_key" not in _local_config()["cloud"]
    write_local_config(local_url="http://l/v1", local_model="small")  # merge
    stored = json.loads(config.read_text(encoding="utf-8"))
    assert "api_key" not in stored["cloud"]
    assert stored["cloud"]["price_per_1k"] == 0.4
    tiers = tiers_for_workspace(
        capability_token="RVSC1.test.signature",
        track_id="cloud-primary",
    )
    names = {t.name for t in tiers}
    assert names == {"local", "cloud"}             # both survived
    cloud = next(t for t in tiers if t.is_cloud)
    assert cloud.api_key == ""
    assert cloud.proxy_url == "http://127.0.0.1:8443/v1"


def test_direct_raw_cloud_key_is_ignored(tmp_path, monkeypatch):
    _no_env(monkeypatch, tmp_path)
    config = tmp_path / "local-llm.json"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config))

    with pytest.warns(DeprecationWarning, match="deprecated and ignored"):
        write_local_config(
            cloud_url="https://cloud.example/v1",
            cloud_model="model-a",
            cloud_api_key="test-credential-value",
        )

    assert "api_key" not in json.loads(config.read_text())["cloud"]


@pytest.mark.parametrize("url", [
    "https://user:pass" + "\x40" + "cloud.example/v1",
    "https://cloud.example/v1?token=test-value",
    "https://cloud.example/v1?API_KEY=test-value",
])
def test_endpoint_url_rejects_embedded_credentials(tmp_path, monkeypatch, url):
    _no_env(monkeypatch, tmp_path)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "local-llm.json"))

    with pytest.raises(ValueError, match="credentials|credential query") as exc:
        write_local_config(cloud_url=url, cloud_model="model-a")

    assert "test-value" not in str(exc.value)


def test_workspace_standard_is_the_single_default_local_tier(tmp_path, monkeypatch):
    """The workspace has ONE standard local model. With a 'workspace'-role model
    registered, that is the single default local tier — the Code Companion's
    own model (code-fix) is NOT folded into the workspace cascade."""
    _no_env(monkeypatch, tmp_path)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "absent.json"))  # no config
    reg = tmp_path / "models"
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", str(reg))
    from rvnd import models_registry
    # workspace standard (qwen-3b) + the coder's own model (qwen-7b, code-fix)
    for mid, role, n in (("qwen2_5-coder-3b-q4", "workspace", 4),
                         ("qwen2_5-coder-7b-q4", "code-fix", 8)):
        d = reg / mid; d.mkdir(parents=True)
        g = d / f"{mid}.gguf"; g.write_bytes(b"\x00" * n)
        models_registry.register_model(mid, role, artifact_path=str(g), via="pull")
    tiers = tiers_for_workspace()
    assert len(tiers) == 1                              # ONE standard, not a pile
    assert tiers[0].name == "local"
    assert tiers[0].model.endswith("qwen2_5-coder-3b-q4.gguf")   # the workspace role
    assert tiers[0].url == "inproc"


def test_no_workspace_role_falls_back_to_single_smallest(tmp_path, monkeypatch):
    _no_env(monkeypatch, tmp_path)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "absent.json"))
    reg = tmp_path / "models"
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", str(reg))
    from rvnd import models_registry
    for mid, n in (("phi-3_5-mini-q4", 2), ("qwen2_5-coder-7b-q4", 8)):
        d = reg / mid; d.mkdir(parents=True)
        g = d / f"{mid}.gguf"; g.write_bytes(b"\x00" * n)
        models_registry.register_model(mid, "drafter", artifact_path=str(g), via="pull")
    tiers = tiers_for_workspace()
    assert len(tiers) == 1
    assert tiers[0].model.endswith("phi-3_5-mini-q4.gguf")       # the smallest


def test_served_locally_records_and_reports(tmp_path, monkeypatch):
    monkeypatch.setenv(LOCAL_URL_ENV, "http://localhost:9/v1")
    monkeypatch.setenv(LOCAL_MODEL_ENV, "dummy-local")
    for k in _CLOUD:
        monkeypatch.delenv(k, raising=False)
    r = cascade_for_workspace(tmp_path / "c", "write a function",
                         completer=_fake_ok, log_root=tmp_path / "l")
    assert r["ok"] is True
    assert r["served_by"] == "local"
    assert r["served_is_cloud"] is False        # cloud never touched
    assert r["audit_id"]                          # recorded on the chain
    assert "ledger" in r
