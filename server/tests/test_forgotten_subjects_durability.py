# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Durability and fail-closed tests for the forgotten-subject guard."""

from __future__ import annotations

import json
import os

import pytest

from workspaces import erasure, forgotten_subjects
from workspaces.mutation_log import LogEvent, MutationLog


def test_ensure_is_idempotent_and_owner_only(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first_hash, first_added = forgotten_subjects.ensure(
        workspace, "acmecorp", "erase-req:first"
    )
    second_hash, second_added = forgotten_subjects.ensure(
        workspace, "acmecorp", "erase-req:second"
    )

    assert first_added is True
    assert second_added is False
    assert second_hash == first_hash
    ledger = forgotten_subjects._ledger_path(workspace)
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
    if os.name != "nt":
        assert ledger.stat().st_mode & 0o077 == 0


def test_ensure_recognises_historical_row_salt(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = forgotten_subjects._folder_dir(workspace)
    directory.mkdir()
    canonical_salt = "1" * 64
    historical_salt = "2" * 64
    forgotten_subjects._salt_path(workspace).write_text(
        canonical_salt + "\n", encoding="utf-8"
    )
    subject_hash = forgotten_subjects._hash_subject(
        historical_salt, "acmecorp"
    )
    forgotten_subjects._ledger_path(workspace).write_text(
        json.dumps({
            "subject_hash": subject_hash,
            "salt": historical_salt,
            "added_at": 1.0,
            "request_id": "erase-req:historical",
        }) + "\n",
        encoding="utf-8",
    )

    ensured_hash, added = forgotten_subjects.ensure(
        workspace, "acmecorp", "erase-req:retry"
    )

    assert (ensured_hash, added) == (subject_hash, False)
    assert forgotten_subjects.contains(workspace, "acmecorp") is True


def test_corrupt_ledger_fails_closed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = forgotten_subjects._folder_dir(workspace)
    directory.mkdir()
    forgotten_subjects._ledger_path(workspace).write_text(
        "not-json\n", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="corrupt forgotten-subject ledger"):
        forgotten_subjects.ensure(
            workspace, "acmecorp", "erase-req:corrupt"
        )
    with pytest.raises(RuntimeError, match="corrupt forgotten-subject ledger"):
        forgotten_subjects.check(workspace, "acmecorp")


def test_erasure_guard_failure_has_no_destructive_side_effects(
    tmp_path, monkeypatch,
):
    log_root = tmp_path / "logs"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    log = MutationLog(workspace, log_root=log_root)
    log.append(LogEvent(
        event="ingest",
        folder_path=str(workspace),
        pair_id="pair:guarded",
        lifecycle_state="live",
        channel="document",
        actor="test",
        extra={"pair": {
            "problem": {"summary": "acmecorp record"},
            "solution": {"body": "acmecorp record"},
        }},
    ))
    before = list(log.replay())

    def _fail_ensure(*args, **kwargs):
        raise OSError("read-only ledger")

    monkeypatch.setattr(forgotten_subjects, "ensure", _fail_ensure)

    with pytest.raises(
        erasure.ErasureGuardRegistrationError,
        match="erasure not started",
    ):
        erasure.execute(
            str(workspace),
            "acmecorp",
            legal_basis="art_17_1_a",
            requester_ref="requester:opaque",
            reason="retention ended",
            log_root=log_root,
        )

    after = list(log.replay())
    assert [event.audit_id for event in after] == [
        event.audit_id for event in before
    ]
    assert all(event.event != "purge" for event in after)
