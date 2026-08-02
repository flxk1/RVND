# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The reason() MCP tool: composition + auditable recording."""

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


def _seed_causal_chain(folder: Path, log_root: Path):
    mem = WorkspaceMemory(folder, log_root=log_root, actor="t")
    mem.remember({
        "id": "sha256:e1",
        "problem": {"id": "p1", "scope": "gdpr", "type": "rule", "summary": "breach -> notify"},
        "solution": {"id": "sha256:e1", "problem_id": "p1", "body": "b",
                     "authority_tier": 1, "confidence": 0.9, "body_format": "prose"},
        "edges": [{"subject": "breach", "predicate": "triggers", "object": "notify",
                   "dimension": "causal"}],
    })
    mem.remember({
        "id": "sha256:e2",
        "problem": {"id": "p2", "scope": "gdpr", "type": "rule", "summary": "notify -> fine"},
        "solution": {"id": "sha256:e2", "problem_id": "p2", "body": "b",
                     "authority_tier": 1, "confidence": 0.9, "body_format": "prose"},
        "edges": [{"subject": "notify", "predicate": "enables", "object": "compliance",
                   "dimension": "causal"}],
    })


def test_reason_composes_and_records_auditably(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    _seed_causal_chain(folder, log_root)

    out = srv.reason(folder_context=str(folder), max_depth=3, record=True)

    # Composed the two causal hops into one inference.
    assert out["count"] >= 1
    inf = out["inferences"][0]
    assert (inf["subject"], inf["object"]) == ("breach", "compliance")
    assert inf["dimension"] == "causal"
    assert inf["confidence"] == 0.81            # 0.9 * 0.9
    # Provenance: the two source pairs, in order.
    assert [hop["source_pair"] for hop in inf["path"]] == ["sha256:e1", "sha256:e2"]

    # Auditable: the inference was recorded to the signed log AND the chain still verifies.
    assert out["recorded"] >= 1
    assert MutationLog(folder, log_root=log_root).verify_chain().ok

    # The recorded inference is reconstructable from memory with its provenance.
    rec = WorkspaceMemory(folder, log_root=log_root, actor="t").by_id(out["recorded_ids"][0])
    assert rec is not None
    assert rec["problem"]["facets"]["dimension"] == "causal"
    assert rec["problem"]["facets"]["via"]        # the provenance path is stored


def test_reason_does_not_feed_on_its_own_output(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    _seed_causal_chain(folder, log_root)

    first = srv.reason(folder_context=str(folder), record=True)
    second = srv.reason(folder_context=str(folder), record=True)
    # Recorded inferences are excluded from the grounded set, so the second run
    # sees the same source facts and derives the same count — no runaway.
    assert second["count"] == first["count"]


def test_reason_dry_run_records_nothing(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    _seed_causal_chain(folder, log_root)

    out = srv.reason(folder_context=str(folder), record=False)
    assert out["count"] >= 1
    assert out["recorded"] == 0


def test_reason_is_a_declared_tool(tmp_path, monkeypatch):
    # 2026-06-12 surface fold: reason left the registered surface and is
    # the workspace_memory op "reason"; the function itself stays (tests above).
    srv = _fresh_mcp(monkeypatch, tmp_path / "log")
    assert "reason" not in srv._DECLARED_TOOLS
    ops = {o["op"] for o in srv.workspace_memory("help")["ops"]}
    assert "reason" in ops
