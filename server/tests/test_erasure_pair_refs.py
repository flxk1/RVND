# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Opaque pair refs in erasure records — the name must not survive its own
erasure.

Everything a purge leaves on the permanent chain (the tombstone, the
erasure tracker, the composite) names the purged pair only through
``forgotten_subjects.purged_pair_ref``. The raw pair id may carry the very
subject being erased — a legacy ``card:<person's name>`` mint — and before
this contract the erasure re-engraved it verbatim in all three places.
``erasure.status`` stitches tombstones to trackers by equality of the ref;
legacy chains (raw id on both sides) stitch the same way.
"""

from __future__ import annotations

import pytest

from rvnd import card_store, erasure, forgotten_subjects
from rvnd.mutation_log import LogEvent, MutationLog
from rvnd.subject_card import SubjectCard


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    log_root = tmp_path / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    from rvnd import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    return {"log_root": log_root, "workspace": workspace}


def _seed_legacy_card_event(workspace, log_root, subject_id):
    """An ingest event as pre-opaque-mint save_card wrote it: the subject
    verbatim in the pair id AND in extra."""
    log = MutationLog(workspace, log_root=log_root)
    log.append(LogEvent(
        event="ingest", folder_path=str(workspace),
        pair_id=f"card:{subject_id}", channel="fact", actor="user",
        extra={"kind": "fact-intake", "subject_id": subject_id,
               "domain": "crm", "facets_written": []}))


def test_erasure_leaves_no_subject_on_chain_even_for_legacy_pairs(isolated_env):
    ws, lr = isolated_env["workspace"], isolated_env["log_root"]
    _seed_legacy_card_event(ws, lr, "Anna Schmidt")
    card_store.save_card(
        SubjectCard(domain="crm", facets={"note": "Anna Schmidt owes 5 EUR"},
                    subject_id="Anna Schmidt"),
        ws, log_root=lr)

    req = erasure.request(str(ws), "Anna Schmidt",
                          requester_ref="req:rt", reason="DSAR",
                          log_root=lr)
    report = erasure.execute(
        str(ws), "Anna Schmidt",
        legal_basis="art_17_1_a", requester_ref="req:rt", reason="DSAR",
        log_root=lr, request_id=req["request_id"])
    assert report.purged_event_count >= 2

    # The load-bearing assertion: after the erasure, the raw chain carries
    # the subject nowhere — not in the tombstones, not in the tracker pair
    # ids, not in the composite.
    chain_text = MutationLog(ws, log_root=lr).log_file.read_text(
        encoding="utf-8").lower()
    assert "anna" not in chain_text
    assert "schmidt" not in chain_text

    # status() still stitches the request to its per-pair purges through
    # the shared opaque ref.
    manifest = erasure.status(str(ws), req["request_id"], log_root=lr)
    assert manifest["requested"] is not None
    assert manifest["executed"] is not None
    assert len(manifest["purges"]) == 2
    assert all(p["pair_id"].startswith("pair-ref:")
               for p in manifest["purges"])
    log = MutationLog(ws, log_root=lr)
    assert {p["pair_id"] for p in manifest["purges"]} == {
        forgotten_subjects.purged_pair_ref(log.folder_path, "card:Anna Schmidt"),
        forgotten_subjects.purged_pair_ref(
            log.folder_path,
            "card:" + forgotten_subjects.opaque_ref(
                log.folder_path, "Anna Schmidt", domain="card-ref")[:16]),
    }


def test_status_stitches_legacy_raw_id_chains(isolated_env):
    """Chains written before the opaque-ref contract carry the raw id on
    both sides (tracker extra.purged_pair_id, tombstone pair_id); the
    fallback keeps stitching them."""
    ws, lr = isolated_env["workspace"], isolated_env["log_root"]
    rid = "erase-req:legacy0001"
    log = MutationLog(ws, log_root=lr)
    log.append(LogEvent(
        event="system", folder_path=str(ws),
        pair_id=f"erasure-track:{rid}:sha256:old", channel="system",
        actor="erasure:user",
        extra={"kind": "erasure_pair_purge_start", "request_id": rid,
               "erasure_request_id": rid, "purged_pair_id": "sha256:old",
               "subject_preview": "[REDACTED]"}))
    log.append_raw(
        event="purge", pair_id="sha256:old", lifecycle_state="purged",
        channel="system", actor="system:purge",
        extra={"kind": "purge_tombstone", "purged_event_count": 1,
               "legal_basis": "art_17_1_a"})

    manifest = erasure.status(str(ws), rid, log_root=lr)
    assert len(manifest["purges"]) == 1
    assert manifest["purges"][0]["pair_id"] == "sha256:old"
