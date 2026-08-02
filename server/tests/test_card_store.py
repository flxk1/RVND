# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Where typed facts land: the SubjectCard, persisted to the folder + signed log,
and reloaded so the next run never re-asks them."""

from __future__ import annotations

import json

from workspaces.subject_card import SubjectCard
from workspaces.fact_intake import FactNeed, build_form, record_standing
from workspaces import card_store as cs


NEEDS = [
    FactNeed("tax_status", "VAT status?", scope="standing"),
    FactNeed("jurisdiction", "Jurisdiction?", scope="standing"),
    FactNeed("line_items", "Line items?", scope="per_case"),
]


def test_standing_answers_persist_to_folder_and_are_reloadable(tmp_path):
    folder = tmp_path / "licensee-acme"
    log_root = tmp_path / ".log"

    # Run 1 — user types into the form; standing facts recorded onto a card.
    answers = {"tax_status": "reverse-charge", "jurisdiction": "DE", "line_items": "x"}
    facets = record_standing(NEEDS, answers, standing={})        # per-case NOT included
    card = SubjectCard(domain="invoice", facets=facets, subject_id="acme")
    res = cs.save_card(card, folder, log_root=log_root, facets_written=list(facets))

    # it is on disk, and the write left a signed audit id
    assert cs.card_path(folder, "acme").exists()
    assert res["audit_id"]
    assert cs.list_cards(folder) == ["acme"]

    # Run 2 — reload the entity; its standing facts come back, so the form is empty
    reloaded = cs.load_card(folder, "acme")
    assert reloaded.facets == {"tax_status": "reverse-charge", "jurisdiction": "DE"}
    form = build_form(NEEDS, standing=reloaded.facets, per_case_data={"line_items": "y"})
    assert form.complete and form.questions == []


def test_per_case_answer_is_not_persisted_to_the_card(tmp_path):
    folder = tmp_path / "f"
    facets = record_standing(NEEDS, {"tax_status": "x", "line_items": "SECRET"}, standing={})
    cs.save_card(SubjectCard(domain="invoice", facets=facets, subject_id="e1"),
                 folder, log_root=tmp_path / ".log")
    reloaded = cs.load_card(folder, "e1")
    assert "line_items" not in reloaded.facets        # per-case never stored on the entity


def test_first_time_entity_has_no_card(tmp_path):
    assert cs.load_card(tmp_path / "nobody", "x") is None


def test_each_write_appends_a_new_audit_event(tmp_path):
    folder = tmp_path / "f"; log_root = tmp_path / ".log"
    c = SubjectCard(domain="invoice", facets={"tax_status": "a"}, subject_id="e2")
    a1 = cs.save_card(c, folder, log_root=log_root)["audit_id"]
    c.facets["jurisdiction"] = "DE"
    a2 = cs.save_card(c, folder, log_root=log_root)["audit_id"]
    assert a1 and a2 and a1 != a2                      # every change is a distinct logged event


# ---------------------------------------------------------------------------
# Erasure hooks: scan previews what redact does; identity is word-delimited;
# rewrites keep unknown keys and never touch the subject_id field.
# ---------------------------------------------------------------------------


def test_scan_and_redact_reach_facets_attachments_and_keys(tmp_path):
    folder = tmp_path / "f"
    cs.save_card(SubjectCard(domain="crm",
                             facets={"contact": "Ada Lovelace",
                                     "Ada Lovelace": "key-carried"},
                             attachments=["specs/ada lovelace.pdf"],
                             subject_id="acme"),
                 folder, log_root=tmp_path / ".log")
    scan = cs.scan(folder, "Ada Lovelace")
    assert scan["hits"] == {"acme": 3}
    res = cs.redact(folder, "Ada Lovelace")
    assert res == {"ok": True, "redacted": {"acme": 3}, "deleted": []}
    reloaded = cs.load_card(folder, "acme")
    assert reloaded.facets == {"contact": "[REDACTED]",
                               "[REDACTED]": "key-carried"}
    assert reloaded.attachments == ["specs/[REDACTED].pdf"]
    assert cs.scan(folder, "Ada Lovelace")["hits"] == {}


def test_redact_preserves_unknown_keys_and_subject_id(tmp_path):
    folder = tmp_path / "f"
    path = cs.card_path(folder, "nevada-holdings")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"subject_id": "nevada-holdings", "keep": 42, '
                    '"future_field": "Ada Lovelace lives here"}',
                    encoding="utf-8")
    res = cs.redact(folder, "Ada Lovelace")
    assert res["redacted"] == {"nevada-holdings": 1}
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["keep"] == 42
    assert raw["future_field"] == "[REDACTED] lives here"
    # the id field mirrors the file name: never substring-rewritten
    assert raw["subject_id"] == "nevada-holdings"


def test_identity_match_is_word_delimited(tmp_path):
    folder = tmp_path / "f"
    for sid in ("nevada-holdings", "ada-corp"):
        cs.save_card(SubjectCard(domain="crm", subject_id=sid),
                     folder, log_root=tmp_path / ".log")
    scan = cs.scan(folder, "ada")
    assert scan["identity"] == ["ada-corp"]
    res = cs.redact(folder, "ada")
    assert res["deleted"] == ["ada-corp"]
    assert cs.list_cards(folder) == ["nevada-holdings"]


def test_stale_rewrite_scratch_is_scanned_and_removed(tmp_path):
    folder = tmp_path / "f"
    d = folder / "cards"
    d.mkdir(parents=True)
    (d / "victim.json.tmp").write_text('{"notes": "Ada Lovelace"}',
                                       encoding="utf-8")
    assert cs.scan(folder, "Ada Lovelace")["unreadable"] == \
        ["victim.json.tmp"]
    res = cs.redact(folder, "Ada Lovelace")
    assert res["deleted"] == ["victim.json.tmp"]
    assert not (d / "victim.json.tmp").exists()


def test_card_pair_id_is_opaque_on_chain(tmp_path):
    from workspaces.mutation_log import MutationLog
    import re
    folder = tmp_path / "f"; log_root = tmp_path / ".log"
    cs.save_card(SubjectCard(domain="crm", facets={"tax_status": "a"},
                             subject_id="Anna Schmidt"),
                 folder, log_root=log_root)
    (evt,) = [e for e in MutationLog(folder, log_root=log_root).replay()
              if e.event == "ingest"]
    assert re.fullmatch(r"card:[0-9a-f]{16}", evt.pair_id)
    assert "anna" not in evt.pair_id.lower()
    # the erasure sweep still finds the event: plaintext id lives in extra,
    # which purge removes whole — the pair id is what tombstones keep.
    assert evt.extra["subject_id"] == "Anna Schmidt"


def test_card_pair_ref_stable_per_folder_salted_across_folders(tmp_path):
    from workspaces.mutation_log import MutationLog
    log_root = tmp_path / ".log"
    card = SubjectCard(domain="crm", facets={}, subject_id="Anna Schmidt")
    f1, f2 = tmp_path / "f1", tmp_path / "f2"
    cs.save_card(card, f1, log_root=log_root)
    cs.save_card(card, f1, log_root=log_root)
    cs.save_card(card, f2, log_root=log_root)
    p1 = {e.pair_id for e in MutationLog(f1, log_root=log_root).replay()
          if e.event == "ingest"}
    p2 = {e.pair_id for e in MutationLog(f2, log_root=log_root).replay()
          if e.event == "ingest"}
    assert len(p1) == 1          # deterministic: both saves share one lineage
    assert p1 != p2              # folder-salted: no cross-folder linkage


def test_card_pair_ref_domain_separated_from_forgotten_hash(tmp_path):
    from workspaces import forgotten_subjects as fs
    from workspaces.mutation_log import MutationLog
    folder = tmp_path / "f"; log_root = tmp_path / ".log"
    # lowercase subject == its forgotten normalisation: same salt, same text,
    # only the domain label differs — the refs must still be unrelated.
    cs.save_card(SubjectCard(domain="crm", facets={}, subject_id="anna schmidt"),
                 folder, log_root=log_root)
    h = fs.add(folder, "anna schmidt", request_id="r1")
    (evt,) = [e for e in MutationLog(folder, log_root=log_root).replay()
              if e.event == "ingest"]
    ref = evt.pair_id.split(":", 1)[1]
    assert not h.startswith(ref)
