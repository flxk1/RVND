# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B5 (0.6.8): erasure as first-class verb + forgotten_subjects ledger.

Three-state model (D4):

  request → ERASURE_REQUESTED event; no purge.
  sweep   → dry-run preview; returns SweepReport.
  execute → sweep + per-pair purge + composite tombstone + ledger entry.

These tests document the contract end-to-end.
"""

from __future__ import annotations

import json

import pytest

from rvnd import erasure, forgotten_subjects
from rvnd.mutation_log import LogEvent, MutationLog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Per-test log root + key dir; both keypairs initialised so purges
    don't fail for lack of a controller key.
    """
    log_root = tmp_path / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    from rvnd import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    return {"log_root": log_root, "keydir": keydir, "workspace": workspace,
            "tmp_path": tmp_path}


def _seed_pair_with_text(workspace, log_root, *, pair_id, summary,
                          body="", channel="document", problem_type="case"):
    """Append an ingest event whose embedded pair carries the given text.

    Used as a tiny stand-in for the real extractor pipeline.
    """
    log = MutationLog(workspace, log_root=log_root)
    pair = {
        "id":      pair_id,
        "problem": {
            "id":      "sha256:problem-" + pair_id[-8:],
            "summary": summary,
            "type":    problem_type,
            "facets":  {},
        },
        "solution": {
            "id":             pair_id,
            "problem_id":     "sha256:problem-" + pair_id[-8:],
            "body":           body,
            "body_format":    "prose" if body else "metadata",
            "authority_tier": 5,
            "confidence":     0.5,
            "cited_sources":  [],
            "extractor_chain": ["test:seed"],
        },
    }
    log.append(LogEvent(
        event="ingest",
        folder_path=str(workspace),
        pair_id=pair_id,
        lifecycle_state="live",
        channel=channel,
        actor="test",
        extra={"pair": pair, "distribution_scope": "private"},
    ))
    return pair_id


# ---------------------------------------------------------------------------
# Sweep — text matching
# ---------------------------------------------------------------------------


def test_sweep_finds_pair_text_match(isolated_env):
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    _seed_pair_with_text(ws, lr, pair_id="sha256:hit",
                         summary="Notes about Jane Doe's contract",
                         body="The agreement with Jane Doe covers 2025.")
    _seed_pair_with_text(ws, lr, pair_id="sha256:miss",
                         summary="Unrelated note",
                         body="Q3 revenue overview.")

    report = erasure.sweep(str(ws), "Jane Doe", log_root=lr)
    assert report.total_hits() >= 1
    hits = report.hits_by_kind.get("pair", [])
    pair_ids = {h.pair_id for h in hits}
    assert "sha256:hit" in pair_ids
    assert "sha256:miss" not in pair_ids


def test_sweep_finds_capture_llm_match(isolated_env):
    """A captured LLM exchange whose pair carries the subject in its body."""
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    _seed_pair_with_text(
        ws, lr, pair_id="sha256:llm-1",
        summary="LLM exchange",
        body="Subject Jane Doe was discussed in the conversation.",
        channel="llm_answer",
        problem_type="llm_exchange",
    )
    report = erasure.sweep(str(ws), "Jane Doe", log_root=lr)
    hits = report.hits_by_kind.get("capture_llm", [])
    assert any(h.pair_id == "sha256:llm-1" for h in hits), (
        f"capture_llm hits: {report.hits_by_kind.get('capture_llm')}"
    )


def test_sweep_finds_capture_web_match(isolated_env):
    """A captured web search whose pair carries the subject in its body."""
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    _seed_pair_with_text(
        ws, lr, pair_id="sha256:web-1",
        summary="web search: Jane Doe",
        body="Result 1: Jane Doe biography ...",
        channel="websearch",
        problem_type="websearch",
    )
    report = erasure.sweep(str(ws), "Jane Doe", log_root=lr)
    hits = report.hits_by_kind.get("capture_web", [])
    assert any(h.pair_id == "sha256:web-1" for h in hits), (
        f"capture_web hits: {report.hits_by_kind.get('capture_web')}"
    )


def test_sweep_cascade_includes_descendants(isolated_env):
    """A descendant folder's hits should appear when cascade=True."""
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    child = ws / "child"
    child.mkdir(parents=True, exist_ok=True)
    _seed_pair_with_text(ws, lr, pair_id="sha256:root",
                         summary="Root note about Jane Doe", body="")
    _seed_pair_with_text(child, lr, pair_id="sha256:child",
                         summary="Child note about Jane Doe", body="")

    # Without cascade — only root.
    r_no = erasure.sweep(str(ws), "Jane Doe", cascade=False, log_root=lr)
    folders_no = set(r_no.hits_by_folder.keys())
    assert any("child" not in f for f in folders_no), folders_no
    assert all("child" not in f for f in folders_no), (
        f"child folder unexpectedly in non-cascade sweep: {folders_no}"
    )

    # With cascade — both.
    r_yes = erasure.sweep(str(ws), "Jane Doe", cascade=True, log_root=lr)
    folders_yes = set(r_yes.hits_by_folder.keys())
    assert any("child" in f for f in folders_yes), (
        f"cascade sweep missed child folder: {folders_yes}"
    )


