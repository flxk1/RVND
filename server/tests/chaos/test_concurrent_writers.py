# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Chaos C1: concurrent writers without explicit cross-process lock.

This test makes the concurrent-writer requirement concrete.

Scenario:
  Two processes each append ~200 events to the SAME mutation log over the
  SAME ``log_root`` in a tight loop. We then run ``verify_chain()`` and
  assert that:

    * total_events == 2 × N (no events lost)
    * broken_links is empty (chain still validates)
    * signature_failures is empty (no signatures lost)
    * every appended event has a distinct ``prev_hash`` per chain position
      (no two events claim the same predecessor)

Status (0.6.8 B1): FIXED and expected to PASS on POSIX. ``append()`` holds
``fcntl.flock(LOCK_EX)`` across the read-last-then-append region and the
tail cache is size-guarded, so two processes with separate open file
descriptions cooperate (``flock`` is per open file description, and both
descriptions lock the same inode). This test is the permanent regression:
if the lock regresses, the chain forks and ``verify_chain`` reports
broken_links. On platforms with neither ``fcntl`` nor ``msvcrt`` the lock
is a documented no-op and this test is expected to fail there (TD7:
Windows is Tier 2, best-effort).

History: this file originally documented the pre-fix behaviour with an
``xfail(strict=True)`` plan — that wording outlived the fix and read as if
the defect were still open. It is not; the marker was never needed once
B1 landed.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import tempfile
import time
from pathlib import Path

import pytest

# Spawns real OS processes and joins each with a 120s budget by design (see
# module docstring): platform-timing-dependent and not boundable by a
# per-test signal timeout, so it stays out of the fast subset.
pytestmark = pytest.mark.slow


def _writer_process(workspace_str: str, log_root_str: str,
                    n_events: int, label: str) -> None:
    """Append ``n_events`` events to the shared mutation log.

    Runs in a child process. Must import inside the function so the import
    happens after the fork (each worker gets its own module state).
    """
    # Late imports — avoid the cost in the parent.
    from workspaces.mutation_log import LogEvent, MutationLog

    workspace = Path(workspace_str)
    log_root = Path(log_root_str)
    log = MutationLog(workspace, log_root=log_root)

    for i in range(n_events):
        log.append(LogEvent(
            event="ingest",
            folder_path=str(workspace),
            pair_id=f"pair:{label}:{i:04d}",
            channel="document",
            actor=f"chaos:writer:{label}",
            extra={"i": i, "label": label, "pid": os.getpid()},
        ))


def test_two_processes_no_chain_corruption(tmp_path: Path) -> None:
    workspace = tmp_path / "concurrent_workspace"
    workspace.mkdir(parents=True)
    log_root = tmp_path / ".workspaces"
    log_root.mkdir(parents=True)
    n_each = 200

    # Spawn two writer processes; bind to the SAME workspace + log_root.
    ctx = mp.get_context("spawn")  # avoid copying state non-deterministically
    p_a = ctx.Process(target=_writer_process,
                      args=(str(workspace), str(log_root), n_each, "A"))
    p_b = ctx.Process(target=_writer_process,
                      args=(str(workspace), str(log_root), n_each, "B"))
    t0 = time.time()
    p_a.start()
    p_b.start()
    p_a.join(timeout=120)
    p_b.join(timeout=120)
    elapsed = time.time() - t0
    assert p_a.exitcode == 0, f"writer A failed (exit {p_a.exitcode})"
    assert p_b.exitcode == 0, f"writer B failed (exit {p_b.exitcode})"

    # Now verify the chain.
    from workspaces.mutation_log import MutationLog
    log = MutationLog(workspace, log_root=log_root)
    result = log.verify_chain()

    # Diagnostics: dump count of unique prev_hash values + duplicates.
    log_file = log_root / log.folder_id / "events.jsonl"
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    prev_hashes = [l.get("prev_hash") for l in lines]
    seen_prev: dict[str, int] = {}
    for ph in prev_hashes:
        seen_prev[ph] = seen_prev.get(ph, 0) + 1
    duplicate_predecessors = {ph: c for ph, c in seen_prev.items() if c > 1}

    diag = (
        f"\nconcurrent-writer diagnostics:\n"
        f"  elapsed       = {elapsed:.2f}s\n"
        f"  events on disk = {len(lines)}\n"
        f"  expected       = {2 * n_each}\n"
        f"  broken_links   = {len(result.broken_links)}\n"
        f"  sig_failures   = {len(result.signature_failures)}\n"
        f"  malformed      = {result.malformed_lines}\n"
        f"  duplicate prev_hash claims = {len(duplicate_predecessors)} "
        f"(forks in the chain)\n"
    )
    if result.broken_links[:3]:
        diag += f"  first broken: {result.broken_links[:3]}\n"

    assert len(lines) == 2 * n_each, (
        f"events lost — expected {2 * n_each}, got {len(lines)}{diag}"
    )
    assert result.malformed_lines == 0, diag
    assert len(duplicate_predecessors) == 0, (
        "chain forked: two events share the same prev_hash" + diag
    )
    assert len(result.broken_links) == 0, diag
    assert len(result.signature_failures) == 0, diag
    assert result.ok, diag


def test_single_process_baseline(tmp_path: Path) -> None:
    """Sanity check: same load from a single process MUST pass.

    If this fails, the test framework is broken — not the chain. Keep it
    here so a failing concurrent test points clearly at the concurrency
    layer, not the underlying append path.
    """
    workspace = tmp_path / "baseline_workspace"
    workspace.mkdir(parents=True)
    log_root = tmp_path / ".workspaces"
    log_root.mkdir(parents=True)
    _writer_process(str(workspace), str(log_root), 200, "single")

    from workspaces.mutation_log import MutationLog
    log = MutationLog(workspace, log_root=log_root)
    result = log.verify_chain()
    assert result.ok, (
        f"single-process baseline failed — broken={result.broken_links[:3]}, "
        f"sig={result.signature_failures[:3]}"
    )
    assert result.total_events == 200
