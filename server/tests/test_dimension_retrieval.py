# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Dimension-guided ask-a-folder retrieval."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from rvnd.dimensions import Dimension, classify_query_dimension
from rvnd.memory import WorkspaceMemory


# ── Query-intent classifier ──────────────────────────────────────

@pytest.mark.parametrize(
    "query,expected",
    [
        ("why does the build fail?", Dimension.CAUSAL),
        ("what is this clause for?", Dimension.INTENTIONAL),
        ("how is the system built?", Dimension.STRUCTURAL),
        ("when did the deadline pass?", Dimension.TEMPORAL),
        ("what is this similar to?", Dimension.RELATIONAL),
        ("summarise the document", None),
        ("", None),
    ],
)
def test_classify_query_dimension(query, expected):
    assert classify_query_dimension(query) == expected


# ── Re-rank helper (the load-bearing logic) ──────────────────────

def _pair(pid, *dims):
    return {"id": pid, "edges": [{"dimension": d.value} for d in dims]}


def test_rerank_promotes_matching_dimension_stably():
    import rvnd.mcp_server as srv
    a = _pair("a", Dimension.STRUCTURAL)          # no causal edge
    b = _pair("b", Dimension.CAUSAL)              # causal edge
    c = _pair("c", Dimension.RELATIONAL)          # no causal edge
    out = srv._rerank_by_dimension([a, b, c], Dimension.CAUSAL)
    assert out[0]["id"] == "b"                    # causal pair promoted
    assert [p["id"] for p in out[1:]] == ["a", "c"]  # rest keep their order


def test_rerank_noop_without_hint():
    import rvnd.mcp_server as srv
    pairs = [_pair("a"), _pair("b")]
    assert srv._rerank_by_dimension(pairs, None) is pairs


# ── Integration: the hint surfaces and ordering changes ──────────

def _fresh_mcp(monkeypatch, log_root: Path):
    import rvnd.mcp_server as srv
    importlib.reload(srv)
    monkeypatch.setattr("rvnd.mcp_serving._log_root", lambda: log_root)
    return srv


def _seed_causal_and_plain(folder: Path, log_root: Path):
    """Two pairs that match the same keyword; only one carries a causal edge."""
    mem = WorkspaceMemory(folder, log_root=log_root, actor="test")
    causal = {
        "id": "sha256:causal",
        "problem": {"id": "pc", "scope": "s", "type": "rule",
                    "summary": "the deadline obligation"},
        "solution": {"id": "sha256:causal", "problem_id": "pc",
                     "body": "deadline rule", "authority_tier": 1,
                     "confidence": 1.0, "body_format": "prose"},
        "edges": [{"subject": "pc", "predicate": "applies-when",
                   "object": "late", "dimension": Dimension.CAUSAL.value}],
    }
    plain = {
        "id": "sha256:plain",
        "problem": {"id": "pp", "scope": "s", "type": "rule",
                    "summary": "the deadline definition"},
        "solution": {"id": "sha256:plain", "problem_id": "pp",
                     "body": "deadline term", "authority_tier": 1,
                     "confidence": 1.0, "body_format": "prose"},
        "edges": [{"subject": "pp", "predicate": "belongs-to",
                   "object": "contracts", "dimension": Dimension.STRUCTURAL.value}],
    }
    mem.remember(plain)
    mem.remember(causal)


def test_why_query_sets_causal_hint(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    _seed_causal_and_plain(folder, log_root)

    out = srv.pairs_safe_context_for_query(
        folder_context=str(folder), query="why is the deadline missed?", k=5,
    )
    assert out["dimension_hint"] == Dimension.CAUSAL.value
    assert out["count"] >= 1


def test_neutral_query_has_no_hint(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    _seed_causal_and_plain(folder, log_root)

    out = srv.pairs_safe_context_for_query(
        folder_context=str(folder), query="deadline", k=5,
    )
    assert out["dimension_hint"] is None