# ---------------------------------------------------------------------------
# Dry-run / writes
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_writes(isolated_env):
    """Dry-run runs the sweep but does not append composite or purge events."""
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    _seed_pair_with_text(ws, lr, pair_id="sha256:dryrun",
                         summary="About Jane Doe", body="")
    log = MutationLog(ws, log_root=lr)
    before = list(log.replay())

    report = erasure.execute(
        str(ws), "Jane Doe",
        legal_basis="art_17_1_a",
        requester_ref="req:dry",
        reason="dry-run test",
        dry_run=True,
        log_root=lr,
    )
    assert report.dry_run is True
    assert report.composite_tombstone_id == ""
    assert report.purged_event_count == 0

    after = list(log.replay())
    assert len(after) == len(before), (
        f"dry-run wrote {len(after) - len(before)} unexpected events"
    )


def test_execute_writes_composite_tombstone(isolated_env):
    """execute appends an erasure_composite system event summarising the sweep."""
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    _seed_pair_with_text(ws, lr, pair_id="sha256:e1",
                         summary="Re: Jane Doe agreement", body="")
    _seed_pair_with_text(ws, lr, pair_id="sha256:e2",
                         summary="Followup on Jane Doe", body="")

    report = erasure.execute(
        str(ws), "Jane Doe",
        legal_basis="art_17_1_b",
        requester_ref="req:42",
        reason="consent withdrawn",
        log_root=lr,
    )
    assert report.composite_tombstone_id.startswith("erase-composite:")

    log = MutationLog(ws, log_root=lr)
    composites = [
        e for e in log.replay()
        if e.event == "system"
        and isinstance(e.extra, dict)
        and e.extra.get("kind") == "erasure_composite"
    ]
    assert len(composites) == 1
    c = composites[0]
    assert c.extra.get("legal_basis") == "art_17_1_b"
    assert c.extra.get("requester_ref") == "req:42"
    # Subject text must NOT appear in the audit chain.
    serialised = json.dumps(c.extra)
    assert "Jane Doe" not in serialised, (
        "subject text leaked into composite tombstone extra"
    )
    assert c.extra.get("subject_preview") == "[REDACTED]"


def test_leaky_reason_is_scrubbed_from_all_permanent_records(isolated_env):
    """The reason is free text; an operator who writes the subject into it
    must not defeat the erasure. Both the request event and everything
    execute writes (per-pair tombstones, composite) carry the scrubbed
    form."""
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    _seed_pair_with_text(ws, lr, pair_id="sha256:p1",
                         summary="Re: Jane Doe", body="")
    req = erasure.request(str(ws), "Jane Doe",
                          requester_ref="req:leak",
                          reason="Jane Doe asked us to forget her",
                          log_root=lr)
    erasure.execute(str(ws), "Jane Doe",
                    legal_basis="art_17_1_a", requester_ref="req:leak",
                    reason="erase Jane Doe per DSAR", log_root=lr,
                    request_id=req["request_id"])

    log = MutationLog(ws, log_root=lr)
    chain = log.log_file.read_text(encoding="utf-8").lower()
    assert "jane doe" not in chain
    composites = [e for e in log.replay()
                  if (e.extra or {}).get("kind") == "erasure_composite"]
    assert composites[0].extra["reason"] == "erase [REDACTED] per DSAR"


def test_execute_writes_individual_purges(isolated_env):
    """Each affected pair gets its own B1 purge tombstone (one per pair).
    Verified by counting `purge` events in the chain afterwards.
    """
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    _seed_pair_with_text(ws, lr, pair_id="sha256:p1",
                         summary="Re: Jane Doe A", body="")
    _seed_pair_with_text(ws, lr, pair_id="sha256:p2",
                         summary="Re: Jane Doe B", body="")

    erasure.execute(
        str(ws), "Jane Doe",
        legal_basis="art_17_1_a",
        requester_ref="req:p",
        reason="t",
        log_root=lr,
    )

    log = MutationLog(ws, log_root=lr)
    purges = [e for e in log.replay() if e.event == "purge"]
    # Tombstones name their pair through the opaque folder-salted ref,
    # never the raw id.
    refs = {e.pair_id for e in purges}
    assert forgotten_subjects.purged_pair_ref(log.folder_path, "sha256:p1") in refs
    assert forgotten_subjects.purged_pair_ref(log.folder_path, "sha256:p2") in refs
    assert "sha256:p1" not in refs and "sha256:p2" not in refs


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_execute_refuses_without_legal_basis(isolated_env):
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    _seed_pair_with_text(ws, lr, pair_id="sha256:x", summary="Jane Doe", body="")
    with pytest.raises(ValueError, match="legal_basis"):
        erasure.execute(
            str(ws), "Jane Doe",
            legal_basis="",
            requester_ref="r", reason="t",
            log_root=lr,
        )


