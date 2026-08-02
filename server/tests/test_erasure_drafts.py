# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Erasure reach into draft files: the sweep reports subject occurrences in
drafts, execute rewrites them (deleting unparseable files), the composite
tombstone carries the counts, and dry_run touches nothing."""
from __future__ import annotations

import pytest

from workspaces import draft_store as D
from workspaces import erasure
from workspaces.mutation_log import MutationLog


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from workspaces import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    folder = tmp_path / "ws"
    folder.mkdir()
    log_root = tmp_path / "logs"
    return {"folder": str(folder), "log_root": log_root}


def _seed_drafts(env):
    D.save(env["folder"], "chat", {"transcript": [
        {"who": "you", "text": "please erase Ada Lovelace"}]},
        log_root=env["log_root"])
    D.save(env["folder"], "policy_paste", {"text": "reviews by Ada Lovelace"},
           log_root=env["log_root"])
    D.save(env["folder"], "map", {"text": "no subject here"},
           log_root=env["log_root"])


def test_sweep_reports_draft_hits(env):
    _seed_drafts(env)
    report = erasure.sweep(env["folder"], "Ada Lovelace",
                           log_root=env["log_root"])
    hits = report.hits_by_kind["draft"]
    assert {h.pair_id for h in hits} == {"draft:chat", "draft:policy_paste"}
    assert all(h.kind == "draft" for h in hits)
    # the subject never appears in a snippet
    assert all("Ada" not in h.snippet for h in hits)
    # draft hits are files, not chain pairs — pair counts stay clean
    assert report.estimated_tombstone["affected_pair_count"] == 0
    assert report.estimated_tombstone["hits_by_kind_count"]["draft"] == 2


def test_sweep_reports_unreadable_draft(env):
    bad = D.draft_path(env["folder"], "cards", env["log_root"])
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")
    report = erasure.sweep(env["folder"], "Ada Lovelace",
                           log_root=env["log_root"])
    hits = report.hits_by_kind["draft"]
    assert [h.pair_id for h in hits] == ["draft:cards"]
    assert "unreadable" in hits[0].snippet


def test_sweep_names_sealed_folder_instead_of_claiming_clean(env):
    """A sealed folder's drafts cannot be inspected; the preview reports the
    blind spot rather than zero hits, so it never disagrees with execute."""
    from workspaces import seal
    _seed_drafts(env)
    seal.seal_folder(env["folder"], passphrase="pw", log_root=env["log_root"])
    report = erasure.sweep(env["folder"], "Ada Lovelace",
                           log_root=env["log_root"])
    assert report.hits_by_kind["draft"] == []          # unknown, not claimed
    assert report.drafts_sealed == [report.folder_context]
    assert report.to_dict()["drafts_sealed"] == [report.folder_context]
    # an unsealed folder reports no blind spot
    seal.unseal_folder(env["folder"], passphrase="pw", log_root=env["log_root"])
    again = erasure.sweep(env["folder"], "Ada Lovelace",
                          log_root=env["log_root"])
    assert again.drafts_sealed == []
    assert {h.pair_id for h in again.hits_by_kind["draft"]} == {
        "draft:chat", "draft:policy_paste"}


def test_dry_run_leaves_drafts_untouched(env):
    _seed_drafts(env)
    before = D.load(env["folder"], "chat", log_root=env["log_root"])["payload"]
    report = erasure.execute(
        env["folder"], "Ada Lovelace", legal_basis="art_17_1_a",
        requester_ref="ticket-1", reason="test", dry_run=True,
        log_root=env["log_root"])
    assert report.dry_run and report.draft_surfaces_redacted == 0
    after = D.load(env["folder"], "chat", log_root=env["log_root"])["payload"]
    assert after == before


def test_execute_redacts_drafts_and_notes_it_on_the_tombstone(env):
    _seed_drafts(env)
    bad = D.draft_path(env["folder"], "cards", env["log_root"])
    bad.write_text("{not json", encoding="utf-8")

    report = erasure.execute(
        env["folder"], "Ada Lovelace", legal_basis="art_17_1_a",
        requester_ref="ticket-1", reason="test", log_root=env["log_root"])
    assert report.draft_surfaces_redacted == 2
    assert report.draft_surfaces_deleted == 1
    assert not bad.exists()

    chat = D.load(env["folder"], "chat", log_root=env["log_root"])["payload"]
    assert chat["transcript"][0]["text"] == "please erase [REDACTED]"
    untouched = D.load(env["folder"], "map", log_root=env["log_root"])["payload"]
    assert untouched == {"text": "no subject here"}

    # the folder's own drafts report in the manifest (keyed by resolved path)
    drafts_entry = report.cascade_manifest[report.sweep.folder_context]["drafts"]
    assert drafts_entry["deleted"] == ["cards"]
    assert set(drafts_entry["redacted"]) == {"chat", "policy_paste"}

    # composite tombstone carries the counts
    log = MutationLog(env["folder"], log_root=env["log_root"])
    composites = [e for e in log.replay()
                  if (e.extra or {}).get("kind") == "erasure_composite"]
    assert len(composites) == 1
    extra = composites[0].extra
    assert extra["draft_surfaces_redacted"] == 2
    assert extra["draft_surfaces_deleted"] == 1
    assert extra["hits_by_kind_count"]["draft"] == 3

    # a re-sweep finds nothing left in drafts
    again = erasure.sweep(env["folder"], "Ada Lovelace",
                          log_root=env["log_root"])
    assert again.hits_by_kind["draft"] == []


def test_execute_without_draft_hits_reports_zero(env):
    D.save(env["folder"], "map", {"text": "clean"}, log_root=env["log_root"])
    report = erasure.execute(
        env["folder"], "Ada Lovelace", legal_basis="art_17_1_a",
        requester_ref="ticket-1", reason="test", log_root=env["log_root"])
    assert report.draft_surfaces_redacted == 0
    assert report.draft_surfaces_deleted == 0
    assert D.load(env["folder"], "map",
                  log_root=env["log_root"])["payload"] == {"text": "clean"}
