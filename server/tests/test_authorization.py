# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""M5/A6 — opt-in, fail-closed access control on governed reads.

Default (no access policy) is permissive: local-first, the operator owns the folder.
When a folder opts in (policy.access_control_enabled), a read by a NAMED party is
gated against the party register — unknown/suspended actors are denied, runtime
principals and active registered parties pass. Enterprise-readiness panel."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.security  # red-team-relevant: runs in the `-m security` gate

from workspaces.authorization import check_access
from workspaces.parties import register_party, set_party_status
from workspaces.policy import FolderPolicy, save_policy


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    f = tmp_path / "org"; f.mkdir()
    return str(f), str(tmp_path / "logs")


def _enable(fpath):
    save_policy(fpath, FolderPolicy(access_control_enabled=True))


# ── default is permissive (local-first) ──────────────────────────────────────

def test_access_open_when_not_enabled(folder):
    f, lr = folder
    assert check_access(f, "anyone-at-all", "read", log_root=lr) is True


def test_policy_roundtrips_the_flag(tmp_path):
    p = FolderPolicy(access_control_enabled=True)
    assert p.to_dict()["access_control_enabled"] is True
    assert FolderPolicy.from_dict(p.to_dict()).access_control_enabled is True
    assert "access_control_enabled" not in FolderPolicy().to_dict()  # legacy stays clean


# ── opt-in: fail-closed on unknown / suspended; runtime + active parties pass ──

def test_enabled_denies_unknown_actor(folder):
    f, lr = folder; _enable(f)
    assert check_access(f, "mallory", "read", log_root=lr) is False


def test_enabled_denies_anonymous_actor(folder):
    f, lr = folder; _enable(f)
    assert check_access(f, "", "read", log_root=lr) is False


def test_enabled_allows_runtime_principal(folder):
    f, lr = folder; _enable(f)
    # the runtime's own default actor + builtins are not external callers
    assert check_access(f, "mcp:l0", "read", log_root=lr) is True
    assert check_access(f, "system", "read", log_root=lr) is True


def test_enabled_allows_active_registered_party(folder):
    f, lr = folder; _enable(f)
    register_party(f, "alice", "human", actor="admin", log_root=lr)
    assert check_access(f, "alice", "read", log_root=lr) is True


def test_enabled_denies_suspended_party(folder):
    f, lr = folder; _enable(f)
    register_party(f, "bob", "agent", actor="admin", log_root=lr)
    set_party_status(f, "bob", "suspended", log_root=lr)
    assert check_access(f, "bob", "read", log_root=lr) is False


def test_competence_required_and_checked(folder):
    f, lr = folder; _enable(f)
    register_party(f, "dpo", "human", competences=["data-protection"],
                   actor="admin", log_root=lr)
    assert check_access(f, "dpo", "read", competence="data-protection", log_root=lr) is True
    assert check_access(f, "dpo", "read", competence="legal", log_root=lr) is False


def test_unknown_action_denied(folder):
    f, lr = folder; _enable(f)
    assert check_access(f, "mcp:l0", "exfiltrate", log_root=lr) is False


# ── the read facade ops are actually gated ────────────────────────────────────

def test_recent_read_op_is_gated(folder, monkeypatch):
    f, _ = folder; _enable(f)
    from workspaces import mcp_impl as M
    # a named external actor with no registration is denied the read...
    denied = M.recent(f, actor="mallory")
    assert denied.get("error") == "access denied"
    # ...while the runtime's own default actor passes (local-first default path).
    ok = M.recent(f)
    assert "error" not in ok


def test_reads_open_when_not_enabled(folder):
    f, _ = folder  # no _enable → access control OFF
    from workspaces import mcp_impl as M
    assert "error" not in M.recent(f, actor="mallory")        # permissive default
