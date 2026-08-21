# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The reason() MCP tool: composition over Versum + auditable recording.

Versum is the only knowledge plane (Language -> Ingest -> Versum -> Solver), so
reason() reads Versum and fails closed on an unindexed workspace. It composes
multi-hop inferences from Versum edges and, with record=True, writes each
inference to the signed reasoning channel so the derivation is auditable. The
legacy pair-overlay that reason() once fell back on has been retired.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from versum.store.graph import Claim, Concept, Edge, save_claims, save_concepts, save_edges

from rvnd.mutation_log import MutationLog


def _fresh_mcp(monkeypatch, log_root: Path):
    import rvnd.mcp_server as srv
    importlib.reload(srv)
    monkeypatch.setattr("rvnd.mcp_serving._log_root", lambda: log_root)
    return srv


def _seed_versum(folder: Path):
    """Index a small causal chain into the folder's Versum store: concept-a
    -> concept-b -> concept-c, so reason() can compose concept-a -> concept-c."""
    root = folder / ".versum"
    root.mkdir(parents=True, exist_ok=True)
    save_claims(root / "claims.csv", [
        Claim("claim-a", "urn:source:a", text="A", predicate="causes", dimension="causal"),
    ], "generic")
    save_concepts(root / "concepts.csv", [
        Concept("concept-a", label="A"), Concept("concept-b", label="B"),
        Concept("concept-c", label="C"),
    ])
    save_edges(root / "semantic_edges.csv", [
        Edge("edge-1", "concept-a", "concept-b", "part_of", confidence="0.8", dimension="structural"),
        Edge("edge-2", "concept-b", "concept-c", "rhymes_with", confidence="0.5", dimension="causal"),
    ])


def test_reason_composes_over_versum_and_records_auditably(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    _seed_versum(folder)
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.reason(folder_context=str(folder), start="concept-a", record=True)
    assert out["knowledge_backend"] == "loomground-versum"
    assert any(i["object"] == "concept-c" for i in out["inferences"])   # composed a-> ... ->c
    assert out["recorded"] >= 1                                          # recorded to the chain
    assert MutationLog(folder, log_root=log_root).verify_chain().ok      # signed + intact


def test_reason_dry_run_records_nothing(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    _seed_versum(folder)
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.reason(folder_context=str(folder), start="concept-a", record=False)
    assert out["recorded"] == 0


def test_reason_requires_versum_index(tmp_path, monkeypatch):
    """No Versum index -> fail closed with a clean error dict (versum required),
    never a silent non-Versum fallback and never an uncaught raise."""
    folder = tmp_path / "wks"; folder.mkdir(); log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.reason(folder_context=str(folder), record=False)
    assert out["knowledge_backend"] is None
    assert "versum index" in out["error"].lower()
    assert out["inferences"] == [] and out["recorded"] == 0
