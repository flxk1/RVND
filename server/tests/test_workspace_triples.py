# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""workspace_remember / workspace_query: the triple façade over the pair+edge store."""

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


def test_remember_then_query_roundtrip(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.workspace_remember(folder_context=str(folder),
                              subject="breach", predicate="triggers", object="notify")
    assert out["remembered"] is True
    assert out["triple"]["dimension"] == "causal"   # inferred from "triggers"

    # Found by each component and by wildcard.
    assert srv.workspace_query(folder_context=str(folder), subject="breach")["count"] == 1
    assert srv.workspace_query(folder_context=str(folder), predicate="triggers")["count"] == 1
    assert srv.workspace_query(folder_context=str(folder), object="notify")["count"] == 1
    allq = srv.workspace_query(folder_context=str(folder))
    assert allq["count"] == 1
    t = allq["triples"][0]
    assert (t["subject"], t["predicate"], t["object"], t["dimension"]) == \
        ("breach", "triggers", "notify", "causal")
    assert t["source_pair"] == out["pair_id"]


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


def test_query_filters_and_missing_component(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    srv.workspace_remember(folder_context=str(folder), subject="A", predicate="causes", object="B")
    srv.workspace_remember(folder_context=str(folder), subject="A", predicate="part-of", object="C")
    assert srv.workspace_query(folder_context=str(folder), subject="A")["count"] == 2
    assert srv.workspace_query(folder_context=str(folder), predicate="causes")["count"] == 1
    assert srv.workspace_query(folder_context=str(folder), subject="A", object="C")["count"] == 1

    bad = srv.workspace_remember(folder_context=str(folder), subject="", predicate="p", object="o")
    assert bad["remembered"] is False


def test_remembered_triples_feed_reasoning(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    srv.workspace_remember(folder_context=str(folder), subject="X", predicate="causes", object="Y")
    srv.workspace_remember(folder_context=str(folder), subject="Y", predicate="causes", object="Z")

    out = srv.reason(folder_context=str(folder), record=False)
    inf = [i for i in out["inferences"] if i["subject"] == "X" and i["object"] == "Z"]
    assert inf and inf[0]["dimension"] == "causal"   # composed from the two asserted facts
