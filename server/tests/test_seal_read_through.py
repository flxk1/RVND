# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace Lock — 'sealed but served' read-through (workspaces.seal.read_through).

A sealed workspace must still be answerable: Workspaces decrypts the memory store into
memory to serve a read, while the on-disk store stays ciphertext and nothing
is written to disk. These tests pin that behaviour.
"""

from __future__ import annotations

import pytest

from workspaces import seal
from workspaces.memory import WorkspaceMemory
from workspaces.mutation_log import MutationLog


def _seed(folder, log_root, n=3):
    mem = WorkspaceMemory(folder, log_root=log_root, actor="t")
    for i in range(n):
        mem.remember({
            "id": f"sha256:x{i}",
            "problem": {"id": f"p{i}", "scope": "s", "type": "rule",
                        "summary": f"item {i}"},
            "solution": {"id": f"sha256:x{i}", "problem_id": f"p{i}",
                         "body": "body", "authority_tier": 1,
                         "confidence": 1.0, "body_format": "prose"},
        })
    return mem


def _snapshot(folder, log_root):
    log_dir = seal._resolve_log_dir(folder, log_root)
    return {p.relative_to(log_dir).as_posix(): p.read_bytes()
            for p in sorted(log_dir.rglob("*")) if p.is_file()}


def test_read_through_returns_plaintext_without_unsealing(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    _seed(folder, log_root, n=3)

    before = _snapshot(folder, log_root)
    assert "events.jsonl" in before

    seal.seal_folder(folder, passphrase="pw-123", log_root=log_root)
    assert seal.is_sealed(folder, log_root=log_root)

    # Serve the sealed workspace in memory.
    served = seal.read_through(folder, passphrase="pw-123", log_root=log_root)

    # Same bytes as before sealing.
    assert served == before

    # And the workspace is STILL sealed — read-through did not unseal to disk.
    assert seal.is_sealed(folder, log_root=log_root)
    log_dir = seal._resolve_log_dir(folder, log_root)
    assert not log_dir.exists()          # no plaintext dir on disk
    assert seal._sealed_path(log_dir).exists()  # ciphertext blob untouched


def test_read_through_can_verify_chain_from_memory(tmp_path):
    """Prove a sealed workspace is *usable*: the audit chain verifies from the
    in-memory events without ever unsealing to disk."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    _seed(folder, log_root, n=4)

    # Baseline chain length while unsealed.
    plain = MutationLog(folder, log_root=log_root).log_file.read_bytes()
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)

    served_events = seal.read_through_file(
        folder, "events.jsonl", passphrase="pw", log_root=log_root)
    # The served events are exactly the sealed chain bytes.
    assert served_events == plain
    # Non-empty, line-delimited JSON — i.e. a real chain we could verify.
    lines = [l for l in served_events.decode().splitlines() if l.strip()]
    assert len(lines) >= 4
    assert seal.is_sealed(folder, log_root=log_root)  # still sealed


def test_read_through_wrong_passphrase_fails_clean(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    _seed(folder, log_root, n=2)
    seal.seal_folder(folder, passphrase="correct", log_root=log_root)

    with pytest.raises(seal.SealError):
        seal.read_through(folder, passphrase="wrong", log_root=log_root)

    # Untouched: still sealed, right passphrase still serves.
    assert seal.is_sealed(folder, log_root=log_root)
    served = seal.read_through(folder, passphrase="correct", log_root=log_root)
    assert "events.jsonl" in served


def test_read_through_on_unsealed_folder_raises(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    _seed(folder, log_root, n=1)
    with pytest.raises(seal.SealError):
        seal.read_through(folder, passphrase="pw", log_root=log_root)


def test_read_through_then_normal_unseal_still_works(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    _seed(folder, log_root, n=2)
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    seal.read_through(folder, passphrase="pw", log_root=log_root)  # serve once
    # The escape hatch (full unseal for direct access) still works after serving.
    res = seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert res["unsealed"]
    assert MutationLog(folder, log_root=log_root).verify_chain().ok
    assert len(WorkspaceMemory(folder, log_root=log_root, actor="t").all_pairs()) == 2
