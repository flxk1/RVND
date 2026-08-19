# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Draft store contract: per-surface round-trip, whitelist + size + sealed
refusals, corrupt-file preservation, no chain writes, and the erasure hooks
(scan/redact). Run: pytest server/tests/test_draft_store.py"""
from __future__ import annotations

import json

import pytest

from rvnd import draft_store as D
from rvnd import seal
from rvnd.memory import WorkspaceMemory
from rvnd.mutation_log import MutationLog


@pytest.fixture
def env(tmp_path):
    folder = tmp_path / "ws"
    folder.mkdir()
    return {"folder": str(folder), "log_root": str(tmp_path / "log")}


def test_save_load_round_trip_per_surface(env):
    payloads = {
        "policy_paste": {"text": "no automated hiring", "use_llm": False},
        "map": {"text": "Article 9", "group_by": "role", "question": ""},
        "cards": {"cards": [{"domain": "hr", "description": "screening bot"}]},
        "officers": {"entries": [{"role": "compliance"}]},
        "chat": {"policy_accum": "no pii egress", "transcript": [
            {"who": "you", "text": "hello", "intent": "", "ts": "t0"}]},
    }
    for surface, payload in payloads.items():
        r = D.save(env["folder"], surface, payload, log_root=env["log_root"])
        assert r["ok"] and r["surface"] == surface and r["updated"]
    for surface, payload in payloads.items():
        r = D.load(env["folder"], surface, log_root=env["log_root"])
        assert r["ok"] and r["payload"] == payload


def test_load_all_shape_and_missing_is_empty(env):
    assert D.load(env["folder"], log_root=env["log_root"]) == {
        "ok": True, "drafts": {}, "updated": {}, "unreadable": {}}
    D.save(env["folder"], "chat", {"policy_accum": "x"}, log_root=env["log_root"])
    r = D.load(env["folder"], log_root=env["log_root"])
    assert set(r["drafts"]) == {"chat"} and r["updated"]["chat"]
    assert D.load_all(env["folder"], log_root=env["log_root"]) == {
        "chat": {"policy_accum": "x"}}


def test_missing_single_surface_is_an_empty_draft_not_an_error(env):
    r = D.load(env["folder"], "map", log_root=env["log_root"])
    assert r["ok"] and r["payload"] == {} and r["updated"] == ""


def test_unknown_surface_refused_on_save_load_discard(env):
    for r in (D.save(env["folder"], "evil/../x", {}, log_root=env["log_root"]),
              D.load(env["folder"], "nope", log_root=env["log_root"]),
              D.discard(env["folder"], "nope", log_root=env["log_root"])):
        assert not r["ok"] and "unknown draft surface" in r["error"]
    assert not D.drafts_dir(env["folder"], env["log_root"]).exists()


def test_non_dict_payload_refused(env):
    r = D.save(env["folder"], "chat", ["not", "an", "object"],
               log_root=env["log_root"])
    assert not r["ok"] and "JSON object" in r["error"]


def test_oversize_payload_refused_with_size_named(env):
    big = {"text": "x" * (D.MAX_PAYLOAD_BYTES + 1)}
    r = D.save(env["folder"], "policy_paste", big, log_root=env["log_root"])
    assert not r["ok"] and str(D.MAX_PAYLOAD_BYTES) in r["error"]


def test_sealed_workspace_refuses_save_load_discard(env, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd import signing
    signing.ensure_keypair()
    mem = WorkspaceMemory(env["folder"], log_root=env["log_root"], actor="t")
    mem.remember({
        "id": "sha256:x0",
        "problem": {"id": "p0", "scope": "s", "type": "rule", "summary": "i"},
        "solution": {"id": "sha256:x0", "problem_id": "p0", "body": "b",
                     "authority_tier": 1, "confidence": 1.0,
                     "body_format": "prose"}})
    D.save(env["folder"], "chat", {"policy_accum": "x"}, log_root=env["log_root"])
    seal.seal_folder(env["folder"], passphrase="pw", log_root=env["log_root"])
    for r in (D.save(env["folder"], "chat", {"policy_accum": "y"},
                     log_root=env["log_root"]),
              D.load(env["folder"], log_root=env["log_root"]),
              D.discard(env["folder"], log_root=env["log_root"]),
              D.redact(env["folder"], "x", log_root=env["log_root"])):
        assert not r["ok"] and "sealed" in r["error"]
    assert D.load_all(env["folder"], log_root=env["log_root"]) == {}
    # sealed drafts are uninspectable — scan says so instead of "clean"
    assert D.scan(env["folder"], "x", log_root=env["log_root"]) == {
        "hits": {}, "unreadable": [], "sealed": True}
    # the drafts rode into the sealed blob and come back on unseal
    seal.unseal_folder(env["folder"], passphrase="pw", log_root=env["log_root"])
    r = D.load(env["folder"], "chat", log_root=env["log_root"])
    assert r["ok"] and r["payload"] == {"policy_accum": "x"}


def test_corrupt_file_reported_and_preserved(env):
    D.save(env["folder"], "map", {"text": "t"}, log_root=env["log_root"])
    path = D.draft_path(env["folder"], "map", env["log_root"])
    path.write_text("{not json", encoding="utf-8")
    r = D.load(env["folder"], "map", log_root=env["log_root"])
    assert not r["ok"] and r["path"] == str(path)
    assert path.read_text(encoding="utf-8") == "{not json"   # preserved
    r = D.load(env["folder"], log_root=env["log_root"])
    assert r["ok"] and r["unreadable"] == {"map": str(path)}
    assert D.load_all(env["folder"], log_root=env["log_root"]) == {}
    # discard is the explicit recovery
    assert D.discard(env["folder"], "map", log_root=env["log_root"])["discarded"] == ["map"]
    assert not path.exists()


def test_discard_all_and_idempotent(env):
    D.save(env["folder"], "chat", {"a": 1}, log_root=env["log_root"])
    D.save(env["folder"], "map", {"b": 2}, log_root=env["log_root"])
    r = D.discard(env["folder"], log_root=env["log_root"])
    assert r["ok"] and sorted(r["discarded"]) == ["chat", "map"]
    assert D.discard(env["folder"], log_root=env["log_root"])["discarded"] == []


def test_atomic_write_leaves_no_tmp_file(env):
    D.save(env["folder"], "chat", {"a": 1}, log_root=env["log_root"])
    d = D.drafts_dir(env["folder"], env["log_root"])
    assert sorted(p.name for p in d.iterdir()) == ["chat.json"]


def test_draft_ops_write_no_chain_events(env):
    D.save(env["folder"], "chat", {"a": 1}, log_root=env["log_root"])
    D.load(env["folder"], log_root=env["log_root"])
    D.discard(env["folder"], log_root=env["log_root"])
    log = MutationLog(env["folder"], log_root=env["log_root"])
    assert list(log.replay()) == []


def test_scan_counts_subject_across_surfaces_case_insensitive(env):
    D.save(env["folder"], "chat", {"transcript": [
        {"who": "you", "text": "erase Ada Lovelace please"},
        {"who": "rvnd", "text": "ada lovelace appears twice: ada lovelace"}]},
        log_root=env["log_root"])
    D.save(env["folder"], "map", {"text": "nothing here"}, log_root=env["log_root"])
    r = D.scan(env["folder"], "Ada Lovelace", log_root=env["log_root"])
    assert r == {"hits": {"chat": 3}, "unreadable": [], "sealed": False}


def test_redact_rewrites_strings_and_deletes_unreadable(env):
    D.save(env["folder"], "chat", {"transcript": [
        {"who": "you", "text": "erase Ada Lovelace please"}]},
        log_root=env["log_root"])
    D.save(env["folder"], "policy_paste", {"text": "clean"}, log_root=env["log_root"])
    bad = D.draft_path(env["folder"], "cards", env["log_root"])
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")
    r = D.redact(env["folder"], "ada lovelace", log_root=env["log_root"])
    assert r["ok"] and r["redacted"] == {"chat": 1} and r["deleted"] == ["cards"]
    assert not bad.exists()
    after = D.load(env["folder"], "chat", log_root=env["log_root"])["payload"]
    assert after["transcript"][0]["text"] == "erase [REDACTED] please"
    # untouched surface keeps its bytes
    assert D.load(env["folder"], "policy_paste",
                  log_root=env["log_root"])["payload"] == {"text": "clean"}


def test_stale_rewrite_scratch_is_scanned_and_removed(env):
    """A crash between save's tmp write and its os.replace leaves a full
    draft payload in <surface>.json.tmp — the sweep must name it and
    execute must delete it, or an erased subject survives on disk."""
    d = D.drafts_dir(env["folder"], env["log_root"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "chat.json.tmp").write_text(
        '{"surface": "chat", "payload": {"text": "Ada Lovelace"}}',
        encoding="utf-8")
    r = D.scan(env["folder"], "Ada Lovelace", log_root=env["log_root"])
    assert r["unreadable"] == ["chat.json.tmp"] and r["hits"] == {}
    res = D.redact(env["folder"], "Ada Lovelace", log_root=env["log_root"])
    assert res["ok"] and res["deleted"] == ["chat.json.tmp"]
    assert not (d / "chat.json.tmp").exists()


def test_envelope_shape_on_disk(env):
    D.save(env["folder"], "map", {"text": "t"}, log_root=env["log_root"])
    raw = json.loads(D.draft_path(env["folder"], "map", env["log_root"])
                     .read_text(encoding="utf-8"))
    assert set(raw) == {"surface", "updated", "payload"}
    assert raw["surface"] == "map" and raw["payload"] == {"text": "t"}
