# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for at-rest memory encryption (workspaces.seal)."""

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


def test_seal_unseal_round_trip_preserves_chain_and_pairs(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    _seed(folder, log_root, n=3)

    assert MutationLog(folder, log_root=log_root).verify_chain().ok
    assert not seal.is_sealed(folder, log_root=log_root)

    out = seal.seal_folder(folder, passphrase="pw-123", log_root=log_root)
    assert out["sealed"] and out["files_sealed"] >= 1
    assert seal.is_sealed(folder, log_root=log_root)
    # plaintext dir is gone while sealed
    assert not (log_root / out["path"].split("/")[-1].removesuffix(".sealed")).exists()

    res = seal.unseal_folder(folder, passphrase="pw-123", log_root=log_root)
    assert res["unsealed"] and res["files_restored"] >= 1
    assert not seal.is_sealed(folder, log_root=log_root)

    # The audit chain still verifies and the memory is intact.
    assert MutationLog(folder, log_root=log_root).verify_chain().ok
    assert len(WorkspaceMemory(folder, log_root=log_root, actor="t").all_pairs()) == 3


def test_wrong_passphrase_restores_nothing(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    _seed(folder, log_root, n=2)
    seal.seal_folder(folder, passphrase="correct-horse", log_root=log_root)

    with pytest.raises(seal.SealError):
        seal.unseal_folder(folder, passphrase="wrong", log_root=log_root)

    # Still sealed; no plaintext leaked.
    assert seal.is_sealed(folder, log_root=log_root)

    # The right passphrase still works afterwards.
    seal.unseal_folder(folder, passphrase="correct-horse", log_root=log_root)
    assert MutationLog(folder, log_root=log_root).verify_chain().ok


def test_double_seal_is_refused(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    _seed(folder, log_root, n=1)
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    with pytest.raises(seal.SealError):
        seal.seal_folder(folder, passphrase="pw", log_root=log_root)


def test_seal_requires_passphrase_and_existing_memory(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    _seed(folder, log_root, n=1)
    with pytest.raises(seal.SealError):
        seal.seal_folder(folder, passphrase="", log_root=log_root)

    empty = tmp_path / "empty"; empty.mkdir()
    with pytest.raises(seal.SealError):
        seal.seal_folder(empty, passphrase="pw", log_root=log_root)


def test_sealed_blob_is_not_plaintext(tmp_path):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    _seed(folder, log_root, n=1)
    out = seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    blob = (log_root).rglob("*.sealed")
    raw = next(blob).read_text()
    # The summary text must not appear in the sealed file.
    assert "item 0" not in raw
    assert "workspace-seal" in raw  # it's our envelope
