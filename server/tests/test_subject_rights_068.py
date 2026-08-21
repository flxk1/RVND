# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-16: subject-rights + retention posture, pinned.

Backs docs/concepts/data-retention-and-subject-rights.md. RVND has no
time-based retention job by design (the audit chain is append-only and
tamper-evident); storage-limitation is enforced by targeted erasure, and the
erasure sweep doubles as the Art. 15 "what do you hold about me" access index.

These tests pin exactly those claims so the documented posture stays true:

  * the sweep finds a subject's data completely (access completeness);
  * it names its sealed blind spots instead of reporting them clean
    (access honesty);
  * erasure is the effective retention mechanism — after it, the data is gone
    and re-ingest is blocked (storage-limitation actually holds).
"""
from __future__ import annotations

import pytest

from rvnd import erasure, forgotten_subjects, seal
from rvnd.mutation_log import LogEvent, MutationLog

pytestmark = pytest.mark.security  # privacy / subject-rights integrity


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    ws = tmp_path / "ws"
    ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def _seed_pair(ws, lr, *, pair_id, summary):
    log = MutationLog(ws, log_root=lr)
    pair = {
        "id": pair_id,
        "problem": {"id": "sha256:p-" + pair_id[-6:], "summary": summary,
                    "type": "case", "facets": {}},
        "solution": {"id": pair_id, "problem_id": "sha256:p-" + pair_id[-6:],
                     "body": summary, "body_format": "prose",
                     "authority_tier": 5, "confidence": 0.5,
                     "cited_sources": [], "extractor_chain": ["test:seed"]},
    }
    log.append(LogEvent(event="ingest", folder_path=ws, pair_id=pair_id,
                        lifecycle_state="live", channel="document", actor="test",
                        extra={"pair": pair, "distribution_scope": "private"}))


def test_sweep_is_a_complete_subject_access_index(env):
    """Art. 15(1): the sweep must find EVERY item referencing the subject, so
    'what do you hold about me' is answerable — and its machine-readable form
    round-trips."""
    subject = "Ingrid Vogel"
    for i in range(3):
        _seed_pair(env["ws"], env["lr"], pair_id=f"sha256:pair-{i}",
                   summary=f"{subject} appears in matter {i}.")
    # A pair NOT about the subject must not appear in the index.
    _seed_pair(env["ws"], env["lr"], pair_id="sha256:other",
               summary="Unrelated party signed elsewhere.")

    report = erasure.sweep(env["ws"], subject, log_root=env["lr"])
    assert report.total_hits() == 3, (
        f"access index incomplete: found {report.total_hits()} of 3 items")
    d = report.to_dict()
    assert d["subject"] == subject and d["total_hits"] == 3
    assert "hits_by_kind" in d, "access report is not machine-readable"


def test_access_index_names_its_sealed_blind_spots(env):
    """Access honesty: a sealed folder's drafts/cards cannot be inspected, so
    the report must NAME the sealed blind spot, never report it as clean."""
    subject = "Ingrid Vogel"
    _seed_pair(env["ws"], env["lr"], pair_id="sha256:pair-0",
               summary=f"{subject} in the record.")
    # Seal the folder so draft/card inspection is blinded.
    seal.seal_folder(env["ws"], passphrase="pw-123", log_root=env["lr"])

    report = erasure.sweep(env["ws"], subject, log_root=env["lr"])
    blind = set(report.drafts_sealed) | set(report.cards_sealed)
    assert blind, (
        "sweep reported a sealed folder as fully inspected — the access index "
        "must name what it could not see")


def test_erasure_is_the_effective_retention_mechanism(env):
    """Storage-limitation holds via erasure: after erasing the subject, a
    re-sweep finds nothing AND re-ingest is blocked by the forgotten ledger."""
    subject = "Ingrid Vogel"
    _seed_pair(env["ws"], env["lr"], pair_id="sha256:pair-0",
               summary=f"{subject} in the record.")
    assert erasure.sweep(env["ws"], subject, log_root=env["lr"]).total_hits() == 1

    erasure.execute(env["ws"], subject, legal_basis="art_17_1_a",
                    requester_ref="dsar-1", reason="retention period lapsed",
                    log_root=env["lr"])

    # Data is gone from the index.
    assert erasure.sweep(env["ws"], subject, log_root=env["lr"]).total_hits() == 0, (
        "erasure did not remove the subject's data — retention not enforced")
    # And re-collection is blocked (durable, not point-in-time).
    assert forgotten_subjects.contains(env["ws"], subject), (
        "erased subject is not on the forgotten ledger — re-ingest would slip in")
