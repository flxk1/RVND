# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the Ed25519 signing layer on the mutation log (0.6.6+).

Layered on top of the SHA-256 hash chain. The chain catches casual tampering
(modify or delete without recomputing). Ed25519 closes the chain-rewrite gap:
an adversary with filesystem write access can no longer forge events even if
they recompute downstream prev_hash values, because they don't have the
private key.

Tests cover: keypair generation, signing on append, verification on replay,
chain-rewrite detection (the threat hash-chain alone misses), backward compat
with unsigned events, signature-payload binding to chain position.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _default_chain_profile(monkeypatch):
    """This module tests DEFAULT chain semantics (pinning off, legacy
    tolerance, advisory divergence). Clear the opt-in protections in case the
    hardened profile (RVND_TEST_HARDENED=1) enabled them suite-wide; tests
    that want a protection ON set it explicitly in their own body, which runs
    after this fixture and wins."""
    for var in ("WORKSPACE_KEY_PINNING", "WORKSPACE_STRICT_KEY_PINNING",
                "WORKSPACE_STRICT_HOST_DIVERGENCE"):
        monkeypatch.delenv(var, raising=False)

from rvnd.mutation_log import (
    GENESIS_HASH,
    LogEvent,
    MutationLog,
    _canonical_event_hash,
    _signed_bytes,
)


@pytest.fixture(autouse=True)
def isolated_keydir(tmp_path, monkeypatch):
    """Each test gets a fresh keypair location so they don't share state."""
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    return keydir


def _make_event(folder: Path, i: int) -> LogEvent:
    return LogEvent(
        event="ingest",
        folder_path=str(folder),
        pair_id=f"pair-{i}",
        actor="test",
        extra={"i": i},
    )


def test_keypair_generated_on_first_use(isolated_keydir, tmp_path):
    """First append() to a fresh log generates the identity keypair.

    0.6.8 B4: identity keys live at ``<keydir>/<host_id>/identity.{priv,pub}``,
    not flat at the keydir root. The migration helper handles legacy installs.
    """
    from rvnd.signing import _host_id
    assert not isolated_keydir.exists()
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    log.append(_make_event(tmp_path / "work", 0))
    assert isolated_keydir.exists()
    host_subdir = isolated_keydir / _host_id()
    assert (host_subdir / "identity.priv").exists()
    assert (host_subdir / "identity.pub").exists()


def test_event_carries_signature(tmp_path):
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    log.append(_make_event(tmp_path / "work", 0))
    log_file = tmp_path / ".workspaces" / log.folder_id / "events.jsonl"
    obj = json.loads(log_file.read_text().strip())
    assert obj["signature"], "event must carry an ed25519 signature"
    # Hex-encoded ed25519 signature is 128 hex chars (64 bytes).
    assert len(obj["signature"]) == 128


def test_verify_chain_passes_on_clean_signed_log(tmp_path):
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    for i in range(5):
        log.append(_make_event(tmp_path / "work", i))
    result = log.verify_chain()
    assert result.ok
    assert result.total_events == 5
    assert result.unsigned_events == 0
    assert result.signature_failures == []


def test_signature_detects_chain_rewrite_attack(tmp_path):
    """The threat hash-chain alone misses: an attacker recomputes downstream
    prev_hash values after deleting an event. The chain validates, but
    signatures fail because the attacker doesn't have the private key.
    """
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    for i in range(5):
        log.append(_make_event(tmp_path / "work", i))
    log_file = tmp_path / ".workspaces" / log.folder_id / "events.jsonl"
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 5

    # Attacker deletes the middle event.
    del lines[2]
    # Attacker recomputes prev_hashes downstream so the chain validates.
    for i in range(1, len(lines)):
        prev_event = lines[i - 1]
        lines[i]["prev_hash"] = _canonical_event_hash(prev_event)
    # Write back.
    log_file.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    result = log.verify_chain()
    # The hash chain now validates (attacker recomputed it).
    assert result.broken_links == [], (
        "hash chain re-validated as expected after recomputation"
    )
    # But signatures don't — attacker can't forge them.
    assert not result.ok
    assert len(result.signature_failures) >= 1, (
        f"signature layer must catch the chain rewrite; got {result.signature_failures}"
    )


