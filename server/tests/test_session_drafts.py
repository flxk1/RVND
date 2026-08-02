# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Drafts through the session bundle: capture is server-sourced (the draft
store, never a caller param), drafts sit under the manifest hash so a tampered
draft refuses the load, and restore rehydrates the destination's store."""
from __future__ import annotations

import json

from workspaces import draft_store as D
from workspaces import parties, session_io as S, session_mcp as M


def _ws(tmp_path, wid: str, *, chat: dict | None = None) -> dict:
    folder = tmp_path / "src" / wid
    folder.mkdir(parents=True)
    lr = str(tmp_path / "logs" / wid)
    parties.register_party(str(folder), f"bot-{wid}", "agent", log_root=lr)
    if chat is not None:
        D.save(str(folder), "chat", chat, log_root=lr)
    return {"folder_context": str(folder), "id": wid, "log_root": lr}


def test_build_embeds_drafts_from_the_store(tmp_path):
    ws = _ws(tmp_path, "alpha", chat={"policy_accum": "no pii egress"})
    built = M.session_build([ws], {"order": ["alpha"], "focused": "alpha"},
                            name="env")
    assert built["ok"]
    doc = built["bundle"]["workspaces"][0]
    assert doc["drafts"] == {"chat": {"policy_accum": "no pii egress"}}


def test_caller_supplied_drafts_are_ignored(tmp_path):
    """A client cannot inject drafts into the signed bundle — the store is
    the only source."""
    ws = _ws(tmp_path, "alpha", chat={"policy_accum": "real"})
    ws["drafts"] = {"chat": {"policy_accum": "forged"}}
    built = M.session_build([ws], {"order": ["alpha"], "focused": "alpha"},
                            name="env")
    assert built["bundle"]["workspaces"][0]["drafts"] == {
        "chat": {"policy_accum": "real"}}


def test_workspace_without_drafts_gets_an_empty_slot(tmp_path):
    ws = _ws(tmp_path, "alpha")
    built = M.session_build([ws], {"order": ["alpha"], "focused": "alpha"},
                            name="env")
    assert built["bundle"]["workspaces"][0]["drafts"] == {}


def test_tampered_draft_refuses_the_load(tmp_path):
    ws = _ws(tmp_path, "alpha", chat={"policy_accum": "no pii egress"})
    built = M.session_build([ws], {"order": ["alpha"], "focused": "alpha"},
                            name="env")
    b = json.loads(json.dumps(built["bundle"]))
    b["workspaces"][0]["drafts"]["chat"]["policy_accum"] = "swapped"
    out = M.session_verify_bytes(b)
    assert out["ok"] is False
    assert out["report"]["refusal"]["reason"] == "altered_content"
    assert "drafts" in out["report"]["refusal"]["detail"]


def test_restore_rehydrates_the_destination_store(tmp_path):
    ws = _ws(tmp_path, "alpha", chat={"policy_accum": "no pii egress"})
    built = M.session_build([ws], {"order": ["alpha"], "focused": "alpha"},
                            name="env")
    dl = str(tmp_path / "dl")
    out = M.session_restore_bytes(built["bundle"], str(tmp_path / "dest"),
                                  log_root_for={"alpha": dl})
    assert out["ok"]
    dest = out["folders"]["alpha"]
    got = D.load(dest, "chat", log_root=dl)
    assert got["ok"] and got["payload"] == {"policy_accum": "no pii egress"}
    # facade view matches (what the surface's draft_load would render)
    via_op = M.workspace_session("draft_load",
                                 {"folder_context": dest, "log_root": dl})
    assert via_op["drafts"] == {"chat": {"policy_accum": "no pii egress"}}


def test_refused_bundle_drafts_are_named_not_dropped(tmp_path):
    """A hand-built bundle can carry junk in the drafts slot; only whitelist
    surfaces with object payloads land in the destination store, and every
    refusal is named in the result — never a silent drop."""
    ws = _ws(tmp_path, "alpha")
    doc = S.capture_workspace(ws["folder_context"], workspace_id="alpha",
                              log_root=ws["log_root"])
    doc["drafts"] = {"evil": {"x": 1}, "chat": "not-an-object",
                     "map": {"text": "kept"}}
    dl = str(tmp_path / "dl")
    _, refused = S.restore_workspace(doc, str(tmp_path / "dest" / "alpha"),
                                     log_root=dl)
    assert {r["surface"] for r in refused} == {"evil", "chat"}
    assert all(r["error"] for r in refused)
    r = D.load(str(tmp_path / "dest" / "alpha"), log_root=dl)
    assert r["drafts"] == {"map": {"text": "kept"}}


