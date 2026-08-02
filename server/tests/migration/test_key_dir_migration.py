# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Migration regression: per-host key namespacing (0.6.8 B4).

Pre-0.6.8 layout:

    ~/.workspace/keys/identity.priv
    ~/.workspace/keys/identity.pub

Post-0.6.8 key directory layout:

    ~/.workspace/keys/<host_id>/identity.priv
    ~/.workspace/keys/<host_id>/identity.pub

where ``host_id`` is a 12-char hex prefix of
``sha256(hostname + machine_id)``.

This test asserts the migration behaviour: place a legacy key file in a
tmp keydir, call the migration function, verify (a) the key moves into
the host-scoped subdirectory, (b) an audit event of type ``key_migration``
is written to a target folder's mutation log so the rehoming is honest.

Today, the migration function and the audit event do not exist. This test
is therefore the failing-then-passing acceptance gate for B4.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces.mutation_log import MutationLog, folder_hash


def _read_log_events(log: MutationLog) -> list[dict]:
    """Return all events in the log as raw dicts."""
    if not log.log_file.exists():
        return []
    return [
        json.loads(line)
        for line in log.log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def legacy_keydir(tmp_path, monkeypatch):
    """A tmp dir laid out in pre-0.6.8 (flat) shape, with a placeholder
    keypair file at the legacy path."""
    keydir = tmp_path / "keys"
    keydir.mkdir(parents=True, exist_ok=True)
    # We use placeholder bytes — the migration must not depend on the
    # cryptographic validity of the keypair, only on its presence + path.
    legacy_priv = keydir / "identity.priv"
    legacy_pub = keydir / "identity.pub"
    legacy_priv.write_bytes(
        b"-----BEGIN " b"PRIVATE KEY-----\n"
        b"PLACEHOLDER-PRIVATE-KEY-FOR-MIGRATION-TEST\n"
        b"-----END PRIVATE KEY-----\n"
    )
    legacy_pub.write_bytes(
        b"-----BEGIN PUBLIC KEY-----\n"
        b"PLACEHOLDER-PUBLIC-KEY-FOR-MIGRATION-TEST\n"
        b"-----END PUBLIC KEY-----\n"
    )
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    return keydir


@pytest.fixture
def target_log(tmp_path):
    """A fresh per-folder mutation log that the migration writes its audit
    event into."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    log_root = tmp_path / ".workspaces" / "log"
    return MutationLog(workspace, log_root=log_root)


# --------------------------------------------------------------------------
# Today: the migration function does not exist. Once B4 lands, drop the
# xfail marker; the assertions below are the acceptance criteria.
# --------------------------------------------------------------------------


def test_legacy_keypair_moves_into_host_subdir(legacy_keydir, target_log):
    """Calling the migration function relocates identity.{priv,pub} from
    the flat keydir into a host-scoped subdirectory."""
    from workspaces.signing import migrate_legacy_keypair_to_host_subdir  # type: ignore

    legacy_priv = legacy_keydir / "identity.priv"
    legacy_pub = legacy_keydir / "identity.pub"
    legacy_priv_bytes = legacy_priv.read_bytes()
    legacy_pub_bytes = legacy_pub.read_bytes()

    result = migrate_legacy_keypair_to_host_subdir(audit_log=target_log)

    # The migration function returns the new host_id (12-char hex) for the
    # caller to surface in `workspaces status`.
    assert isinstance(result, str)
    assert len(result) == 12
    host_id = result

    # Legacy locations are gone (moved, not copied — there is exactly one
    # canonical location for the active host's key).
    assert not legacy_priv.exists(), (
        "legacy identity.priv must be moved out of the flat keydir, not "
        "left as a duplicate"
    )
    assert not legacy_pub.exists()

    # New locations exist with the same bytes.
    new_priv = legacy_keydir / host_id / "identity.priv"
    new_pub = legacy_keydir / host_id / "identity.pub"
    assert new_priv.exists()
    assert new_pub.exists()
    assert new_priv.read_bytes() == legacy_priv_bytes
    assert new_pub.read_bytes() == legacy_pub_bytes


def test_migration_writes_key_migration_audit_event(legacy_keydir, target_log):
    """Migration must leave an audit-chain breadcrumb. The chain stays
    'honest about the change' — operators can later answer 'when did my
    key dir change shape?' from the log alone."""
    from workspaces.signing import migrate_legacy_keypair_to_host_subdir  # type: ignore

    pre_count = len(_read_log_events(target_log))
    migrate_legacy_keypair_to_host_subdir(audit_log=target_log)
    post_count = len(_read_log_events(target_log))
    assert post_count == pre_count + 1, "expected exactly one new audit event"

    last = _read_log_events(target_log)[-1]
    # Event shape required by B4.
    assert last.get("event") == "key_migration" or (
        last.get("event") == "system"
        and last.get("extra", {}).get("kind") == "key_migration"
    ), (
        "audit event must be identifiable as key_migration "
        "(either event='key_migration' or event='system' with "
        "extra.kind='key_migration')"
    )
    extra = last.get("extra", {})
    assert extra.get("from_path", "").endswith("identity.priv"), (
        "audit event must record the old key path"
    )
    assert "host_id" in extra, "audit event must record the new host_id"


def test_migration_is_idempotent(legacy_keydir, target_log):
    """Running the migration twice must not double-move, double-audit, or
    raise. Operators may re-run safely."""
    from workspaces.signing import migrate_legacy_keypair_to_host_subdir  # type: ignore

    host_id_a = migrate_legacy_keypair_to_host_subdir(audit_log=target_log)
    events_after_first = len(_read_log_events(target_log))

    host_id_b = migrate_legacy_keypair_to_host_subdir(audit_log=target_log)
    events_after_second = len(_read_log_events(target_log))

    assert host_id_a == host_id_b, "host_id must be stable across calls"
    assert events_after_second == events_after_first, (
        "second migration call must not write an audit event when there is "
        "nothing left to migrate"
    )


def test_migration_noop_on_fresh_install(tmp_path, target_log, monkeypatch):
    """If there is no legacy keypair to move, the migration function must
    return the active host_id without raising and without writing an audit
    event. Fresh installs see no historical layout to fix."""
    from workspaces.signing import migrate_legacy_keypair_to_host_subdir  # type: ignore

    fresh_keydir = tmp_path / "fresh-keys"
    fresh_keydir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(fresh_keydir))

    pre_count = len(_read_log_events(target_log))
    host_id = migrate_legacy_keypair_to_host_subdir(audit_log=target_log)
    post_count = len(_read_log_events(target_log))

    assert isinstance(host_id, str) and len(host_id) == 12
    assert post_count == pre_count, (
        "fresh install: no migration needed, no audit event"
    )
