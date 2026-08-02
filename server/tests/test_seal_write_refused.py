# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Write-while-sealed = refuse; and constructing a log on a sealed workspace must
not recreate the plaintext dir (which would brick unseal)."""

from __future__ import annotations

import pytest

from workspaces import seal
from workspaces.memory import WorkspaceMemory
from workspaces.mutation_log import MutationLog, SealedWriteError


def _seed(folder, log_root, n=2):
    mem = WorkspaceMemory(folder, log_root=log_root, actor="t")
    for i in range(n):
        mem.remember({
            "id": f"sha256:x{i}",
            "problem": {"id": f"p{i}", "scope": "s", "type": "rule", "summary": f"i{i}"},
            "solution": {"id": f"sha256:x{i}", "problem_id": f"p{i}", "body": "b",
                         "authority_tier": 1, "confidence": 1.0, "body_format": "prose"},
        })


def test_construct_on_sealed_workspace_does_not_recreate_dir(tmp_path):
    folder = tmp_path / "w"; folder.mkdir(); logr = tmp_path / "log"
    _seed(folder, logr, 2)
    seal.seal_folder(folder, passphrase="pw", log_root=logr)
    log_dir = seal._resolve_log_dir(folder, logr)
    assert not log_dir.exists()           # sealed: plaintext dir is gone

    MutationLog(folder, log_root=logr)    # constructing must NOT recreate it
    WorkspaceMemory(folder, log_root=logr, actor="t")
    assert not log_dir.exists()           # still gone (the bug was recreation)
    assert seal.is_sealed(folder, log_root=logr)

    # unseal still works (it would have refused if the dir had been recreated)
    seal.unseal_folder(folder, passphrase="pw", log_root=logr)
    assert MutationLog(folder, log_root=logr).verify_chain().ok


def test_write_to_sealed_workspace_is_refused(tmp_path):
    folder = tmp_path / "w"; folder.mkdir(); logr = tmp_path / "log"
    _seed(folder, logr, 1)
    seal.seal_folder(folder, passphrase="pw", log_root=logr)

    mem = WorkspaceMemory(folder, log_root=logr, actor="t")   # sealed-safe construction
    with pytest.raises(Exception) as exc:                # remember() → append() refuses
        mem.remember({
            "id": "sha256:new", "problem": {"id": "pn", "scope": "s", "type": "rule", "summary": "n"},
            "solution": {"id": "sha256:new", "problem_id": "pn", "body": "b",
                         "authority_tier": 1, "confidence": 1.0, "body_format": "prose"},
        })
    assert "sealed" in str(exc.value).lower()

    # no plaintext leaked; still sealed and recoverable
    assert seal.is_sealed(folder, log_root=logr)
    seal.unseal_folder(folder, passphrase="pw", log_root=logr)
    assert len(WorkspaceMemory(folder, log_root=logr, actor="t").all_pairs()) == 1


def test_append_raises_typed_error(tmp_path):
    folder = tmp_path / "w"; folder.mkdir(); logr = tmp_path / "log"
    _seed(folder, logr, 1)
    log = MutationLog(folder, log_root=logr)
    seal.seal_folder(folder, passphrase="pw", log_root=logr)
    log2 = MutationLog(folder, log_root=logr)
    from workspaces.mutation_log import LogEvent
    with pytest.raises(SealedWriteError):
        log2.append(LogEvent(event="ingest", channel="system",
                             folder_path=str(folder), pair_id="sha256:z", actor="t"))
