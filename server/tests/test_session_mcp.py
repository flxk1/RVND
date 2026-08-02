# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""S12 — the workspace_session MCP facade (over session_io).

Exercises the facade directly (independent of mcp_server registration): the
save→verify→restore loop, single-workspace export/import, and — critically —
that fail-closed refusals come back as STRUCTURED results ({"ok": False,
report, forensic}), never a raised traceback across the MCP boundary.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces import draft_store, parties, session_mcp as M


def _live_ws(tmp_path, wid: str) -> dict:
    folder = tmp_path / "src" / wid
    folder.mkdir(parents=True)
    lr = str(tmp_path / "logs" / wid)
    parties.register_party(str(folder), f"bot-{wid}", "agent", log_root=lr)
    # drafts are captured from the workspace's draft store, not from params
    draft_store.save(str(folder), "chat", {"notes": [wid]}, log_root=lr)
    return {"folder_context": str(folder), "id": wid, "name": wid.title(),
            "log_root": lr, "presentation": {"view": "patch"}}


def test_save_verify_restore_roundtrip(tmp_path):
    wss = [_live_ws(tmp_path, "alpha"), _live_ws(tmp_path, "beta")]
    rail = {"order": ["alpha", "beta"], "focused": "alpha"}
    path = str(tmp_path / "env.rvnd")

    saved = M.session_save(wss, rail, path, name="env", signed_by="alex")
    assert saved["ok"] and Path(path).exists()
    assert saved["card"]["workspace_count"] == 2 and saved["card"]["signed_by"] == "alex"

    ver = M.session_verify(path)
    assert ver["ok"] and ver["report"]["ok"] and ver["report"]["referential"]["ok"]

    res = M.session_restore(path, str(tmp_path / "dest"),
                            log_root_for={"alpha": str(tmp_path / "dl/a"),
                                          "beta": str(tmp_path / "dl/b")})
    assert res["ok"] and set(res["folders"]) == {"alpha", "beta"}
    assert res["rail"] == rail
    from workspaces.mutation_log import MutationLog
    assert MutationLog(res["folders"]["alpha"],
                       log_root=str(tmp_path / "dl/a")).verify_chain().ok


def test_verify_returns_structured_refusal_not_exception(tmp_path):
    wss = [_live_ws(tmp_path, "alpha")]
    path = str(tmp_path / "env.rvnd")
    M.session_save(wss, {"order": ["alpha"], "focused": "alpha"}, path, name="env")
    # tamper on disk
    bundle = json.loads(Path(path).read_text())
    obj = json.loads(bundle["workspaces"][0]["chain"]["log_lines"][0])
    obj["extra"]["kind"] = "HACKED"
    bundle["workspaces"][0]["chain"]["log_lines"][0] = json.dumps(obj)
    Path(path).write_text(json.dumps(bundle))

    ver = M.session_verify(path)                     # must NOT raise
    assert ver["ok"] is False
    assert ver["report"]["refusal"]["reason"] == "broken_chain"
    assert ver["forensic"]["workspaces"]["alpha"]["salvageable"] is False


def test_restore_refuses_tampered_file_structured(tmp_path):
    wss = [_live_ws(tmp_path, "alpha")]
    path = str(tmp_path / "env.rvnd")
    M.session_save(wss, {"order": ["alpha"], "focused": "alpha"}, path, name="env")
    bundle = json.loads(Path(path).read_text())
    bundle["rail"]["focused"] = "beta"               # off-chain tamper, manifest stale
    Path(path).write_text(json.dumps(bundle))
    res = M.session_restore(path, str(tmp_path / "dest"))
    assert res["ok"] is False and res["report"]["refusal"]["reason"] == "altered_content"


