# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace Lock session — unlock caches the key, serve reads sealed-but-unlocked,
locked workspaces refuse, unsealed workspaces read from disk."""

from __future__ import annotations

import pytest

from rvnd import seal_binding, seal
from rvnd.memory import WorkspaceMemory


def _seed(folder, log_root, n=3):
    mem = WorkspaceMemory(folder, log_root=log_root, actor="t")
    for i in range(n):
        mem.remember({
            "id": f"sha256:x{i}",
            "problem": {"id": f"p{i}", "scope": "s", "type": "rule", "summary": f"i{i}"},
            "solution": {"id": f"sha256:x{i}", "problem_id": f"p{i}", "body": "b",
                         "authority_tier": 1, "confidence": 1.0, "body_format": "prose"},
        })


def test_unsealed_workspace_serves_from_disk(tmp_path):
    folder = tmp_path / "w"; folder.mkdir(); log_root = tmp_path / "log"
    _seed(folder, log_root, 2)
    assert not seal_binding.is_sealed(folder, log_root=log_root)
    store = seal_binding.serve(folder, log_root=log_root)
    assert "events.jsonl" in store


def test_sealed_locked_workspace_refuses(tmp_path):
    folder = tmp_path / "w"; folder.mkdir(); log_root = tmp_path / "log"
    _seed(folder, log_root, 2)
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    seal_binding.lock(folder, log_root=log_root)  # ensure no stale key
    assert seal_binding.is_sealed(folder, log_root=log_root)
    assert not seal_binding.is_unlocked(folder, log_root=log_root)
    with pytest.raises(seal.SealError):
        seal_binding.serve(folder, log_root=log_root)


def test_unlock_then_serve_uses_cached_key(tmp_path):
    folder = tmp_path / "w"; folder.mkdir(); log_root = tmp_path / "log"
    _seed(folder, log_root, 3)
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)

    out = seal_binding.unlock(folder, passphrase="pw", log_root=log_root)
    assert out["unlocked"] and seal_binding.is_unlocked(folder, log_root=log_root)

    # serve() now works WITHOUT re-supplying the passphrase (cached key).
    store = seal_binding.serve(folder, log_root=log_root)
    assert "events.jsonl" in store
    # still sealed on disk — serving did not unseal.
    assert seal_binding.is_sealed(folder, log_root=log_root)

    # state for the UI
    st = seal_binding.state(folder, log_root=log_root)
    assert st == {"sealed": True, "unlocked": True, "wall": "up"}

    # lock drops the key; serve refuses again.
    seal_binding.lock(folder, log_root=log_root)
    assert not seal_binding.is_unlocked(folder, log_root=log_root)
    with pytest.raises(seal.SealError):
        seal_binding.serve(folder, log_root=log_root)


def test_unlock_wrong_passphrase_does_not_cache(tmp_path):
    folder = tmp_path / "w"; folder.mkdir(); log_root = tmp_path / "log"
    _seed(folder, log_root, 1)
    seal.seal_folder(folder, passphrase="right", log_root=log_root)
    with pytest.raises(seal.SealError):
        seal_binding.unlock(folder, passphrase="nope", log_root=log_root)
    assert not seal_binding.is_unlocked(folder, log_root=log_root)
    # right passphrase still works
    seal_binding.unlock(folder, passphrase="right", log_root=log_root)
    assert seal_binding.serve_file(folder, "events.jsonl", log_root=log_root)
    seal_binding.lock(folder, log_root=log_root)


def test_read_pairs_serves_sealed_unlocked_matches_disk(tmp_path):
    from rvnd.memory import WorkspaceMemory
    folder = tmp_path / "w"; folder.mkdir(); log_root = tmp_path / "log"
    _seed(folder, log_root, 3)
    before = {p["id"] for p in WorkspaceMemory(folder, log_root=log_root, actor="t").all_pairs()}
    assert len(before) == 3
    # unsealed: read_pairs reads from disk
    assert set(seal_binding.read_pairs(folder, log_root=log_root)) == before

    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    seal_binding.unlock(folder, passphrase="pw", log_root=log_root)
    # sealed + unlocked: served from memory, same pairs, still sealed on disk
    assert set(seal_binding.read_pairs(folder, log_root=log_root)) == before
    assert seal_binding.is_sealed(folder, log_root=log_root)

    seal_binding.lock(folder, log_root=log_root)
    with pytest.raises(seal.SealError):
        seal_binding.read_pairs(folder, log_root=log_root)


def test_recent_tool_serves_sealed_unlocked_refuses_locked(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    from rvnd import mcp_server as M
    recent = getattr(M.recent, "fn", M.recent)
    unlock = getattr(M.workspace_lock_unlock, "fn", M.workspace_lock_unlock)
    lock_t = getattr(M.workspace_lock_lock, "fn", M.workspace_lock_lock)
    logr = tmp_path / "log"; folder = tmp_path / "w"; folder.mkdir()
    _seed(folder, logr, 3)

    r = recent(str(folder))
    assert r["count"] == 3 and not r.get("locked")

    seal.seal_folder(folder, passphrase="pw", log_root=logr)
    seal_binding.lock(folder, log_root=logr)
    r = recent(str(folder))
    assert r.get("locked") is True and r["count"] == 0

    u = unlock(str(folder), "pw")
    assert u["ok"] and u["sealed"] and u["unlocked"]
    r = recent(str(folder))
    assert r.get("served_sealed") and r["count"] == 3

    lock_t(str(folder))
    r = recent(str(folder))
    assert r.get("locked") is True


def test_search_and_by_id_serve_or_refuse_when_sealed(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    from rvnd import mcp_server as M
    search = getattr(M.search, "fn", M.search)
    by_id = getattr(M.by_id, "fn", M.by_id)
    unlock = getattr(M.workspace_lock_unlock, "fn", M.workspace_lock_unlock)
    logr = tmp_path / "log"; folder = tmp_path / "w"; folder.mkdir()
    _seed(folder, logr, 3)

    # unsealed: normal
    assert "results" in search(str(folder), "i1")
    assert by_id(str(folder), "sha256:x0")["found"] is True

    seal.seal_folder(folder, passphrase="pw", log_root=logr)
    seal_binding.lock(folder, log_root=logr)
    assert search(str(folder), "i1").get("locked") is True
    assert by_id(str(folder), "sha256:x0").get("found") is False

    unlock(str(folder), "pw")
    rs = search(str(folder), "i1")
    assert rs.get("served_sealed") and len(rs["results"]) >= 1
    b = by_id(str(folder), "sha256:x0")
    assert b["found"] is True and b.get("served_sealed") and b["pair"]
    seal_binding.lock(folder, log_root=logr)


def test_safe_context_serves_sealed_unlocked_refuses_locked(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    from rvnd import mcp_server as M
    psc = getattr(M.pairs_safe_context_for_query, "fn", M.pairs_safe_context_for_query)
    unlock = getattr(M.workspace_lock_unlock, "fn", M.workspace_lock_unlock)
    logr = tmp_path / "log"; folder = tmp_path / "w"; folder.mkdir()
    _seed(folder, logr, 3)

    r = psc(str(folder), "i1")
    assert "views" in r and not r.get("locked")

    seal.seal_folder(folder, passphrase="pw", log_root=logr)
    seal_binding.lock(folder, log_root=logr)
    r = psc(str(folder), "i1")
    assert r.get("locked") is True and r["count"] == 0

    unlock(str(folder), "pw")
    r = psc(str(folder), "i1")
    assert r.get("served_sealed") and r["count"] >= 1 and r["views"]
    seal_binding.lock(folder, log_root=logr)


def test_guard_fails_closed_when_seal_state_errors(tmp_path, monkeypatch):
    """A read whose seal-state probe raises must refuse (fail-closed), never
    fall through to a direct disk read of a possibly-sealed workspace."""
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    from rvnd import mcp_server as M
    recent = getattr(M.recent, "fn", M.recent)
    logr = tmp_path / "log"; folder = tmp_path / "w"; folder.mkdir()
    _seed(folder, logr, 3)
    seal.seal_folder(folder, passphrase="pw", log_root=logr)

    def _boom(*a, **k):
        raise RuntimeError("seal state unavailable")
    monkeypatch.setattr(seal_binding, "is_sealed", _boom)

    r = recent(str(folder))
    assert r.get("locked") is True, "guard must refuse, not fall through to disk"
    assert r["count"] == 0 and not r.get("results")
