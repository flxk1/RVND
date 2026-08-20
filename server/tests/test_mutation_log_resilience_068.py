# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B6 (0.6.8) — mutation_log resilience.

B6.1: disk-full (ENOSPC) mid-append raises DiskFullError and truncates
      the partial write so the on-disk chain stays well-formed.
B6.2: malformed-UTF-8 / malformed-JSON in the middle of the log does
      NOT block subsequent appends or replays.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from workspaces.mutation_log import (
    DiskFullError,
    LogEvent,
    MutationLog,
)


# ---------------------------------------------------------------------------
# B6.1 — disk full
# ---------------------------------------------------------------------------


def test_disk_full_during_append_raises_DiskFullError_and_truncates_log(
    tmp_path, monkeypatch,
):
    folder = tmp_path / "workspace"
    folder.mkdir()
    log_root = tmp_path / "log_root"
    log = MutationLog(folder, log_root=log_root)

    # First append succeeds, establishing a clean baseline file.
    log.append(LogEvent(event="ingest", folder_path=str(folder),
                        pair_id="pair-1"))
    baseline_size = log.log_file.stat().st_size
    assert baseline_size > 0

    # Patch the file-handle write method ONLY for the next call to raise
    # ENOSPC. We monkeypatch Path.open to wrap the returned handle.
    real_open = Path.open

    class _FailingHandle:
        def __init__(self, inner):
            self._inner = inner
            self._tripped = False

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def write(self, data):
            if not self._tripped:
                self._tripped = True
                # Partial write THEN raise — verifies truncate is needed.
                self._inner.write(data[: max(1, len(data) // 2)])
                self._inner.flush()
                raise OSError(errno.ENOSPC, "No space left on device")
            return self._inner.write(data)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

    def fake_open(self, *a, **kw):
        h = real_open(self, *a, **kw)
        # Only wrap append-mode handles on the events.jsonl
        if str(self).endswith("events.jsonl") and "a" in (a[0] if a else kw.get("mode", "")):
            return _FailingHandle(h)
        return h

    monkeypatch.setattr(Path, "open", fake_open)

    with pytest.raises(DiskFullError) as exc_info:
        log.append(LogEvent(event="ingest", folder_path=str(folder),
                            pair_id="pair-2"))
    assert exc_info.value.errno == errno.ENOSPC

    # The file size must be back to the baseline — no half line left behind.
    post_size = log.log_file.stat().st_size
    assert post_size == baseline_size, (
        f"expected log truncated back to {baseline_size} bytes, got {post_size}"
    )

    # Chain still verifies cleanly.
    monkeypatch.setattr(Path, "open", real_open)
    result = log.verify_chain()
    assert result.ok, f"chain broke after disk-full recovery: {result}"
    assert result.total_events == 1


# ---------------------------------------------------------------------------
# B6.2 — malformed-line resilience
# ---------------------------------------------------------------------------


def test_malformed_unicode_line_does_not_block_subsequent_append(tmp_path):
    folder = tmp_path / "workspace"
    folder.mkdir()
    log_root = tmp_path / "log_root"
    log = MutationLog(folder, log_root=log_root)

    log.append(LogEvent(event="ingest", folder_path=str(folder), pair_id="pair-1"))
    log.append(LogEvent(event="ingest", folder_path=str(folder), pair_id="pair-2"))

    # Inject a raw malformed-UTF-8 line in the middle of the chain.
    with open(log.log_file, "ab") as fh:
        fh.write(b"\xff\xfe not utf-8 \x80\x81\n")

    # Append must succeed.
    new_id = log.append(LogEvent(event="ingest", folder_path=str(folder),
                                 pair_id="pair-3"))
    assert new_id

    result = log.verify_chain()
    assert result.malformed_lines >= 1
    # Three real events + 1 malformed. broken_links may include the
    # malformed line; that's expected.
    assert result.total_events == 3

    # Replay must skip the bad line, yielding the three real events.
    pair_ids = [e.pair_id for e in log.replay()]
    assert pair_ids == ["pair-1", "pair-2", "pair-3"]


def test_malformed_json_line_counted_as_malformed_not_broken_link(tmp_path):
    folder = tmp_path / "workspace"
    folder.mkdir()
    log_root = tmp_path / "log_root"
    log = MutationLog(folder, log_root=log_root)
    log.append(LogEvent(event="ingest", folder_path=str(folder), pair_id="p1"))

    # Inject a malformed JSON line (valid UTF-8, invalid JSON).
    with open(log.log_file, "a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")

    log.append(LogEvent(event="ingest", folder_path=str(folder), pair_id="p2"))

    result = log.verify_chain()
    assert result.malformed_lines >= 1
    # No tamper false-positive: the two real events still link to each
    # other through the canonical-hash chain.
    assert result.total_events == 2