def test_export_then_import_between_files(tmp_path):
    src = [_live_ws(tmp_path, "alpha"), _live_ws(tmp_path, "beta")]
    dst = [_live_ws(tmp_path, "gamma")]
    src_path, dst_path = str(tmp_path / "src.rvnd"), str(tmp_path / "dst.rvnd")
    M.session_save(src, {"order": ["alpha", "beta"], "focused": "alpha"}, src_path, name="src")
    M.session_save(dst, {"order": ["gamma"], "focused": "gamma"}, dst_path, name="dst")

    track_path = str(tmp_path / "beta.rvnd")
    exp = M.session_export(src_path, "beta", track_path)
    assert exp["ok"] and Path(track_path).exists()

    out_path = str(tmp_path / "merged.rvnd")
    imp = M.session_import(dst_path, track_path, "beta", out_path)
    assert imp["ok"] and imp["card"]["workspace_count"] == 2

    # importing 'beta' again → id collision, structured refusal
    again = M.session_import(out_path, track_path, "beta", str(tmp_path / "x.rvnd"))
    assert again["ok"] is False and "already exists" in again["report"]["refusal"]["detail"]


def test_help_and_unknown_op(tmp_path):
    assert "ops" in M.workspace_session("help")
    assert "unknown op" in M.workspace_session("frobnicate")["error"]


# --- content-based ops (browser holds the .rvnd file) ------------------------

def test_build_verify_bytes_roundtrip(tmp_path):
    wss = [_live_ws(tmp_path, "alpha"), _live_ws(tmp_path, "beta")]
    rail = {"order": ["alpha", "beta"], "focused": "alpha"}
    built = M.session_build(wss, rail, name="env", signed_by="alex")
    assert built["ok"] and built["bundle"]["format"] == "rvnd-session"

    ok = M.session_verify_bytes(built["bundle"])       # browser re-uploads it
    assert ok["ok"] and ok["report"]["referential"]["ok"]
    assert ok["card"]["signed_by"] == "alex"


def test_verify_bytes_structured_refusal(tmp_path):
    built = M.session_build([_live_ws(tmp_path, "alpha")],
                            {"order": ["alpha"], "focused": "alpha"}, name="env")
    b = built["bundle"]
    b["rail"]["focused"] = "beta"                       # off-chain tamper
    out = M.session_verify_bytes(b)                     # must not raise
    assert out["ok"] is False
    assert out["report"]["refusal"]["reason"] == "altered_content"
    assert "workspaces" in out["forensic"]


def test_restore_bytes_reconstructs(tmp_path):
    built = M.session_build([_live_ws(tmp_path, "alpha")],
                            {"order": ["alpha"], "focused": "alpha"}, name="env")
    out = M.session_restore_bytes(built["bundle"], str(tmp_path / "dest"),
                                  log_root_for={"alpha": str(tmp_path / "dl")})
    assert out["ok"] and "alpha" in out["folders"]
    from workspaces.mutation_log import MutationLog
    assert MutationLog(out["folders"]["alpha"],
                       log_root=str(tmp_path / "dl")).verify_chain().ok


# --- draft ops over the facade (no log_root param on the wire) ----------------

def test_facade_draft_ops_resolve_the_serving_log_root(tmp_path, monkeypatch):
    """MCP callers pass only folder_context + surface; the drafts must land
    under WORKSPACE_L0_LOG_ROOT — the root the chain lives under — not the
    per-user default."""
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    folder = tmp_path / "ws"
    folder.mkdir()
    saved = M.workspace_session("draft_save", {
        "folder_context": str(folder), "surface": "map",
        "payload": {"text": "Article 9", "group_by": "role"}})
    assert saved["ok"]
    assert Path(saved["path"]).is_relative_to(tmp_path / "logs")

    loaded = M.workspace_session("draft_load", {"folder_context": str(folder)})
    assert loaded["ok"] and loaded["drafts"]["map"]["text"] == "Article 9"

    gone = M.workspace_session("draft_discard", {
        "folder_context": str(folder), "surface": "map"})
    assert gone["ok"] and gone["discarded"] == ["map"]
    assert M.workspace_session("draft_load", {
        "folder_context": str(folder)})["drafts"] == {}
