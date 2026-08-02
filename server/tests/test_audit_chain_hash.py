# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the hash-chain tamper-evidence on MutationLog (post-0.6.5).

Each well-formed event carries ``prev_hash`` linking it cryptographically to
its predecessor. ``verify_chain()`` walks the log and surfaces broken links.
Deletion, modification, reordering — all visible.

Backward compat: legacy events (pre-0.6.5, no prev_hash field) are accepted
and counted but not validated.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _default_chain_profile(monkeypatch):
    """This module tests DEFAULT chain semantics (pinning off, legacy
    tolerance, advisory divergence). Clear the opt-in protections in case the
    hardened profile (RVND_TEST_HARDENED=1) enabled them suite-wide; tests
    that want a protection ON set it explicitly in their own body, which runs
    after this fixture and wins."""
    for var in ("WORKSPACE_KEY_PINNING", "WORKSPACE_STRICT_KEY_PINNING",
                "WORKSPACE_STRICT_HOST_DIVERGENCE"):
        monkeypatch.delenv(var, raising=False)

from workspaces.mutation_log import (
    GENESIS_HASH,
    LogEvent,
    MutationLog,
    _canonical_event_hash,
)


def _make_event(folder: Path, i: int) -> LogEvent:
    return LogEvent(
        event="ingest",
        folder_path=str(folder),
        pair_id=f"pair-{i}",
        actor="test",
        extra={"i": i},
    )


def test_fresh_log_chain_starts_at_genesis(tmp_path: Path) -> None:
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    log.append(_make_event(tmp_path / "work", 0))
    events = list(log.replay())
    assert len(events) == 1
    assert events[0].prev_hash == GENESIS_HASH


def test_chain_links_form_correctly(tmp_path: Path) -> None:
    """Each event's prev_hash equals the canonical hash of its predecessor."""
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    for i in range(5):
        log.append(_make_event(tmp_path / "work", i))
    log_file = tmp_path / ".workspaces" / log.folder_id / "events.jsonl"
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    assert lines[0]["prev_hash"] == GENESIS_HASH
    for i in range(1, 5):
        expected = _canonical_event_hash(lines[i - 1])
        assert lines[i]["prev_hash"] == expected, f"event {i} chain broken"


def test_verify_chain_ok_on_clean_log(tmp_path: Path) -> None:
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    for i in range(10):
        log.append(_make_event(tmp_path / "work", i))
    result = log.verify_chain()
    assert result.ok
    assert bool(result) is True
    assert result.total_events == 10
    assert result.legacy_events == 0
    assert result.broken_links == []
    assert result.malformed_lines == 0


def test_verify_chain_detects_silent_deletion(tmp_path: Path) -> None:
    """The HIGH-severity gap that motivated this patch.

    Attacker removes a middle event. Pre-0.6.5: replay returns 4 events with no
    warning. Post-0.6.5: verify_chain reports a broken link at the position
    where the deleted event used to be.
    """
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    for i in range(5):
        log.append(_make_event(tmp_path / "work", i))
    log_file = tmp_path / ".workspaces" / log.folder_id / "events.jsonl"

    # Attacker tampers — removes the middle event.
    lines = log_file.read_text().splitlines()
    assert len(lines) == 5
    del lines[2]
    log_file.write_text("\n".join(lines) + "\n")

    # Replay still works (we never crash readers on tamper) — but verify_chain detects.
    assert sum(1 for _ in log.replay()) == 4
    result = log.verify_chain()
    assert not result.ok
    assert result.total_events == 4
    assert len(result.broken_links) == 1
    break_info = result.broken_links[0]
    assert break_info["reason"] == "prev_hash_mismatch"


def test_verify_chain_detects_modification(tmp_path: Path) -> None:
    """Attacker modifies the actor field on a historical event."""
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    for i in range(4):
        log.append(_make_event(tmp_path / "work", i))
    log_file = tmp_path / ".workspaces" / log.folder_id / "events.jsonl"

    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    # Tamper: change actor on event 1 from "test" to "innocent_bystander"
    lines[1]["actor"] = "innocent_bystander"
    log_file.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    result = log.verify_chain()
    assert not result.ok
    # Event 2's prev_hash points to original event 1; now event 1 has different
    # content, so event 2's link breaks.
    assert len(result.broken_links) >= 1


def test_verify_chain_detects_reorder(tmp_path: Path) -> None:
    """Attacker swaps two events. Both links around the swap break."""
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    for i in range(5):
        log.append(_make_event(tmp_path / "work", i))
    log_file = tmp_path / ".workspaces" / log.folder_id / "events.jsonl"

    lines = log_file.read_text().splitlines()
    lines[2], lines[3] = lines[3], lines[2]
    log_file.write_text("\n".join(lines) + "\n")

    result = log.verify_chain()
    assert not result.ok
    assert len(result.broken_links) >= 1


