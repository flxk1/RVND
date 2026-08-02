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

import pytest

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


def test_query_requires_versum_index(tmp_path, monkeypatch):
    """workspace_query reads Versum only; an unindexed folder fails closed
    ("index the folder"), never a non-Versum overlay."""
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    srv.workspace_remember(folder_context=str(folder), subject="A", predicate="causes", object="B")

    with pytest.raises(FileNotFoundError, match="index the folder"):
        srv.workspace_query(folder_context=str(folder), subject="A")
