# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the per-folder mutation log primitive."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from rvnd import (
    LOG_ROOT_DEFAULT,
    LogEvent,
    MutationLog,
    folder_hash,
)
from rvnd import mutation_log as mutation_log_module


class _LockTestFile:
    def __init__(self):
        self.seeks = []

    def fileno(self):
        return 37

    def seek(self, offset):
        self.seeks.append(offset)


def test_file_lock_posix_uses_flock_modes(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        LOCK_EX=1,
        LOCK_SH=2,
        LOCK_UN=3,
        flock=lambda fd, mode: calls.append((fd, mode)),
    )
    monkeypatch.setattr(mutation_log_module, "_IS_WINDOWS", False)
    monkeypatch.setitem(sys.modules, "fcntl", fake)

    with mutation_log_module._file_lock(_LockTestFile(), exclusive=False):
        calls.append(("body", "ran"))

    assert calls == [(37, fake.LOCK_SH), ("body", "ran"), (37, fake.LOCK_UN)]


def test_file_lock_windows_uses_real_locking_region(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        LK_LOCK=10,
        LK_UNLCK=11,
        locking=lambda fd, mode, size: calls.append((fd, mode, size)),
    )
    fh = _LockTestFile()
    monkeypatch.setattr(mutation_log_module, "_IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)

    with mutation_log_module._file_lock(fh, exclusive=False):
        calls.append(("body", "ran"))

    # msvcrt has no shared mode: reads deliberately take the same exclusive
    # one-byte region as writers.
    assert calls == [
        (37, fake.LK_LOCK, 1),
        ("body", "ran"),
        (37, fake.LK_UNLCK, 1),
    ]
    assert fh.seeks == [0, 0]


def test_file_lock_windows_acquire_failure_is_fail_closed(monkeypatch):
    calls = []

    def locking(fd, mode, size):
        calls.append((fd, mode, size))
        raise OSError("lock unavailable")

    fake = SimpleNamespace(LK_LOCK=10, LK_UNLCK=11, locking=locking)
    monkeypatch.setattr(mutation_log_module, "_IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)

    entered = False
    with pytest.raises(OSError, match="lock unavailable"):
        with mutation_log_module._file_lock(_LockTestFile(), exclusive=True):
            entered = True

    assert entered is False
    assert calls == [(37, fake.LK_LOCK, 1)]


def test_file_lock_windows_missing_backend_is_fail_closed(monkeypatch):
    monkeypatch.setattr(mutation_log_module, "_IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "msvcrt", None)

    with pytest.raises(RuntimeError, match="requires msvcrt"):
        with mutation_log_module._file_lock(_LockTestFile(), exclusive=True):
            pytest.fail("body must not run without a locking backend")


# ===========================================================================
# folder_hash determinism + isolation
# ===========================================================================


def test_folder_hash_deterministic(tmp_path):
    """Same path → same hash across calls."""
    p = tmp_path / "x"
    h1 = folder_hash(p)
    h2 = folder_hash(p)
    assert h1 == h2
    assert len(h1) == 32


def test_folder_hash_distinguishes_paths(tmp_path):
    """Different paths → different hashes."""
    a = folder_hash(tmp_path / "a")
    b = folder_hash(tmp_path / "b")
    assert a != b


def test_folder_hash_handles_string_or_path(tmp_path):
    p = tmp_path / "x"
    assert folder_hash(p) == folder_hash(str(p))


def test_folder_hash_resolves_relative(tmp_path, monkeypatch):
    """Hash of '.' equals hash of the absolute current directory."""
    monkeypatch.chdir(tmp_path)
    assert folder_hash(".") == folder_hash(tmp_path)


# ===========================================================================
# LogEvent validation
# ===========================================================================


def test_log_event_requires_pair_id():
    with pytest.raises(ValueError):
        LogEvent(event="ingest", folder_path="/x", pair_id="")


def test_log_event_accepts_empty_folder_path():
    """folder_path is set by the log on append; LogEvent doesn't require it."""
    # Should NOT raise.
    e = LogEvent(event="ingest", folder_path="", pair_id="sha256:abc")
    assert e.folder_path == ""


def test_log_event_rejects_unknown_event():
    with pytest.raises(ValueError):
        LogEvent(event="not_a_real_event", folder_path="/x", pair_id="sha256:abc")


def test_log_event_rejects_unknown_channel():
    with pytest.raises(ValueError):
        LogEvent(
            event="ingest",
            folder_path="/x",
            pair_id="sha256:abc",
            channel="telegraph",
        )


def test_log_event_round_trip_via_jsonl():
    """Serialise + deserialise preserves every field."""
    e1 = LogEvent(
        event="admit",
        folder_path="/companies/acme/HR/",
        pair_id="sha256:abc123",
        lifecycle_state="admitted",
        channel="document",
        problem_id="sha256:p1",
        source_hash="sha256:s1",
        actor="agent:nd-rules",
        extra={"reason": "ok", "confidence": 0.92},
    )
    e2 = LogEvent.from_dict(json.loads(e1.to_jsonl()))
    assert e2.event == e1.event
    assert e2.folder_path == e1.folder_path
    assert e2.pair_id == e1.pair_id
    assert e2.lifecycle_state == e1.lifecycle_state
    assert e2.channel == e1.channel
    assert e2.actor == e1.actor
    assert e2.audit_id == e1.audit_id
    assert e2.ts == e1.ts
    assert e2.extra == e1.extra


def test_log_event_audit_id_unique_per_construction():
    a = LogEvent(event="ingest", folder_path="/x", pair_id="sha256:abc")
    b = LogEvent(event="ingest", folder_path="/x", pair_id="sha256:abc")
    assert a.audit_id != b.audit_id


# ===========================================================================
# MutationLog — basic append + replay
# ===========================================================================


def test_log_creates_directory(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    assert log.log_dir.exists()
    assert log.log_dir.is_dir()


def test_append_and_replay_round_trip(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    e1 = LogEvent(event="ingest", folder_path="", pair_id="sha256:p1",
                  lifecycle_state="ingested", channel="document")
    audit_id = log.append(e1)
    assert audit_id == e1.audit_id

    events = list(log.replay())
    assert len(events) == 1
    assert events[0].pair_id == "sha256:p1"
    assert events[0].lifecycle_state == "ingested"
    # folder_path overwritten by the log to the absolute folder path.
    assert events[0].folder_path == str((tmp_path / "folder").resolve())


def test_append_overwrites_folder_path(tmp_path):
    """A LogEvent created with the wrong folder_path is corrected on append."""
    log = MutationLog(tmp_path / "real", log_root=tmp_path / "logs")
    e = LogEvent(event="ingest", folder_path="/totally/wrong/path",
                 pair_id="sha256:p1")
    log.append(e)
    [stored] = list(log.replay())
    assert stored.folder_path == str((tmp_path / "real").resolve())


def test_append_raw_convenience(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="ingested")
    [e] = list(log.replay())
    assert e.pair_id == "sha256:p1"


def test_append_order_preserved(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    for i in range(5):
        log.append_raw(event="ingest", pair_id=f"sha256:p{i}", lifecycle_state="ingested")
    ids = [e.pair_id for e in log.replay()]
    assert ids == [f"sha256:p{i}" for i in range(5)]


# ===========================================================================
# MutationLog — folder isolation
# ===========================================================================


def test_two_folders_have_separate_logs(tmp_path):
    log_a = MutationLog(tmp_path / "HR", log_root=tmp_path / "logs")
    log_b = MutationLog(tmp_path / "Engineering", log_root=tmp_path / "logs")

    log_a.append_raw(event="ingest", pair_id="sha256:hr-pair", lifecycle_state="ingested")
    log_b.append_raw(event="ingest", pair_id="sha256:eng-pair", lifecycle_state="ingested")

    hr_pairs = {e.pair_id for e in log_a.replay()}
    eng_pairs = {e.pair_id for e in log_b.replay()}

    assert hr_pairs == {"sha256:hr-pair"}
    assert eng_pairs == {"sha256:eng-pair"}
    # The two logs do NOT see each other's entries — that's the load-bearing
    # property the asymmetric hierarchical rule (A2) builds on.


def test_two_logs_for_same_folder_share_state(tmp_path):
    """A second MutationLog constructed for the same path reads the same data."""
    path = tmp_path / "folder"
    log1 = MutationLog(path, log_root=tmp_path / "logs")
    log1.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="ingested")

    log2 = MutationLog(path, log_root=tmp_path / "logs")
    [e] = list(log2.replay())
    assert e.pair_id == "sha256:p1"


# ===========================================================================
# MutationLog — partial-write tolerance
# ===========================================================================


def test_malformed_lines_are_skipped(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:good1", lifecycle_state="ingested")

    # Inject a malformed line directly to the file (simulating a crash mid-write).
    with log.log_file.open("a") as fh:
        fh.write("this is not valid json\n")
        fh.write('{"event": "ingest"')  # truncated; no closing brace, no newline
        fh.write("\n")

    log.append_raw(event="ingest", pair_id="sha256:good2", lifecycle_state="ingested")

    ids = [e.pair_id for e in log.replay()]
    assert ids == ["sha256:good1", "sha256:good2"]


def test_replay_on_empty_log(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    assert list(log.replay()) == []
    assert log.count() == 0


def test_missing_required_field_in_stored_line_is_skipped(tmp_path):
    """A stored JSON line missing pair_id (e.g. older schema) is skipped, not raised."""
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:good", lifecycle_state="ingested")
    with log.log_file.open("a") as fh:
        fh.write(json.dumps({"event": "ingest", "folder_path": "/x"}) + "\n")  # no pair_id
    ids = [e.pair_id for e in log.replay()]
    assert ids == ["sha256:good"]


# ===========================================================================
# replay_filtered + latest_state + pair_ids
# ===========================================================================


def test_replay_filtered(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="ingested",
                   channel="document")
    log.append_raw(event="ingest", pair_id="sha256:p2", lifecycle_state="ingested",
                   channel="websearch")
    log.append_raw(event="admit", pair_id="sha256:p1", lifecycle_state="admitted")

    docs = list(log.replay_filtered(lambda e: e.channel == "document"))
    assert len(docs) == 1
    assert docs[0].pair_id == "sha256:p1"


def test_latest_state_tracks_transitions(tmp_path):
    """A pair moving through ingested → admitted → live → deleted resolves to the latest."""
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="ingested")
    log.append_raw(event="admit", pair_id="sha256:p1", lifecycle_state="admitted")
    log.append_raw(event="live", pair_id="sha256:p1", lifecycle_state="live")
    log.append_raw(event="delete", pair_id="sha256:p1", lifecycle_state="deleted")
    assert log.latest_state("sha256:p1") == "deleted"


def test_latest_state_unknown_pair(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    assert log.latest_state("sha256:does-not-exist") is None


def test_pair_ids_excludes_deleted_by_default(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="live")
    log.append_raw(event="ingest", pair_id="sha256:p2", lifecycle_state="live")
    log.append_raw(event="delete", pair_id="sha256:p1", lifecycle_state="deleted")

    live = log.pair_ids()
    assert live == {"sha256:p2"}


def test_pair_ids_includes_deleted_when_excludes_overridden(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="live")
    log.append_raw(event="delete", pair_id="sha256:p1", lifecycle_state="deleted")
    # Explicitly include everything.
    all_pairs = log.pair_ids(exclude_states=())
    assert all_pairs == {"sha256:p1"}


# ===========================================================================
# count + purge
# ===========================================================================


def test_count(tmp_path):
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    for i in range(3):
        log.append_raw(event="ingest", pair_id=f"sha256:p{i}", lifecycle_state="ingested")
    assert log.count() == 3


def _purge_kwargs():
    """Standard B1 purge arguments for legacy tests that don't care about
    the GDPR-grounds detail. Tests that DO care should override these."""
    return dict(
        legal_basis="art_17_1_a",
        requester_ref="test-case-001",
        reason="legacy-test-purge",
    )


def _init_controller_key(tmp_path, monkeypatch):
    """B1 prereq: purge() refuses if no controller keypair is present."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd import signing
    signing.ensure_controller_keypair()


def test_purge_removes_all_events_for_pair(tmp_path, monkeypatch):
    _init_controller_key(tmp_path, monkeypatch)
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="ingested")
    log.append_raw(event="admit", pair_id="sha256:p1", lifecycle_state="admitted")
    log.append_raw(event="ingest", pair_id="sha256:p2", lifecycle_state="ingested")

    n_purged = log.purge("sha256:p1", **_purge_kwargs())
    assert n_purged == 2

    # The tombstone is now in the log; survivors come first.
    remaining = [e.pair_id for e in log.replay() if e.event != "purge"]
    assert remaining == ["sha256:p2"]


def test_purge_unknown_pair_is_noop(tmp_path, monkeypatch):
    _init_controller_key(tmp_path, monkeypatch)
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="live")
    n = log.purge("sha256:does-not-exist", **_purge_kwargs())
    assert n == 0
    # No tombstone is written when nothing matched.
    assert len(list(log.replay())) == 1


def test_purge_preserves_other_pairs_malformed_lines(tmp_path, monkeypatch):
    """Purge rewrites the file but preserves malformed lines verbatim (forensic)."""
    _init_controller_key(tmp_path, monkeypatch)
    log = MutationLog(tmp_path / "folder", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="live")
    with log.log_file.open("a") as fh:
        fh.write("malformed-but-preserved-by-purge\n")
    log.append_raw(event="ingest", pair_id="sha256:p2", lifecycle_state="live")

    log.purge("sha256:p1", **_purge_kwargs())
    raw = log.log_file.read_text()
    assert "malformed-but-preserved-by-purge" in raw
    assert "sha256:p2" in raw
    # The raw pair id is gone entirely — the tombstone names it only
    # through the opaque folder-salted ref.
    from rvnd.forgotten_subjects import purged_pair_ref
    assert "sha256:p1" not in raw
    assert purged_pair_ref(log.folder_path, "sha256:p1") in raw


# ===========================================================================
# Default log root behaviour (separate from tmp_path)
# ===========================================================================


def test_default_log_root_is_under_home():
    """Sanity check on the documented default — used by code that doesn't pass log_root."""
    assert str(LOG_ROOT_DEFAULT).startswith(str(Path.home()))
    assert LOG_ROOT_DEFAULT.parts[-2:] == (".workspace", "log")


# ===========================================================================
# Tail cache — repeated appends skip the full-file scan, chain stays valid
# ===========================================================================


def test_many_appends_on_one_instance_chain_verifies(tmp_path):
    """Repeated appends on one instance take the cached-tail path after the
    first; every link must still resolve when re-derived from the file."""
    folder = tmp_path / "workspace"
    folder.mkdir()
    log = MutationLog(folder, log_root=tmp_path / "log_root")
    for i in range(30):
        log.append(LogEvent(event="ingest", folder_path=str(folder),
                            pair_id=f"pair-{i}"))
    result = log.verify_chain()
    assert result.ok, f"chain broke on the cached-tail path: {result}"
    assert result.total_events == 30


def test_interleaved_writers_invalidate_the_tail_cache(tmp_path):
    """A second writer grows the file between one instance's appends; the size
    check must reject the stale cache and rescan, keeping the chain valid."""
    folder = tmp_path / "workspace"
    folder.mkdir()
    a = MutationLog(folder, log_root=tmp_path / "log_root")
    b = MutationLog(folder, log_root=tmp_path / "log_root")
    for i in range(6):
        writer = a if i % 2 == 0 else b
        writer.append(LogEvent(event="ingest", folder_path=str(folder),
                               pair_id=f"pair-{i}"))
    result = a.verify_chain()
    assert result.ok, f"chain broke under interleaved writers: {result}"
    assert result.total_events == 6
