# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B1 (0.6.8): purge tombstone + chain re-link + cross-process file lock.

The 0.6.8 audit-chain hardening replaces the raw ``purge()`` that quietly
rewrote the log and broke ``verify_chain()`` with:

  - controller-co-signed ``purge`` tombstone events on-chain;
  - re-linked + re-signed ``prev_hash`` for every event whose predecessor
    was purged;
  - ``ChainVerificationResult.purged_with_tombstone`` to distinguish
    authorised erasure from tampering;
  - ``fcntl.flock`` advisory locks around append() + purge() + verify_chain()
    so concurrent writers can no longer fork the chain.

These tests document the contract end-to-end.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest

from workspaces.mutation_log import (
    MutationLog,
    VALID_LEGAL_BASES,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_keys(tmp_path, monkeypatch):
    """Bind WORKSPACE_KEY_DIR to a per-test temp dir AND initialise both
    keypairs so purge() finds the controller key it requires."""
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    from workspaces import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    return keydir


def _build_log_with_events(workspace: Path, log_root: Path,
                           *, n_pair_a: int = 3, n_pair_b: int = 2) -> MutationLog:
    """Append a small mix of events for two pairs, in interleaved order."""
    log = MutationLog(workspace, log_root=log_root)
    for i in range(max(n_pair_a, n_pair_b)):
        if i < n_pair_a:
            log.append_raw(event="ingest",
                            pair_id="sha256:pair-A",
                            lifecycle_state="ingested",
                            actor=f"writer-{i}")
        if i < n_pair_b:
            log.append_raw(event="ingest",
                            pair_id="sha256:pair-B",
                            lifecycle_state="ingested",
                            actor=f"writer-{i}")
    return log


# ---------------------------------------------------------------------------
# Tombstone shape + chain re-link
# ---------------------------------------------------------------------------


def test_purge_writes_tombstone_with_required_fields(tmp_path, isolated_keys):
    log = _build_log_with_events(tmp_path / "ws", tmp_path / "logs")
    n = log.purge(
        "sha256:pair-A",
        legal_basis="art_17_1_b",
        requester_ref="req:42",
        reason="subject withdrew consent",
    )
    assert n == 3

    events = list(log.replay())
    tombstones = [e for e in events if e.event == "purge"]
    assert len(tombstones) == 1
    t = tombstones[0]
    # The tombstone outlives the purged events: it names the pair through
    # the opaque folder-salted ref, never the raw id.
    from workspaces.forgotten_subjects import purged_pair_ref
    assert t.pair_id == purged_pair_ref(log.folder_path, "sha256:pair-A")
    assert "sha256:pair-A" not in t.pair_id
    assert t.lifecycle_state == "purged"
    extra = t.extra
    assert extra["kind"] == "purge_tombstone"
    assert extra["legal_basis"] == "art_17_1_b"
    assert extra["requester_ref"] == "req:42"
    assert extra["reason"] == "subject withdrew consent"
    assert extra["purged_event_count"] == 3
    assert len(extra["purged_event_audit_ids"]) == 3
    # Both signatures must be present.
    assert t.signature, "tombstone must carry an operator signature"
    assert extra.get("controller_sig"), "tombstone must carry a controller signature"
    assert extra.get("operator_keyid"), "tombstone must record the operator key fingerprint"
    assert extra.get("controller_keyid"), "tombstone must record the controller key fingerprint"


def test_purge_re_links_subsequent_events(tmp_path, isolated_keys):
    """Survivors whose predecessor was purged must get a fresh prev_hash."""
    log = _build_log_with_events(tmp_path / "ws", tmp_path / "logs",
                                 n_pair_a=2, n_pair_b=3)
    # Capture predecessor hashes BEFORE purge.
    before = [json.loads(l) for l in log.log_file.read_text().splitlines() if l.strip()]
    # The events for pair-B that come AFTER pair-A entries will need a re-link
    # because their predecessor (a pair-A event) will be removed.
    pair_a_count_before = sum(1 for e in before if e.get("pair_id") == "sha256:pair-A")
    assert pair_a_count_before == 2

    log.purge(
        "sha256:pair-A",
        legal_basis="art_17_1_c",
        requester_ref="req:re-link",
        reason="re-link test",
    )

    after = [json.loads(l) for l in log.log_file.read_text().splitlines() if l.strip()]
    # Every surviving event must still have a valid hash chain.
    result = log.verify_chain()
    assert result.ok, (
        f"chain failed post-purge: broken={result.broken_links} "
        f"sig_fail={result.signature_failures}"
    )


def test_verify_chain_passes_post_purge(tmp_path, isolated_keys):
    log = _build_log_with_events(tmp_path / "ws", tmp_path / "logs")
    log.purge(
        "sha256:pair-A",
        legal_basis="art_17_1_a",
        requester_ref="req:1",
        reason="no longer needed",
    )
    result = log.verify_chain()
    assert result.ok
    assert result.broken_links == []
    assert result.signature_failures == []


def test_verify_chain_distinguishes_purge_from_tampering(tmp_path, isolated_keys):
    """Without a tombstone, a prev_hash break must show as broken_links."""
    log = MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="live")
    log.append_raw(event="ingest", pair_id="sha256:p2", lifecycle_state="live")
    log.append_raw(event="ingest", pair_id="sha256:p3", lifecycle_state="live")

    # Corrupt the middle event's prev_hash WITHOUT writing a tombstone —
    # this simulates raw tampering.
    raw_lines = log.log_file.read_text().splitlines()
    middle = json.loads(raw_lines[1])
    middle["prev_hash"] = "deadbeef" * 8  # bogus hex
    raw_lines[1] = json.dumps(middle)
    log.log_file.write_text("\n".join(raw_lines) + "\n")

    result = log.verify_chain()
    assert not result.ok
    assert len(result.broken_links) >= 1
    assert result.purged_with_tombstone == 0