def test_signature_detects_content_modification(tmp_path):
    """Changing any signed field (actor, extra) breaks the signature."""
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    log.append(_make_event(tmp_path / "work", 0))
    log.append(_make_event(tmp_path / "work", 1))
    log_file = tmp_path / ".workspaces" / log.folder_id / "events.jsonl"
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    # Modify the actor of event 0 — its signature should no longer verify.
    lines[0]["actor"] = "attacker"
    log_file.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    result = log.verify_chain()
    assert not result.ok
    # Modification breaks both: signature on event 0 (content tampered) AND
    # the prev_hash on event 1 (which referred to the original canonical hash).
    assert len(result.signature_failures) >= 1 or len(result.broken_links) >= 1


def test_unsigned_events_accepted_as_legacy(tmp_path):
    """Events written before 0.6.6 lack a signature field. They must replay
    cleanly and be counted as ``unsigned_events`` without failing the chain.
    """
    log_dir = tmp_path / ".workspaces"
    log = MutationLog(tmp_path / "work", log_root=log_dir)
    log_file = log_dir / log.folder_id / "events.jsonl"

    legacy = {
        "event": "ingest",
        "folder_path": str(log.folder_path),
        "pair_id": "legacy-pair",
        "actor": "test",
        "audit_id": "00000000-0000-0000-0000-000000000001",
        "ts": 1700000000.0,
        "extra": {},
        "lifecycle_state": "",
        "channel": "system",
        "problem_id": "",
        "source_hash": "",
        "prev_hash": GENESIS_HASH,
        # NO signature field — this simulates a pre-0.6.6 event.
    }
    log_file.write_text(json.dumps(legacy) + "\n")
    # Append a new event on top — it will be signed.
    log.append(_make_event(tmp_path / "work", 99))

    result = log.verify_chain()
    assert result.ok, f"unsigned legacy events shouldn't break chain: {result.signature_failures}"
    assert result.total_events == 2
    assert result.unsigned_events == 1  # the hand-written legacy event
    assert result.signature_failures == []


def test_signed_payload_binds_chain_position(tmp_path):
    """The signed payload includes prev_hash. An attacker can't lift a valid
    signature from one chain position and replay it elsewhere — the prev_hash
    would differ, and the signature would be over the wrong bytes.
    """
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    log.append(_make_event(tmp_path / "work", 0))
    log.append(_make_event(tmp_path / "work", 1))
    log.append(_make_event(tmp_path / "work", 2))
    log_file = tmp_path / ".workspaces" / log.folder_id / "events.jsonl"
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]

    # Lift event 2's signature, put it on event 1 (which has different prev_hash).
    lifted_sig = lines[2]["signature"]
    lines[1]["signature"] = lifted_sig
    log_file.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    result = log.verify_chain()
    assert not result.ok
    assert len(result.signature_failures) >= 1


def test_signed_bytes_includes_prev_hash(tmp_path):
    """Direct test of _signed_bytes: identical content with different prev_hash
    must produce different signed payloads.
    """
    base = {
        "event": "ingest",
        "folder_path": "/x",
        "pair_id": "p",
        "actor": "u",
        "audit_id": "a",
        "ts": 1.0,
        "extra": {},
        "lifecycle_state": "",
        "channel": "system",
        "problem_id": "",
        "source_hash": "",
    }
    a = _signed_bytes({**base, "prev_hash": "GENESIS"})
    b = _signed_bytes({**base, "prev_hash": "deadbeef"})
    assert a != b, "different chain positions must produce different signed payloads"


def test_canonical_hash_still_excludes_signature_and_prev_hash(tmp_path):
    """Regression: the canonical hash must not depend on the signature OR the
    prev_hash, else the chain becomes circular.
    """
    base = {
        "event": "ingest",
        "folder_path": "/x",
        "pair_id": "p",
        "actor": "u",
        "audit_id": "a",
        "ts": 1.0,
        "extra": {},
        "lifecycle_state": "",
        "channel": "system",
        "problem_id": "",
        "source_hash": "",
    }
    h1 = _canonical_event_hash({**base, "prev_hash": "X", "signature": "Y"})
    h2 = _canonical_event_hash({**base, "prev_hash": "A", "signature": "B"})
    h3 = _canonical_event_hash(base)
    assert h1 == h2 == h3


