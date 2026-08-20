# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the namespace back-compat shim (``workspaces._namespace``).

These tests verify that installs predating the ``WORKSPACEVERSUM_*`` →
``WORKSPACE_*`` rename keep working without manual migration:

- Legacy env vars resolve to their modern equivalents with a one-time
  deprecation warning.
- Modern env vars win when both are set (no surprises for already-migrated users).
- The legacy folder-policy filename is still read by :func:`load_policy`.
- :func:`save_policy` always writes the modern filename, silently superseding
  any legacy file in the same folder.

These tests do NOT exercise the ``~/.workspaceversum/`` → ``~/.workspace/`` home-dir
migration (that's covered by ``test_home_dir_migration_068.py`` or similar
integration tests because it touches the user's actual home — keeping it
out of the unit suite avoids env-coupled flakiness).
"""

from __future__ import annotations

import json
import warnings


from workspaces import _namespace
from workspaces.policy import (
    LEGACY_POLICY_FILENAME,
    POLICY_FILENAME,
    FolderPolicy,
    load_policy,
    save_policy,
)


# ---------------------------------------------------------------------------
# Env-var back-compat
# ---------------------------------------------------------------------------


def test_legacy_env_var_copied_to_modern(monkeypatch):
    """A bare ``WORKSPACEVERSUM_X`` should populate ``WORKSPACE_X`` on shim run."""
    monkeypatch.delenv("WORKSPACE_LOCAL_LLM_URL", raising=False)
    monkeypatch.setenv("WORKSPACEVERSUM_LOCAL_LLM_URL", "http://localhost:1234/v1")
    # Re-run the shim manually (idempotent — module import already ran it once,
    # but the env state we set now is fresh, so re-invocation picks it up).
    _namespace._warned.clear()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        _namespace.migrate_legacy_env_vars()
    import os

    assert os.environ["WORKSPACE_LOCAL_LLM_URL"] == "http://localhost:1234/v1"
    # Exactly one deprecation warning for this var.
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("WORKSPACEVERSUM_LOCAL_LLM_URL" in str(w.message) for w in deprecations)


def test_modern_env_var_wins_when_both_set(monkeypatch):
    """If the user already migrated, ``WORKSPACE_X`` should be preserved untouched."""
    monkeypatch.setenv("WORKSPACEVERSUM_LOCAL_LLM_URL", "http://legacy/")
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://modern/")
    _namespace._warned.clear()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        _namespace.migrate_legacy_env_vars()
    import os

    assert os.environ["WORKSPACE_LOCAL_LLM_URL"] == "http://modern/"
    # No warning when the user already uses the modern name.
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not deprecations


def test_neither_env_var_set_is_noop(monkeypatch):
    """If neither legacy nor modern is set, the shim must not error or set anything."""
    monkeypatch.delenv("WORKSPACE_LOCAL_LLM_URL", raising=False)
    monkeypatch.delenv("WORKSPACEVERSUM_LOCAL_LLM_URL", raising=False)
    _namespace._warned.clear()
    _namespace.migrate_legacy_env_vars()  # should not raise
    import os

    assert "WORKSPACE_LOCAL_LLM_URL" not in os.environ


# ---------------------------------------------------------------------------
# Policy-file back-compat
# ---------------------------------------------------------------------------


def test_modern_policy_filename_is_canonical():
    """The exported canonical filename has flipped to the modern name."""
    assert POLICY_FILENAME == ".workspace-policy.json"
    assert LEGACY_POLICY_FILENAME == ".workspaceversum-policy.json"


def test_load_policy_reads_legacy_filename(tmp_path):
    """A folder with ONLY the legacy filename present should still be readable."""
    legacy_file = tmp_path / LEGACY_POLICY_FILENAME
    legacy_file.write_text(json.dumps({
        "privacy_lock_enabled": True,
        "oversight_enabled": False,
        "oversight_default_level": "approve",
    }))
    policy = load_policy(tmp_path)
    assert policy.privacy_lock_enabled is True
    assert policy.oversight_enabled is False


def test_load_policy_prefers_modern_filename(tmp_path):
    """If both filenames exist, the modern one wins."""
    (tmp_path / POLICY_FILENAME).write_text(json.dumps({
        "privacy_lock_enabled": False,
        "oversight_enabled": True,
        "oversight_default_level": "approve",
    }))
    (tmp_path / LEGACY_POLICY_FILENAME).write_text(json.dumps({
        "privacy_lock_enabled": True,
        "oversight_enabled": False,
        "oversight_default_level": "approve",
    }))
    policy = load_policy(tmp_path)
    # Modern file says lock disabled — that's what we should see.
    assert policy.privacy_lock_enabled is False
    assert policy.oversight_enabled is True


def test_save_policy_writes_modern_filename(tmp_path):
    """A save always writes the canonical filename, never the legacy one."""
    policy = FolderPolicy.default()
    save_policy(tmp_path, policy)
    assert (tmp_path / POLICY_FILENAME).exists()
    assert not (tmp_path / LEGACY_POLICY_FILENAME).exists()


def test_save_after_legacy_load_supersedes(tmp_path):
    """A legacy file exists, we load + save: the modern file appears alongside
    (legacy is NOT deleted — manual cleanup is honest, automatic is not)."""
    legacy = tmp_path / LEGACY_POLICY_FILENAME
    legacy.write_text(json.dumps({
        "privacy_lock_enabled": True,
        "oversight_enabled": True,
        "oversight_default_level": "approve",
    }))
    policy = load_policy(tmp_path)
    save_policy(tmp_path, policy)
    assert (tmp_path / POLICY_FILENAME).exists()
    assert legacy.exists()  # NOT deleted; user must clean up manually


# ---------------------------------------------------------------------------
# Folder-marker filename back-compat
# ---------------------------------------------------------------------------


def test_folder_marker_filenames_lists_both():
    """The shim's marker-filename helper exposes both names for probing."""
    names = _namespace.folder_marker_filenames()
    assert ".workspace-folder-id" in names
    assert ".workspaceversum-folder-id" in names
    # Modern name comes first so writers default to it.
    assert names[0] == ".workspace-folder-id"