def test_purge_refuses_without_legal_basis(tmp_path, isolated_keys):
    log = MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="live")
    with pytest.raises(ValueError, match="legal_basis"):
        log.purge("sha256:p1", legal_basis="",
                  requester_ref="r", reason="x")


def test_purge_refuses_invalid_legal_basis_value(tmp_path, isolated_keys):
    log = MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="live")
    with pytest.raises(ValueError, match="unknown legal_basis"):
        log.purge("sha256:p1",
                  legal_basis="art_99_bogus",
                  requester_ref="r", reason="x")


def test_purge_single_key_without_controller(tmp_path, monkeypatch):
    """No controller key → purge proceeds in SINGLE-KEY mode (controller
    co-signature is opt-in, not mandatory). It must NOT auto-create the
    controller key, and the tombstone must record erasure_mode=single-key."""
    keydir = tmp_path / "keys-no-controller"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    from workspaces import signing
    # Identity (operator) key is fine; only the CONTROLLER key is missing.
    signing.ensure_keypair()
    assert signing.public_controller_key_fingerprint() is None

    log = MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id="sha256:p1", lifecycle_state="live")

    purged = log.purge("sha256:p1",
                       legal_basis="art_17_1_a",
                       requester_ref="r",
                       reason="x")
    assert purged == 1

    # The controller key must NOT have been auto-created.
    assert signing.public_controller_key_fingerprint() is None

    # The tombstone records single-key authorisation, and the chain verifies.
    result = log.verify_chain()
    assert result.ok
    assert result.purged_with_tombstone >= 1
    events = [json.loads(l) for l in log._log_file.read_text().splitlines() if l.strip()]
    tomb = next(e for e in events if e.get("extra", {}).get("kind") == "purge_tombstone")
    assert tomb["extra"]["erasure_mode"] == "single-key"
    assert tomb["extra"]["controller_keyid"] is None


# ---------------------------------------------------------------------------
# Cross-process concurrency — fcntl.flock should prevent corruption
# ---------------------------------------------------------------------------


def _append_in_subprocess(workspace_str: str, log_root_str: str,
                          n_events: int, label: str) -> None:
    from workspaces.mutation_log import LogEvent, MutationLog
    log = MutationLog(Path(workspace_str), log_root=Path(log_root_str))
    for i in range(n_events):
        log.append(LogEvent(
            event="ingest",
            folder_path=workspace_str,
            pair_id=f"sha256:{label}:{i:04d}",
            channel="document",
            actor=f"writer:{label}",
            extra={"i": i, "pid": os.getpid()},
        ))


def _purge_in_subprocess(workspace_str: str, log_root_str: str,
                          keydir_str: str, pair_id: str) -> None:
    os.environ["WORKSPACE_KEY_DIR"] = keydir_str
    from workspaces import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    from workspaces.mutation_log import MutationLog
    log = MutationLog(Path(workspace_str), log_root=Path(log_root_str))
    log.append_raw(event="ingest",
                    pair_id=pair_id,
                    lifecycle_state="live",
                    actor="seeder-then-purger")
    log.purge(pair_id,
              legal_basis="art_17_1_a",
              requester_ref=f"concurrent-{os.getpid()}",
              reason="concurrent purge stress")


def test_concurrent_purge_and_append_does_not_corrupt(tmp_path, isolated_keys):
    """Spawn a long-running appender + an interleaved purger and verify the
    chain stays consistent. With the 0.6.8 flock around purge() + append()
    no fork should appear."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    log_root = tmp_path / "logs"
    log_root.mkdir(parents=True)
    keydir = tmp_path / "keys"

    ctx = mp.get_context("spawn")
    p_writer = ctx.Process(
        target=_append_in_subprocess,
        args=(str(workspace), str(log_root), 80, "W"),
    )
    p_purger = ctx.Process(
        target=_purge_in_subprocess,
        args=(str(workspace), str(log_root), str(keydir), "sha256:ephemeral"),
    )

    p_writer.start()
    time.sleep(0.05)  # let the writer get going
    p_purger.start()
    p_writer.join(timeout=120)
    p_purger.join(timeout=120)
    assert p_writer.exitcode == 0
    assert p_purger.exitcode == 0

    log = MutationLog(workspace, log_root=log_root)
    result = log.verify_chain()
    # Tombstones from the purger are authorised re-links, not broken_links.
    assert result.broken_links == [], (
        f"chain corrupted under concurrent purge+append: "
        f"broken={result.broken_links[:3]}"
    )
    # No two events should claim the same predecessor — that would be a fork.
    lines = [json.loads(l) for l in log.log_file.read_text().splitlines() if l.strip()]
    prev_hashes = [l.get("prev_hash") for l in lines if l.get("prev_hash")]
    seen: dict[str, int] = {}
    for ph in prev_hashes:
        seen[ph] = seen.get(ph, 0) + 1
    duplicates = {ph: c for ph, c in seen.items() if c > 1}
    assert duplicates == {}, f"chain forked: duplicate prev_hashes {duplicates}"


# ---------------------------------------------------------------------------
# Sanity: all the documented GDPR grounds are accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("basis", sorted(VALID_LEGAL_BASES))
def test_each_legal_basis_is_accepted(tmp_path, isolated_keys, basis):
    log = MutationLog(tmp_path / f"ws-{basis}", log_root=tmp_path / "logs")
    log.append_raw(event="ingest", pair_id=f"sha256:{basis}", lifecycle_state="live")
    n = log.purge(f"sha256:{basis}",
                  legal_basis=basis,
                  requester_ref="r",
                  reason=f"test-{basis}")
    assert n == 1
