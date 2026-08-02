# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Chaos C3: racing first-use keypair creation.

Two processes share a fresh WORKSPACE_KEY_DIR and each appends signed
events, so both race ``signing.ensure_keypair()`` on first use.

Pre-fix behaviour: ``ensure_keypair`` was check-then-write. Both racers
passed the exists() check, both generated a keypair, the second write won
the file — and each process RETURNED ITS OWN in-memory key. The loser then
signed events with a private key that never persisted, so ``verify_chain``
(which verifies against the on-disk public key) reported signature
failures on a chain nobody tampered with: a silent identity fork.

Post-fix: the canonical key path is claimed atomically (hard-link
first-writer-wins); the loser discards its candidate and loads the
winner's key. Invariants pinned here:

  * both writers exit 0;
  * exactly one keypair on disk (no orphaned temp candidates);
  * verify_chain reports ZERO signature failures — every event from both
    processes verifies against the single on-disk identity.
"""
from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

import pytest

# Real OS processes with generous joins, same rationale as C1.
pytestmark = [pytest.mark.slow, pytest.mark.security]


def _signing_writer(workspace_str: str, log_root_str: str, key_dir: str,
                    n_events: int, label: str) -> None:
    os.environ["WORKSPACE_KEY_DIR"] = key_dir
    from workspaces.mutation_log import LogEvent, MutationLog

    workspace = Path(workspace_str)
    log = MutationLog(workspace, log_root=Path(log_root_str))
    for i in range(n_events):
        log.append(LogEvent(
            event="ingest",
            folder_path=str(workspace),
            pair_id=f"pair:{label}:{i:04d}",
            channel="document",
            actor=f"chaos:keyrace:{label}",
            extra={"i": i, "label": label, "pid": os.getpid()},
        ))


def test_racing_first_use_keypair_single_identity(tmp_path: Path,
                                                  monkeypatch) -> None:
    workspace = tmp_path / "keyrace_workspace"
    workspace.mkdir(parents=True)
    log_root = tmp_path / ".workspaces"
    log_root.mkdir(parents=True)
    key_dir = tmp_path / "keys"  # fresh — neither process has a key yet
    n_each = 50

    ctx = mp.get_context("spawn")
    procs = [
        ctx.Process(target=_signing_writer,
                    args=(str(workspace), str(log_root), str(key_dir),
                          n_each, label))
        for label in ("A", "B")
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    for p in procs:
        assert p.exitcode == 0, f"writer failed (exit {p.exitcode})"

    # Exactly one identity, no leftover claim candidates.
    priv_files = sorted(str(p.relative_to(key_dir))
                        for p in key_dir.rglob("*.priv") if p.is_file())
    tmp_files = [p for p in key_dir.rglob(".*.tmp.*") if p.is_file()]
    assert len(priv_files) == 1, (
        f"expected exactly one private key after the race, got {priv_files}")
    assert not tmp_files, f"orphaned claim candidates left behind: {tmp_files}"

    # Every event from BOTH processes must verify against that identity.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(key_dir))
    from workspaces.mutation_log import MutationLog
    result = MutationLog(workspace, log_root=log_root).verify_chain()
    assert result.total_events == 2 * n_each
    assert len(result.signature_failures) == 0, (
        "identity fork: events signed with a key that never reached disk — "
        f"{result.signature_failures[:3]}")
    assert result.ok, f"chain not ok: broken={result.broken_links[:3]}"