def test_execute_single_key_without_controller(tmp_path, monkeypatch):
    """No controller key → execute proceeds in single-key mode (controller
    co-signature is opt-in). It must purge the matched pair, NOT auto-create
    the controller key, and keep the chain verifying."""
    keydir = tmp_path / "keys-no-controller"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    from rvnd import signing
    signing.ensure_keypair()
    assert signing.public_controller_key_fingerprint() is None

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    lr = tmp_path / "logs"
    _seed_pair_with_text(ws, lr, pair_id="sha256:x",
                         summary="About Jane Doe", body="")

    report = erasure.execute(
        str(ws), "Jane Doe",
        legal_basis="art_17_1_a",
        requester_ref="r",
        reason="t",
        log_root=lr,
    )
    assert report.purged_event_count >= 1
    # Controller key must NOT have been auto-created by a single-key erase.
    assert signing.public_controller_key_fingerprint() is None


# ---------------------------------------------------------------------------
# Forgotten-subjects ledger
# ---------------------------------------------------------------------------


def test_forgotten_subject_blocks_reingest_via_check(isolated_env):
    """After execute, forgotten_subjects.check() must return hash matches."""
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    _seed_pair_with_text(ws, lr, pair_id="sha256:f1",
                         summary="Re: Jane Doe", body="")

    erasure.execute(
        str(ws), "Jane Doe",
        legal_basis="art_17_1_a",
        requester_ref="r",
        reason="t",
        log_root=lr,
    )

    # Direct check.
    hits = forgotten_subjects.check(str(ws), "Jane Doe")
    assert len(hits) == 1, f"expected 1 forgotten-subject hit, got {hits}"

    # Empty / unrelated text → no hits.
    assert forgotten_subjects.check(str(ws), "") == []
    assert forgotten_subjects.check(str(ws), "John Smith") == []

    # check_text alias works the same.
    assert forgotten_subjects.check_text(str(ws), "Jane Doe") == hits


# ---------------------------------------------------------------------------
# Two-phase intake
# ---------------------------------------------------------------------------


def test_erase_request_writes_erasure_requested_event(isolated_env):
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]

    res = erasure.request(
        str(ws), "Jane Doe",
        requester_ref="req:intake",
        reason="DSAR ticket #42",
        log_root=lr,
    )
    assert res["request_id"].startswith("erase-req:")
    assert res["audit_id"]
    assert res["folder"] == str(ws.resolve())

    log = MutationLog(ws, log_root=lr)
    intake = [
        e for e in log.replay()
        if e.event == "system"
        and isinstance(e.extra, dict)
        and e.extra.get("kind") == "ERASURE_REQUESTED"
    ]
    assert len(intake) == 1
    e = intake[0]
    assert e.extra.get("request_id") == res["request_id"]
    assert e.extra.get("requester_ref") == "req:intake"
    assert e.extra.get("subject_preview") == "[REDACTED]"
    # Subject text never lands on chain.
    assert "Jane Doe" not in json.dumps(e.extra)


def test_erase_status_returns_cascade_manifest(isolated_env):
    """A full request → execute round-trip should be visible in status."""
    ws = isolated_env["workspace"]
    lr = isolated_env["log_root"]
    _seed_pair_with_text(ws, lr, pair_id="sha256:s1",
                         summary="Re: Jane Doe target", body="")

    req = erasure.request(
        str(ws), "Jane Doe",
        requester_ref="req:full",
        reason="DSAR",
        log_root=lr,
    )
    rid = req["request_id"]

    erasure.execute(
        str(ws), "Jane Doe",
        legal_basis="art_17_1_b",
        requester_ref="req:full",
        reason="DSAR",
        log_root=lr,
        request_id=rid,
    )

    manifest = erasure.status(str(ws), rid, log_root=lr)
    assert manifest["request_id"] == rid
    assert manifest["requested"] is not None
    assert manifest["executed"] is not None
    assert manifest["executed"]["purged_pair_count"] >= 1
    # At least one per-pair purge with this erasure_request_id.
    assert len(manifest["purges"]) >= 1
    # The forgotten-subject breadcrumb is recorded.
    assert manifest["forgotten"] is not None
    assert manifest["forgotten"]["subject_hash"]
