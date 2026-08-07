# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""workspace_remember + pairs_search: the local fact/pair store.

Versum is the only knowledge plane, so workspace_query and reason() read Versum
and fail closed on an unindexed workspace. workspace_remember still lands a
typed triple on the signed log, and those triples remain reachable via
pairs_search — the retired pair-overlay that workspace_query/reason once fell
back on is gone.
"""

from __future__ import annotations

import importlib
from pathlib import Path


from workspaces.memory import WorkspaceMemory
from workspaces.mutation_log import MutationLog


def _fresh_mcp(monkeypatch, log_root: Path):
    import workspaces.mcp_server as srv
    importlib.reload(srv)
    monkeypatch.setattr("workspaces.mcp_serving._log_root", lambda: log_root)
    return srv


def test_remember_lands_and_is_found_by_pairs_search(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.workspace_remember(folder_context=str(folder),
                                 subject="breach", predicate="triggers", object="notify")
    assert out["remembered"] is True
    assert out["triple"]["dimension"] == "causal"   # inferred from "triggers"

    hits = srv.pairs_search(folder_context=str(folder), query="breach")
    assert any("breach" in str(r) for r in hits["results"])


def test_explicit_dimension_overrides_inference(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.workspace_remember(folder_context=str(folder), subject="a",
                                 predicate="relates", object="b", dimension="structural")
    assert out["triple"]["dimension"] == "structural"


def test_remember_is_idempotent_and_auditable(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    a = srv.workspace_remember(folder_context=str(folder), subject="x", predicate="p", object="y")
    b = srv.workspace_remember(folder_context=str(folder), subject="x", predicate="p", object="y")
    assert a["pair_id"] == b["pair_id"]                       # same triple -> same id

    assert MutationLog(folder, log_root=log_root).verify_chain().ok   # on the signed chain
    rec = WorkspaceMemory(folder, log_root=log_root, actor="t").by_id(a["pair_id"])
    assert rec is not None and rec["problem"]["facets"]["predicate"] == "p"


def test_remember_rejects_incomplete_triple(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    bad = srv.workspace_remember(folder_context=str(folder), subject="", predicate="p", object="o")
    assert bad["remembered"] is False


def test_query_fails_closed_on_unindexed_folder(tmp_path, monkeypatch):
    """workspace_query reads Versum only; a folder that knows NOTHING (no
    remember, no ingest) fails closed with a clean error dict (versum required),
    never a non-Versum overlay."""
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.workspace_query(folder_context=str(folder), subject="A")
    assert out["knowledge_backend"] is None
    assert "versum index" in out["error"].lower()
    assert out["triples"] == []


def test_remember_closes_the_loop_to_query(tmp_path, monkeypatch):
    """A remembered triple is now first-class Versum knowledge: workspace_remember
    writes it into the folder's Versum store, so workspace_query (Versum-only)
    finds it by its term. This closes the remember->query loop that the retired
    memory-only pair overlay left broken (the write went to memory, the read to
    Versum, so a remembered fact was invisible to query)."""
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    srv.workspace_remember(folder_context=str(folder), subject="A", predicate="causes", object="B")

    out = srv.workspace_query(folder_context=str(folder), subject="A")
    assert out["knowledge_backend"] == "loomground-versum"
    hit = next(t for t in out["triples"] if t["subject"] == "A")
    assert hit["object"] == "B"
    assert hit["dimension"] == "causal"   # inferred from "causes"
