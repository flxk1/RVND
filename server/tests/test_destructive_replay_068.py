# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-10: replay-safety of the destructive ops.

MCP delivery is at-least-once, so a client (or a retrying transport) can call
a destructive op twice with identical params. The op must produce ONE effect
and leave a single, non-forked audit record — never a second composite
tombstone or a duplicate ledger row.

Covered here: erasure ``execute`` replay + genuine re-erasure, and the purge
primitive's documented (but previously untested) idempotent return.
Workspace-migrate mid-crash lives in test_workspace_migrate_crash_068.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces import erasure, forgotten_subjects
from workspaces.mutation_log import LogEvent, MutationLog

pytestmark = pytest.mark.security  # destructive-op integrity


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    log_root = tmp_path / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from workspaces import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    return {"log_root": log_root, "workspace": workspace}


def _seed_pair(workspace, log_root, *, pair_id, summary):
    log = MutationLog(workspace, log_root=log_root)
    pair = {
        "id": pair_id,
        "problem": {"id": "sha256:p-" + pair_id[-8:], "summary": summary,
                    "type": "case", "facets": {}},
        "solution": {"id": pair_id, "problem_id": "sha256:p-" + pair_id[-8:],
                     "body": summary, "body_format": "prose",
                     "authority_tier": 5, "confidence": 0.5,
                     "cited_sources": [], "extractor_chain": ["test:seed"]},
    }
    log.append(LogEvent(
        event="ingest", folder_path=str(workspace), pair_id=pair_id,
        lifecycle_state="live", channel="document", actor="test",
        extra={"pair": pair, "distribution_scope": "private"},
    ))
    return pair_id


def _count_composites(workspace, log_root) -> int:
    log = MutationLog(workspace, log_root=log_root)
    events_file = Path(log_root) / log.folder_id / "events.jsonl"
    n = 0
    for line in events_file.read_text().splitlines():
        if not line.strip():
            continue
        if json.loads(line).get("extra", {}).get("kind") == "erasure_composite":
            n += 1
    return n


def _raw_ledger_rows(workspace) -> int:
    """Raw ledger LINE count — the bug wrote a second row that list_subjects
    hides by deduping on read, so we must read the file directly."""
    lp = forgotten_subjects._ledger_path(workspace)
    if not lp.exists():
        return 0
    return len([l for l in lp.read_text().splitlines() if l.strip()])


def _execute(env, subject):
    return erasure.execute(
        str(env["workspace"]), subject,
        legal_basis="art_17_1_a", requester_ref="dsar-42",
        reason="subject exercised Art. 17", log_root=env["log_root"],
    )


# ---------------------------------------------------------------------------
# Erasure execute — replay
# ---------------------------------------------------------------------------


def test_erasure_execute_replay_is_a_noop(isolated_env):
    """A second execute of the same subject, with nothing new to purge, must
    write NO second composite, NO duplicate ledger row, and flag replay."""
    env = isolated_env
    _seed_pair(env["workspace"], env["log_root"],
               pair_id="sha256:pair-erik", summary="Erik Muller lost the appeal.")

    first = _execute(env, "Erik Muller")
    assert first.replayed_noop is False
    assert first.composite_tombstone_id, "first execute must write a composite"
    assert _count_composites(env["workspace"], env["log_root"]) == 1
    assert _raw_ledger_rows(env["workspace"]) == 1

    # Replay — identical params, nothing left to purge.
    second = _execute(env, "Erik Muller")
    assert second.replayed_noop is True, "replay must be recognised as a no-op"
    assert second.composite_tombstone_id == "", "replay must NOT write a composite"
    assert _count_composites(env["workspace"], env["log_root"]) == 1, (
        "replay forked the audit record with a second composite tombstone")
    assert _raw_ledger_rows(env["workspace"]) == 1, (
        "replay wrote a duplicate forgotten-subjects ledger row")

    # And the chain still verifies.
    result = MutationLog(env["workspace"], log_root=env["log_root"]).verify_chain()
    assert result.ok, f"chain broke after replay: {result.broken_links[:3]}"


def test_erasure_reexecute_with_new_data_writes_composite_no_dup_ledger(isolated_env):
    """A GENUINE re-erasure — new data about an already-forgotten subject
    slipped in — must purge it and document that with a fresh composite, but
    must NOT add a duplicate ledger row (the subject is already forgotten)."""
    env = isolated_env
    _seed_pair(env["workspace"], env["log_root"],
               pair_id="sha256:pair-a", summary="Nadia Farah signed here.")
    first = _execute(env, "Nadia Farah")
    assert first.replayed_noop is False
    assert _count_composites(env["workspace"], env["log_root"]) == 1
    assert _raw_ledger_rows(env["workspace"]) == 1

    # New data about the same subject arrives and is erased again.
    _seed_pair(env["workspace"], env["log_root"],
               pair_id="sha256:pair-b", summary="Nadia Farah appears again.")
    second = _execute(env, "Nadia Farah")
    assert second.replayed_noop is False, "a purge with real effect is not a replay"
    assert second.composite_tombstone_id, "the new purge must be documented"
    assert _count_composites(env["workspace"], env["log_root"]) == 2, (
        "a re-erasure with new effect should record a second composite")
    assert _raw_ledger_rows(env["workspace"]) == 1, (
        "the subject is already forgotten — no duplicate ledger row")


def test_erasure_first_execute_zero_hits_still_records_then_replays(isolated_env):
    """First erasure of a never-present subject still records (pre-emptive
    forget); a second identical call is then a recognised replay."""
    env = isolated_env
    first = _execute(env, "Ghost Subject")
    assert first.replayed_noop is False
    assert _raw_ledger_rows(env["workspace"]) == 1
    second = _execute(env, "Ghost Subject")
    assert second.replayed_noop is True
    assert _raw_ledger_rows(env["workspace"]) == 1


# ---------------------------------------------------------------------------
# Purge primitive — documented idempotent return, now asserted
# ---------------------------------------------------------------------------


def test_purge_same_pair_twice_is_idempotent(isolated_env):
    """purge() of a pair_id already purged matches 0 events, returns 0, and
    writes no second tombstone — documented at the call site, never tested."""
    env = isolated_env
    _seed_pair(env["workspace"], env["log_root"],
               pair_id="sha256:pair-x", summary="one-shot pair")
    log = MutationLog(env["workspace"], log_root=env["log_root"])

    first = log.purge("sha256:pair-x", legal_basis="art_17_1_a",
                      requester_ref="r", reason="art 17")
    assert first > 0, "first purge should remove the seeded event(s)"

    events_file = Path(env["log_root"]) / log.folder_id / "events.jsonl"
    tombstones_after_first = sum(
        1 for line in events_file.read_text().splitlines()
        if line.strip() and json.loads(line).get("event") == "purge")

    second = MutationLog(env["workspace"], log_root=env["log_root"]).purge(
        "sha256:pair-x", legal_basis="art_17_1_a", requester_ref="r", reason="art 17")
    assert second == 0, "replayed purge must match 0 events and return 0"

    tombstones_after_second = sum(
        1 for line in events_file.read_text().splitlines()
        if line.strip() and json.loads(line).get("event") == "purge")
    assert tombstones_after_second == tombstones_after_first, (
        "replayed purge wrote a second tombstone for an already-purged pair")

    result = MutationLog(env["workspace"], log_root=env["log_root"]).verify_chain()
    assert result.ok, f"chain broke after double purge: {result.broken_links[:3]}"
