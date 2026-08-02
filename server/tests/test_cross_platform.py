# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Cross-platform guards — the OS-sensitive paths that differ on Windows.

These run on every CI OS (ubuntu/macos/windows in tests.yml). They assert the
file-location, permission-hardening, and path-normalization code does not assume
POSIX. Kept fast and dependency-light so they gate every platform cheaply.
"""
import os
from pathlib import Path

import pytest

from workspaces import workspace_cascade
from workspaces.workspace_cascade import (config_path, write_local_config, _local_config,
                                tiers_for_workspace, CONFIG_PATH_ENV, LOCAL_URL_ENV,
                                LOCAL_MODEL_ENV)


def _no_local_env(monkeypatch):
    for k in (LOCAL_URL_ENV, LOCAL_MODEL_ENV, "LOCAL_CODER_MODEL",
              "WORKSPACE_CLOUD_LLM_URL", "WORKSPACE_CLOUD_LLM_MODEL", "WORKSPACE_CLOUD_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_config_path_honours_override(tmp_path, monkeypatch):
    target = tmp_path / "x" / "local-llm.json"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(target))
    assert config_path() == target


def test_config_path_default_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))           # POSIX home
    monkeypatch.setenv("USERPROFILE", str(tmp_path))    # Windows home
    p = config_path()
    # resolves to a real path under the home dir on any OS (no hard-coded "/")
    assert p.name == "local-llm.json"
    assert "workspace" in p.parts


def test_config_roundtrip_is_os_neutral(tmp_path, monkeypatch):
    _no_local_env(monkeypatch)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "local-llm.json"))
    write_local_config(local_models=[{"model": "phi"}, {"model": "qwen"}])
    cfg = _local_config()
    assert [e["model"] for e in cfg["local"]] == ["phi", "qwen"]
    tiers = tiers_for_workspace()
    assert [t.name for t in tiers] == ["local-phi", "local-qwen"]


def test_chmod_failure_does_not_break_write(tmp_path, monkeypatch):
    """On Windows, chmod 0o600 has limited effect and can raise; the config
    write must still succeed (the chmod is wrapped)."""
    _no_local_env(monkeypatch)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "local-llm.json"))

    real_chmod = Path.chmod

    def boom(self, *a, **k):
        raise PermissionError("simulated Windows ACL")

    monkeypatch.setattr(Path, "chmod", boom)
    try:
        p = write_local_config(local_model="m", local_url="http://h/v1")
        assert p.exists()                      # write survived chmod failure
    finally:
        monkeypatch.setattr(Path, "chmod", real_chmod)


def test_folder_hash_normalizes_path(tmp_path):
    """folder_hash must be stable regardless of trailing slash / case quirks
    so logs resolve to one workspace across OSes."""
    from workspaces.mutation_log import folder_hash
    a = folder_hash(str(tmp_path))
    b = folder_hash(str(tmp_path) + os.sep)
    assert a == b