def test_public_key_export(tmp_path):
    """Public key can be exported in PEM format for third-party verification."""
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    log.append(_make_event(tmp_path / "work", 0))  # triggers key generation
    from rvnd.signing import public_key_pem
    pem = public_key_pem()
    assert "-----BEGIN PUBLIC KEY-----" in pem
    assert "-----END PUBLIC KEY-----" in pem


def test_passphrase_encrypts_keys_at_rest_and_chain_still_verifies(
    tmp_path, monkeypatch
):
    """With WORKSPACE_KEY_PASSPHRASE set, private keys are written
    passphrase-encrypted; signing and chain verification work unchanged,
    and verification reads identity.pub (no passphrase needed)."""
    monkeypatch.setenv("WORKSPACE_KEY_PASSPHRASE", "correct horse battery")
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    log.append(_make_event(tmp_path / "work", 0))

    from rvnd import signing
    priv_pem = signing._private_key_path().read_bytes()
    assert b"ENCRYPTED" in priv_pem, "private key must be encrypted at rest"

    assert log.verify_chain().ok

    # Verification never needs the passphrase: the .pub loads without it.
    monkeypatch.delenv("WORKSPACE_KEY_PASSPHRASE")
    assert signing.identity_public_key_or_none() is not None
    assert log.verify_chain().ok


def test_encrypted_key_without_passphrase_fails_loud(tmp_path, monkeypatch):
    """An encrypted private key with no passphrase available must raise
    naming the env var — never fall through to a fresh identity."""
    monkeypatch.setenv("WORKSPACE_KEY_PASSPHRASE", "correct horse battery")
    from rvnd import signing
    signing.ensure_keypair()

    monkeypatch.delenv("WORKSPACE_KEY_PASSPHRASE")
    with pytest.raises(RuntimeError, match="WORKSPACE_KEY_PASSPHRASE"):
        signing.ensure_keypair()

    monkeypatch.setenv("WORKSPACE_KEY_PASSPHRASE", "wrong passphrase")
    with pytest.raises(RuntimeError, match="did not decrypt"):
        signing.ensure_keypair()


def test_key_rotation_is_a_first_class_event(tmp_path):
    """The key_rotation marker verify_chain reads to distinguish an authorised
    host move from a theft rewrite must be constructible as a first-class
    event, not only as a system-wrapped extra.kind."""
    from rvnd.mutation_log import LogEvent, VALID_EVENTS
    assert "key_rotation" in VALID_EVENTS
    LogEvent(
        event="key_rotation",
        folder_path=str(tmp_path / "ws"),
        pair_id="sha256:rotate",
        actor="controller",
        extra={"reason": "host move"},
    )


def test_key_rotation_marker_suppresses_strict_host_divergence(tmp_path, monkeypatch):
    """Under strict mode, a host change PRECEDED by a first-class key_rotation
    event is authorised and must not fail verification."""
    monkeypatch.setenv("WORKSPACE_STRICT_HOST_DIVERGENCE", "1")
    import json
    from rvnd import signing
    from rvnd.mutation_log import (
        LogEvent, MutationLog, _canonical_event_hash, _signed_bytes,
    )
    log = MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")
    for i in range(2):
        log.append(LogEvent(event="ingest", folder_path=str(tmp_path / "ws"),
                            pair_id=f"sha256:p{i}", actor="w", extra={"i": i}))

    log_file = log.log_file
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]

    def _forge(prev, event, host, aid, **extra):
        obj = {"event": event, "channel": "system",
               "folder_path": str(tmp_path / "ws"), "pair_id": f"sha256:{aid}",
               "lifecycle_state": "", "problem_id": "", "source_hash": "",
               "actor": "controller", "audit_id": aid, "ts": 1.0,
               "extra": extra, "prev_hash": _canonical_event_hash(prev),
               "signature": "", "host_id": host}
        obj["signature"] = signing.sign_bytes(_signed_bytes({**obj, "signature": ""}))
        return obj

    rot = _forge(lines[-1], "key_rotation", "newhost0000", "aaaa-rotation")
    nxt = _forge(rot, "ingest", "newhost0000", "bbbb-postrotate")
    lines += [rot, nxt]
    log_file.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n")

    post = log.verify_chain()
    assert post.ok, "an authorised key_rotation must not trip strict divergence"
    assert not post.host_divergence_warning