def test_restore_result_carries_drafts_refused_per_workspace(tmp_path):
    """An over-cap draft in a bundle refuses at the destination store; the
    restore succeeds, the other surface lands, and the restore/adopt result
    names the refusal (workspace + surface + reason)."""
    ws = _ws(tmp_path, "alpha", chat={"policy_accum": "kept"})
    bundle = M.session_build([ws], {"order": ["alpha"], "focused": "alpha"},
                             name="env")["bundle"]
    # a bundle can carry a draft past the save-side cap (the cap guards the
    # store, not the wire format); the destination store still refuses it
    bundle["workspaces"][0]["drafts"]["map"] = {
        "text": "x" * (D.MAX_PAYLOAD_BYTES + 1)}
    applied = S.restore_environment(
        bundle, str(tmp_path / "dest"),
        log_root_for={"alpha": str(tmp_path / "dl")})
    refused = applied["drafts_refused"]["alpha"]
    assert [r["surface"] for r in refused] == ["map"]
    assert str(D.MAX_PAYLOAD_BYTES) in refused[0]["error"]
    got = D.load(applied["folders"]["alpha"], "chat",
                 log_root=str(tmp_path / "dl"))
    assert got["payload"] == {"policy_accum": "kept"}   # the rest landed
    # a clean bundle reports no refusals (key present, empty)
    clean = M.session_restore_bytes(
        M.session_build([ws], {"order": ["alpha"], "focused": "alpha"},
                        name="env")["bundle"],
        str(tmp_path / "dest2"), log_root_for={"alpha": str(tmp_path / "dl2")})
    assert clean["ok"] and clean["drafts_refused"] == {}


def test_save_restore_save_draft_equality(tmp_path):
    """The round-trip gate, drafts included: a re-capture of the restored
    workspace reproduces the original drafts slot byte-for-byte."""
    ws = _ws(tmp_path, "alpha", chat={"policy_accum": "x",
                                      "transcript": [{"who": "you", "text": "hi"}]})
    D.save(ws["folder_context"], "policy_paste", {"text": "no automated hiring"},
           log_root=ws["log_root"])
    built = M.session_build([ws], {"order": ["alpha"], "focused": "alpha"},
                            name="env")
    dl = str(tmp_path / "dl")
    out = M.session_restore_bytes(built["bundle"], str(tmp_path / "dest"),
                                  log_root_for={"alpha": dl})
    again = M.session_build(
        [{"folder_context": out["folders"]["alpha"], "id": "alpha",
          "log_root": dl}],
        {"order": ["alpha"], "focused": "alpha"}, name="env")
    assert (again["bundle"]["workspaces"][0]["drafts"]
            == built["bundle"]["workspaces"][0]["drafts"])


def test_draft_ops_route_through_the_facade(tmp_path):
    ws = _ws(tmp_path, "alpha")
    saved = M.workspace_session("draft_save", {
        "folder_context": ws["folder_context"], "surface": "map",
        "payload": {"text": "Article 9"}, "log_root": ws["log_root"]})
    assert saved["ok"]
    loaded = M.workspace_session("draft_load", {
        "folder_context": ws["folder_context"], "surface": "map",
        "log_root": ws["log_root"]})
    assert loaded["ok"] and loaded["payload"] == {"text": "Article 9"}
    refused = M.workspace_session("draft_save", {
        "folder_context": ws["folder_context"], "surface": "nope",
        "payload": {}, "log_root": ws["log_root"]})
    assert refused["ok"] is False and "unknown draft surface" in refused["error"]
    gone = M.workspace_session("draft_discard", {
        "folder_context": ws["folder_context"], "log_root": ws["log_root"]})
    assert gone["ok"] and gone["discarded"] == ["map"]
