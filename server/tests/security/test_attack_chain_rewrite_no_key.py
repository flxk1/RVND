# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""A1 — Chain rewrite under file-write access (Ed25519 catches it).

Unsigned chain-rewrite regression.
Tier:   T6 (host shell, write to log dir, NO identity key).
Status: MITIGATED (0.6.6+). Regression locks the mitigation.

PASS = attack failed. The defense (Ed25519 signature bound to ``prev_hash``)
must catch any chain rewrite the attacker performs without holding
``identity.priv``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rvnd.mutation_log import (
    LogEvent,
    MutationLog,
    _canonical_event_hash,
)


pytestmark = pytest.mark.security


@pytest.fixture
def isolated_keys(tmp_path, monkeypatch):
    """Bind keys + log root to a per-test sandbox."""
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    from rvnd import signing
    signing.ensure_keypair()
    return keydir


def _populated_chain(tmp_path: Path, n: int = 5) -> MutationLog:
    log = MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")
    for i in range(n):
        log.append(LogEvent(
            event="ingest",
            folder_path=str(tmp_path / "ws"),
            pair_id=f"sha256:pair-{i}",
            actor="attacker-target",
            extra={"i": i},
        ))
    return log


def test_a1_payload_rewrite_is_detected(tmp_path, isolated_keys):
    """Attacker modifies event[2]'s payload in place. Signature must catch it."""
    log = _populated_chain(tmp_path, n=5)

    # Baseline: chain is clean.
    pre = log.verify_chain()
    assert pre.ok, f"baseline chain should be clean, got: {pre}"

    # Attacker rewrites event[2]'s extra payload without the identity key.
    log_file = log.log_file
    lines = log_file.read_text().splitlines()
    obj = json.loads(lines[2])
    obj["extra"] = {"i": 999, "tampered": True}
    lines[2] = json.dumps(obj, ensure_ascii=False)
    log_file.write_text("\n".join(lines) + "\n")

    # Defense: verify_chain must flag the signature failure on event[2].
    post = log.verify_chain()
    assert not post.ok, (
        "VULNERABILITY: chain rewrite without identity key went undetected "
        f"(verify_chain returned ok=True). Result: {post}"
    )
    assert post.signature_failures, (
        "VULNERABILITY: signature_failures is empty after payload rewrite — "
        "Ed25519 layer is not catching the tamper."
    )
    failed_positions = {f["position"] for f in post.signature_failures}
    assert 2 in failed_positions, (
        f"signature failure should pin event at position 2; got {failed_positions}"
    )


def test_a1_delete_and_relink_is_detected(tmp_path, isolated_keys):
    """Classic chain-rewrite: delete event N, recompute prev_hash on N+1..end.

    Attacker has no signing key, so the re-linked events keep their ORIGINAL
    signatures bound to the ORIGINAL prev_hash values → signature_failures
    on every re-linked event.
    """
    log = _populated_chain(tmp_path, n=6)
    log_file = log.log_file
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    # Delete event index 3.
    deleted = lines.pop(3)
    # Recompute prev_hash on every event after the deletion point.
    for i in range(3, len(lines)):
        prev_canon = _canonical_event_hash(lines[i - 1]) if i > 0 else "GENESIS"
        lines[i]["prev_hash"] = prev_canon
        # NOTE: attacker does NOT touch the signature — they don't have the key.
    log_file.write_text(
        "\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n"
    )

    post = log.verify_chain()
    assert not post.ok, (
        "VULNERABILITY: delete-and-relink chain rewrite went undetected. "
        f"deleted audit_id={deleted.get('audit_id')}. Result: {post}"
    )
    assert post.signature_failures, (
        "VULNERABILITY: signature_failures empty after delete-and-relink. "
        "Re-linked events should fail signature verification because their "
        "signatures were bound to the ORIGINAL prev_hash."
    )


def test_a1_append_forged_event_is_detected(tmp_path, isolated_keys):
    """Attacker appends a synthetic event with bogus signature."""
    log = _populated_chain(tmp_path, n=3)
    log_file = log.log_file
    lines = log_file.read_text().splitlines()
    last = json.loads(lines[-1])
    forged = {
        "event": "ingest",
        "channel": "system",
        "folder_path": str(tmp_path / "ws"),
        "pair_id": "sha256:forged-by-attacker",
        "lifecycle_state": "ingested",
        "problem_id": "",
        "source_hash": "",
        "actor": "system",
        "audit_id": "00000000-forged-0000-0000-000000000000",
        "ts": 1.0,
        "extra": {"injected": True},
        "prev_hash": _canonical_event_hash(last),
        "signature": "deadbeef" * 16,  # 128 hex chars; right shape, wrong sig
        "host_id": "",
    }
    lines.append(json.dumps(forged, ensure_ascii=False))
    log_file.write_text("\n".join(lines) + "\n")

    post = log.verify_chain()
    assert not post.ok, (
        "VULNERABILITY: forged event with bogus signature accepted. "
        f"Result: {post}"
    )
    assert post.signature_failures, (
        "VULNERABILITY: forged event did not produce a signature_failure."
    )
