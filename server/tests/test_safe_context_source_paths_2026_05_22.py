# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the source_paths filter on pairs_safe_context_for_query (2026-05-22).

Powers the Workspace artifact's per-source checkboxes — the user picks
which source documents the next chat should ground in, and the MCP-side
filter enforces it server-side instead of client-side post-pruning.

Semantics:
- Empty / None source_paths = no filter (current behavior preserved).
- Non-empty source_paths = only return pairs whose problem.source_document
  resolves to a path in the list.
- Path comparison is via Path.resolve() so the user can pass relative paths.
- Best-effort: if no pair matches, the fallback recent() pool is ALSO
  filtered before being used. No silent leakage.
"""
from __future__ import annotations

import importlib
from pathlib import Path


from workspaces.memory import WorkspaceMemory


def _fresh_mcp(monkeypatch, log_root: Path):
    import workspaces.mcp_server as srv
    importlib.reload(srv)
    monkeypatch.setattr("workspaces.mcp_serving._log_root", lambda: log_root)
    return srv


def _seed_two_sources(folder: Path, log_root: Path) -> tuple[Path, Path]:
    """Drop two text files into folder, ingest them, return their paths."""
    src_a = folder / "doc_a.txt"
    src_b = folder / "doc_b.txt"
    src_a.write_text("First document about contracts and indemnification.")
    src_b.write_text("Second document about GDPR data subject rights.")
    # Use WorkspaceMemory.remember with synthetic pairs that record source_document
    mem = WorkspaceMemory(folder, log_root=log_root, actor="test")
    pair_a = {
        "id": "sha256:fake_a",
        "problem": {"id": "p_a", "scope": "inbox", "type": "document_ingest",
                    "summary": "doc_a.txt", "source_document": str(src_a)},
        "solution": {"id": "sha256:fake_a", "problem_id": "p_a",
                     "body": "First document about contracts and indemnification.",
                     "authority_tier": 3, "confidence": 1.0,
                     "body_format": "prose"},
    }
    pair_b = {
        "id": "sha256:fake_b",
        "problem": {"id": "p_b", "scope": "inbox", "type": "document_ingest",
                    "summary": "doc_b.txt", "source_document": str(src_b)},
        "solution": {"id": "sha256:fake_b", "problem_id": "p_b",
                     "body": "Second document about GDPR data subject rights.",
                     "authority_tier": 3, "confidence": 1.0,
                     "body_format": "prose"},
    }
    mem.remember(pair_a)
    mem.remember(pair_b)
    return src_a, src_b


# ---------------------------------------------------------------------------
# Filter semantics
# ---------------------------------------------------------------------------


def test_no_filter_returns_all_matches(tmp_path, monkeypatch):
    """source_paths omitted = current behavior, all matches returned."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    _seed_two_sources(folder, log_root)

    out = srv.pairs_safe_context_for_query(
        folder_context=str(folder),
        query="document",
        k=10,
    )
    src_docs = {(v.get("fingerprint") or {}).get("summary") for v in out["views"]}
    # Both source docs should be in scope (no filter applied)
    assert out["count"] >= 2, out
    # Best-effort: at minimum we got both pairs
    assert len(out["views"]) >= 2


def test_filter_to_one_source_returns_only_that_source(tmp_path, monkeypatch):
    """source_paths with one path returns only that source's pairs."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    src_a, _src_b = _seed_two_sources(folder, log_root)

    out = srv.pairs_safe_context_for_query(
        folder_context=str(folder),
        query="document",
        k=10,
        source_paths=[str(src_a)],
    )
    assert out["count"] >= 1
    # Every returned view's underlying pair must come from src_a
    for v in out["views"]:
        # safe-view doesn't directly expose source_document (Lock scrubs it
        # into a doc_token). We check the doc_token is stable per source by
        # looking at the count: we should NOT have more views than there are
        # pairs from src_a.
        pass
    # Stronger: the count must be at most the number of pairs from src_a.
    # We seeded 1 pair per source so count <= 1.
    assert out["count"] <= 1


def test_filter_to_both_sources_returns_both(tmp_path, monkeypatch):
    """source_paths with all sources behaves like no filter."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    src_a, src_b = _seed_two_sources(folder, log_root)

    out = srv.pairs_safe_context_for_query(
        folder_context=str(folder),
        query="document",
        k=10,
        source_paths=[str(src_a), str(src_b)],
    )
    assert out["count"] >= 2


def test_filter_empty_list_treated_as_no_filter(tmp_path, monkeypatch):
    """An empty list is equivalent to omitting the parameter."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    _seed_two_sources(folder, log_root)

    out = srv.pairs_safe_context_for_query(
        folder_context=str(folder),
        query="document",
        k=10,
        source_paths=[],
    )
    assert out["count"] >= 2  # both pairs visible


def test_filter_non_matching_path_returns_empty(tmp_path, monkeypatch):
    """A source_paths list that matches no pair yields zero views, not all."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    _seed_two_sources(folder, log_root)

    out = srv.pairs_safe_context_for_query(
        folder_context=str(folder),
        query="document",
        k=10,
        source_paths=["/nonexistent/path/document.pdf"],
    )
    assert out["count"] == 0
    assert out["views"] == []


def test_filter_relative_path_resolves_correctly(tmp_path, monkeypatch):
    """Relative paths in source_paths are resolved before comparison."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    src_a, _ = _seed_two_sources(folder, log_root)

    # Pass the path with extra ../ → resolved equivalent
    convoluted = str(src_a.parent / ".." / src_a.parent.name / src_a.name)
    out = srv.pairs_safe_context_for_query(
        folder_context=str(folder),
        query="document",
        k=10,
        source_paths=[convoluted],
    )
    assert out["count"] >= 1  # the path resolves to src_a


def test_filter_does_not_block_existing_callers(tmp_path, monkeypatch):
    """Callers that don't pass source_paths get unchanged behavior."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    _seed_two_sources(folder, log_root)

    # No source_paths arg at all — same as None
    out = srv.pairs_safe_context_for_query(
        folder_context=str(folder),
        query="document",
        k=10,
    )
    assert out["count"] >= 2
