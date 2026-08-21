# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""MCP audit-floor + alias kwarg fixes.

N2: local_llm_complete called capture_llm(prompt=…, citations=…) and read
    .get("ok") — none of which exist — so every local completion raised
    TypeError (swallowed) and was NEVER audited. captured was always False.
N3: the workspace_folder/workspace_memory ops write_file/ingest/pair_spans forwarded
    wrong kwarg names to their targets → uncaught TypeError on every call.

Both are MCP-blocker, gateway-reachable. These tests assert the audit row is
actually written (captured:True) and that each advertised op actually runs."""
from __future__ import annotations

import pytest

from rvnd import mcp_impl
from rvnd import mcp_server


# ── N2: local_llm_complete writes the audit-floor capture row ────────────────

@pytest.fixture
def _stub_local_complete(monkeypatch):
    """Stub the real local completion so no model/network is needed."""
    import rvnd.local_llm as local_llm

    def _fake_complete(prompt, model=None, temperature=0.0, max_tokens=512):
        return {
            "ok": True,
            "response": "2 + 2 = 4",
            "model_used": "test-model",
            "latency_ms": 3,
            "endpoint_host": "127.0.0.1:11434",
        }
    monkeypatch.setattr(local_llm, "complete", _fake_complete)


def test_local_llm_complete_writes_capture_row(monkeypatch, tmp_path):
    # Stub completion to return a response containing an email, so we can prove
    # the persisted pair is redacted (D1), not just that a bool flipped.
    import rvnd.local_llm as local_llm
    monkeypatch.setattr(local_llm, "complete", lambda prompt, model=None,
                        temperature=0.0, max_tokens=512: {
        "ok": True, "response": "contact alice\x40secret.example for details",
        "model_used": "test-model", "latency_ms": 3, "endpoint_host": "127.0.0.1:11434",
    })
    log_root = tmp_path / "logs"
    monkeypatch.setattr(mcp_impl, "_log_root", lambda: str(log_root))
    folder = tmp_path / "vault"
    folder.mkdir()

    res = mcp_impl.local_llm_complete("email bob\x40private.example?", str(folder), capture=True)

    assert res["ok"] is True
    # The regression: this was always False because the capture call TypeError'd.
    assert res["captured"] is True, res.get("capture_error")
    assert "capture_error" not in res
    assert res.get("pair_id")                       # correlation id now surfaced

    # Deep check: the pair was really persisted AND the raw emails are redacted.
    from rvnd.memory import WorkspaceMemory
    mem = WorkspaceMemory(str(folder), log_root=str(log_root))
    pair = mem.by_id(res["pair_id"])
    assert pair is not None
    blob = repr(pair)
    assert "bob\x40private.example" not in blob        # prompt redacted (D1)
    assert "alice\x40secret.example" not in blob       # response redacted (D1)


def test_folder_write_file_rejects_directory_parts(tmp_path):
    # A path with a separator must be REFUSED, not silently flattened to a
    # basename (which would write to an unexpected location).
    res = mcp_server.folder_write_file(str(tmp_path), "sub/note.txt", "x")
    assert "error" in res
    assert not (tmp_path / "note.txt").exists()     # nothing written anywhere
    assert not (tmp_path / "sub" / "note.txt").exists()


def test_local_llm_complete_capture_false_when_disabled(_stub_local_complete, tmp_path):
    folder = tmp_path / "vault"
    folder.mkdir()
    res = mcp_impl.local_llm_complete("hi", str(folder), capture=False)
    assert res["ok"] is True
    assert res["captured"] is False


# ── N3: advertised ops actually run (no TypeError on every call) ─────────────

def test_folder_write_file_runs_and_writes(tmp_path):
    res = mcp_server.folder_write_file(str(tmp_path), "note.txt",
                                       "hello world", actor="alex")
    assert res.get("written") is True
    # write_file_to_folder flattens to a basename + sanitises.
    assert (tmp_path / res["sanitised_filename"]).read_bytes() == b"hello world"


def test_folder_ingest_runs_without_typeerror(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("some ingestible content", encoding="utf-8")
    # Must return a dict (ok or a graceful error) — never raise TypeError.
    res = mcp_impl.folder_ingest(str(doc), folder_context=str(tmp_path), actor="alex")
    assert isinstance(res, dict)
    assert "TypeError" not in str(res)


def test_pair_spans_runs_without_typeerror(tmp_path):
    # A missing pair must yield a graceful dict, not a TypeError from a bad
    # forward (pair_ids list / no span_count).
    res = mcp_impl.pair_spans(str(tmp_path), "nonexistent-pair", span_count=3)
    assert isinstance(res, dict)
    assert "TypeError" not in str(res)