def test_legacy_events_accepted_without_prev_hash(tmp_path: Path) -> None:
    """Pre-0.6.5 events have no prev_hash. They must still replay and verify
    must report them as legacy without breaking the chain check on later events.
    """
    log_dir = tmp_path / ".workspaces"
    log = MutationLog(tmp_path / "work", log_root=log_dir)
    log_file = log_dir / log.folder_id / "events.jsonl"

    # Hand-write two "legacy" events (no prev_hash field) — simulating
    # a 0.6.4-era log.
    legacy_a = {
        "event": "ingest",
        "folder_path": str(log.folder_path),
        "pair_id": "legacy-a",
        "actor": "test",
        "audit_id": "00000000-0000-0000-0000-000000000001",
        "ts": 1700000000.0,
        "extra": {},
        "lifecycle_state": "",
        "channel": "system",
        "problem_id": "",
        "source_hash": "",
    }
    legacy_b = dict(legacy_a, pair_id="legacy-b",
                    audit_id="00000000-0000-0000-0000-000000000002")
    log_file.write_text(
        json.dumps(legacy_a) + "\n" + json.dumps(legacy_b) + "\n"
    )

    # Now append a new (post-0.6.5) event. It will see the previous as legacy
    # but compute prev_hash off legacy_b's canonical hash.
    log.append(_make_event(tmp_path / "work", 99))

    result = log.verify_chain()
    assert result.ok, f"chain should be OK, got: {result.broken_links}"
    assert result.total_events == 3
    assert result.legacy_events == 2  # the two hand-written events
    assert result.broken_links == []


def test_malformed_line_reported_in_chain(tmp_path: Path) -> None:
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    log.append(_make_event(tmp_path / "work", 0))
    log.append(_make_event(tmp_path / "work", 1))
    log_file = tmp_path / ".workspaces" / log.folder_id / "events.jsonl"
    # Inject a garbage line in the middle
    content = log_file.read_text()
    parts = content.splitlines()
    corrupted = parts[0] + "\n{not valid json\n" + parts[1] + "\n"
    log_file.write_text(corrupted)

    result = log.verify_chain()
    assert result.malformed_lines == 1
    # The malformed line is reported as a broken link.
    assert any(b["reason"] == "malformed_json" for b in result.broken_links)


def test_canonical_hash_excludes_prev_hash(tmp_path: Path) -> None:
    """If canonical_event_hash included prev_hash, the chain would be circular
    and every event's hash would depend on every other event's content. The
    chain must not depend on the chain.
    """
    base = {
        "event": "ingest",
        "folder_path": "/test",
        "pair_id": "p1",
        "actor": "u",
        "audit_id": "a",
        "ts": 1.0,
        "extra": {},
        "lifecycle_state": "",
        "channel": "system",
        "problem_id": "",
        "source_hash": "",
    }
    h1 = _canonical_event_hash({**base, "prev_hash": "GENESIS"})
    h2 = _canonical_event_hash({**base, "prev_hash": "abcdef"})
    h3 = _canonical_event_hash(base)
    assert h1 == h2 == h3


def test_canonical_hash_deterministic_across_key_order(tmp_path: Path) -> None:
    a = {"event": "ingest", "pair_id": "p", "actor": "u",
         "folder_path": "/x", "audit_id": "1", "ts": 1.0,
         "extra": {"i": 1}, "lifecycle_state": "", "channel": "system",
         "problem_id": "", "source_hash": ""}
    b = {"ts": 1.0, "extra": {"i": 1}, "audit_id": "1",
         "folder_path": "/x", "actor": "u", "pair_id": "p",
         "event": "ingest", "lifecycle_state": "", "channel": "system",
         "problem_id": "", "source_hash": ""}
    assert _canonical_event_hash(a) == _canonical_event_hash(b)


def test_concurrent_appenders_still_form_valid_chain(tmp_path: Path) -> None:
    """10 threads × 20 writes. After the dust settles, the chain validates.

    NB: this test is best-effort — POSIX append atomicity holds for sub-PIPE_BUF
    writes, which our JSONL lines easily fit. On a hostile FS this could in
    theory race, but on Linux ext4 / macOS APFS it's safe.
    """
    import threading
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")

    def writer(tid: int) -> None:
        for i in range(20):
            log.append(LogEvent(
                event="ingest", folder_path=str(tmp_path / "work"),
                pair_id=f"t{tid}-{i}", actor=f"t{tid}", extra={"i": i},
            ))

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()

    result = log.verify_chain()
    assert result.total_events == 200
    # The chain MUST hold under concurrent writes — each appender reads the
    # current last line atomically before its own write.
    assert result.ok, (
        f"chain broke under concurrent writes: "
        f"{len(result.broken_links)} broken links / {result.total_events} events"
    )
