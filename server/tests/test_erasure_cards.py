# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Erasure reach into saved card files: the sweep reports subject occurrences
in <folder>/cards/*.json (naming sealed folders as a blind spot), execute
rewrites matching fields and deletes identity-matching or unreadable files,
the composite tombstone carries the counts, and dry_run touches nothing."""
from __future__ import annotations

import pytest

from workspaces import card_store as C
from workspaces import erasure, seal
from workspaces.mutation_log import MutationLog, SealedWriteError
from workspaces.subject_card import SubjectCard


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from workspaces import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    folder = tmp_path / "ws"
    folder.mkdir()
    return {"folder": str(folder), "log_root": tmp_path / "logs"}


def _execute(env, subject, **kw):
    return erasure.execute(
        env["folder"], subject, legal_basis="art_17_1_a",
        requester_ref="ticket-1", reason="test",
        log_root=env["log_root"], **kw)


def _seed_cards(env):
    """Three cards: one carrying the subject in a field (rewritten on
    execute), one clean, one whose id is the subject (deleted whole)."""
    C.save_card(SubjectCard(domain="invoice", facets={"jurisdiction": "DE"},
                            notes="billing contact is Ada Lovelace",
                            subject_id="acme"),
                env["folder"], log_root=env["log_root"])
    C.save_card(SubjectCard(domain="invoice", facets={"tax_status": "vat"},
                            subject_id="clean-co"),
                env["folder"], log_root=env["log_root"])
    C.save_card(SubjectCard(domain="gdpr", description="data subject card",
                            subject_id="Ada Lovelace"),
                env["folder"], log_root=env["log_root"])


def test_sweep_reports_card_hits(env):
    _seed_cards(env)
    report = erasure.sweep(env["folder"], "Ada Lovelace",
                           log_root=env["log_root"])
    hits = report.hits_by_kind["card"]
    assert {h.pair_id for h in hits} == {"card-file:acme",
                                         "card-file:[REDACTED]"}
    assert all(h.kind == "card" for h in hits)
    # the subject never appears in a snippet or a displayed card id
    assert all("Ada" not in h.snippet and "Ada" not in h.pair_id for h in hits)
    # card hits are files, not chain pairs — the pair counts only carry the
    # identity card's own fact-intake chain event (subject_id in its extra)
    assert report.estimated_tombstone["affected_pair_count"] == 1
    assert report.estimated_tombstone["hits_by_kind_count"]["card"] == 2
    assert report.cards_sealed == []


def test_sweep_reports_unreadable_card_and_stale_scratch(env):
    d = C.card_path(env["folder"], "x").parent
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    (d / "crashed.json.tmp").write_text('{"notes": "Ada Lovelace"}',
                                        encoding="utf-8")
    report = erasure.sweep(env["folder"], "Ada Lovelace",
                           log_root=env["log_root"])
    hits = report.hits_by_kind["card"]
    assert {h.pair_id for h in hits} == {"card-file:broken",
                                         "card-file:crashed.json.tmp"}
    assert all("unreadable" in h.snippet for h in hits)


def test_sweep_names_sealed_cards_as_a_blind_spot(env):
    _seed_cards(env)
    seal.seal_folder(env["folder"], passphrase="pw",
                     log_root=env["log_root"])
    report = erasure.sweep(env["folder"], "Ada Lovelace",
                           log_root=env["log_root"])
    assert report.cards_sealed == [report.folder_context]
    assert report.hits_by_kind["card"] == []


def test_dry_run_leaves_cards_untouched(env):
    _seed_cards(env)
    before = C.load_card(env["folder"], "acme").notes
    report = _execute(env, "Ada Lovelace", dry_run=True)
    assert report.dry_run and report.card_files_redacted == 0
    assert C.load_card(env["folder"], "acme").notes == before
    assert C.card_path(env["folder"], "Ada Lovelace").exists()


def test_execute_redacts_cards_and_notes_it_on_the_tombstone(env):
    _seed_cards(env)
    bad = C.card_path(env["folder"], "broken")
    bad.write_text("{not json", encoding="utf-8")

    report = _execute(env, "Ada Lovelace")
    assert report.card_files_redacted == 1
    assert report.card_files_deleted == 2
    assert not C.card_path(env["folder"], "Ada Lovelace").exists()
    assert not bad.exists()
    assert C.list_cards(env["folder"]) == ["acme", "clean-co"]

    redone = C.load_card(env["folder"], "acme")
    assert redone.notes == "billing contact is [REDACTED]"
    assert redone.facets == {"jurisdiction": "DE"}     # clean fields kept
    untouched = C.load_card(env["folder"], "clean-co")
    assert untouched.facets == {"tax_status": "vat"}

    # card-file pseudo ids never enter the purge path or its records
    assert all(not p.startswith("card-file:") for p in report.purged_pairs)
    assert report.forgotten_subject_hash

    # the folder's cards report in the manifest, deleted ids redacted
    cards_entry = report.cascade_manifest[report.sweep.folder_context]["cards"]
    assert cards_entry["redacted"] == {"acme": 1}
    assert set(cards_entry["deleted"]) == {"[REDACTED]", "broken"}

    # composite tombstone carries the counts, and no card-file pseudo ids
    log = MutationLog(env["folder"], log_root=env["log_root"])
    composites = [e for e in log.replay()
                  if (e.extra or {}).get("kind") == "erasure_composite"]
    assert len(composites) == 1
    extra = composites[0].extra
    assert extra["card_files_redacted"] == 1
    assert extra["card_files_deleted"] == 2
    assert extra["hits_by_kind_count"]["card"] == 3
    assert all(p.startswith("pair-ref:")
               for p in extra["purged_pair_refs"])

    # a re-sweep finds nothing left in cards
    again = erasure.sweep(env["folder"], "Ada Lovelace",
                          log_root=env["log_root"])
    assert again.hits_by_kind["card"] == []


def test_execute_converges_when_subject_is_a_sentinel_substring(env):
    # "Ted" is a substring of "[REDACTED]" — after one execute, a re-sweep
    # must come back clean and a second execute must rewrite nothing.
    C.save_card(SubjectCard(domain="crm", notes="call Ted about renewal",
                            subject_id="renewals"),
                env["folder"], log_root=env["log_root"])
    first = _execute(env, "Ted")
    assert first.card_files_redacted == 1
    assert C.load_card(env["folder"], "renewals").notes == \
        "call [REDACTED] about renewal"
    again = erasure.sweep(env["folder"], "Ted", log_root=env["log_root"])
    assert again.hits_by_kind["card"] == []
    second = _execute(env, "Ted")
    assert second.card_files_redacted == 0
    assert C.load_card(env["folder"], "renewals").notes == \
        "call [REDACTED] about renewal"


def test_execute_on_sealed_workspace_destroys_nothing(env):
    _seed_cards(env)
    seal.seal_folder(env["folder"], passphrase="pw",
                     log_root=env["log_root"])
    with pytest.raises(SealedWriteError):
        _execute(env, "Ada Lovelace")
    # the refusal came before any file mutation
    assert C.card_path(env["folder"], "Ada Lovelace").exists()
    assert C.load_card(env["folder"], "acme").notes == \
        "billing contact is Ada Lovelace"


def test_short_subject_does_not_delete_unrelated_cards(env):
    # identity matching is word-delimited: erasing "Ada" deletes ada-corp
    # but must not delete nevada-holdings or touch its fields.
    C.save_card(SubjectCard(domain="invoice", notes="no subject here",
                            subject_id="nevada-holdings"),
                env["folder"], log_root=env["log_root"])
    C.save_card(SubjectCard(domain="invoice", subject_id="ada-corp"),
                env["folder"], log_root=env["log_root"])
    report = _execute(env, "Ada")
    assert report.card_files_deleted == 1
    assert C.list_cards(env["folder"]) == ["nevada-holdings"]
    kept = C.load_card(env["folder"], "nevada-holdings")
    assert kept.subject_id == "nevada-holdings"
    assert kept.notes == "no subject here"


def test_cascade_reaches_descendant_folder_cards(env):
    _seed_cards(env)
    child = env["folder"] + "/sub"
    C.save_card(SubjectCard(domain="crm", notes="child copy: Ada Lovelace",
                            subject_id="child-card"),
                child, log_root=env["log_root"])

    no_cascade = erasure.sweep(env["folder"], "Ada Lovelace",
                               log_root=env["log_root"])
    assert all(h.folder != str(env["folder"]) + "/sub"
               for h in no_cascade.hits_by_kind["card"])

    report = _execute(env, "Ada Lovelace", cascade=True)
    assert report.card_files_redacted == 2      # acme + child-card
    assert C.load_card(child, "child-card").notes == \
        "child copy: [REDACTED]"


def test_unicode_subject_survivor_regression(env):
    # length-changing lowercasing (U+0130) used to leave the subject in a
    # card reported redacted.
    C.save_card(SubjectCard(domain="crm",
                            notes="İİİİ Ada Lovelace",
                            subject_id="intl"),
                env["folder"], log_root=env["log_root"])
    report = _execute(env, "Ada Lovelace")
    assert report.card_files_redacted == 1
    assert C.load_card(env["folder"], "intl").notes == "İİİİ [REDACTED]"
    again = erasure.sweep(env["folder"], "Ada Lovelace",
                          log_root=env["log_root"])
    assert again.hits_by_kind["card"] == []


def test_status_surfaces_card_hits_for_a_request(env):
    _seed_cards(env)
    req = erasure.request(env["folder"], "Ada Lovelace",
                          requester_ref="ticket-1", reason="test",
                          log_root=env["log_root"])
    _execute(env, "Ada Lovelace", request_id=req["request_id"])
    manifest = erasure.status(env["folder"], req["request_id"],
                              log_root=env["log_root"])
    assert manifest["executed"]["hits_by_kind_count"]["card"] == 2


def test_execute_without_card_hits_reports_zero(env):
    C.save_card(SubjectCard(domain="invoice", facets={"tax_status": "vat"},
                            subject_id="clean-co"),
                env["folder"], log_root=env["log_root"])
    report = _execute(env, "Ada Lovelace")
    assert report.card_files_redacted == 0
    assert report.card_files_deleted == 0
    assert C.load_card(env["folder"], "clean-co").facets == \
        {"tax_status": "vat"}


def test_composite_folder_count_includes_file_only_folders(env):
    # A folder whose chain never mentions the subject but whose cards do:
    # the preview counts it, and the executed composite must agree.
    C.save_card(SubjectCard(domain="crm", notes="only here: Ada Lovelace",
                            subject_id="acme"),
                env["folder"], log_root=env["log_root"])
    preview = erasure.sweep(env["folder"], "Ada Lovelace",
                            log_root=env["log_root"])
    assert preview.estimated_tombstone["affected_folder_count"] == 1
    _execute(env, "Ada Lovelace")
    log = MutationLog(env["folder"], log_root=env["log_root"])
    extra = next(e.extra for e in log.replay()
                 if (e.extra or {}).get("kind") == "erasure_composite")
    assert extra["affected_folder_count"] == 1
