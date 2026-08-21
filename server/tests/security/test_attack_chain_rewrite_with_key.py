# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""A2 — Chain rewrite WHILE holding the identity key.

Signed chain-rewrite regression.
Tier: T7 (host shell + identity.priv).

Every event is stamped with ``host_id``; ``verify_chain`` surfaces a
mid-chain host shift without a key_rotation marker as
``host_divergence_warning`` — advisory by default, a hard verification
failure under ``WORKSPACE_STRICT_HOST_DIVERGENCE=1`` (single-host
deployments, where any host shift is an incident).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rvnd.mutation_log import (
    LogEvent,
    MutationLog,
    _canonical_event_hash,
    _signed_bytes,
)


pytestmark = pytest.mark.security


def _make_keydir(tmp_path: Path, name: str, monkeypatch) -> Path:
    keydir = tmp_path / name
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
            actor="legit-writer",
            extra={"i": i},
        ))
    return log


def test_a2_host_id_is_stamped_on_every_event(tmp_path, monkeypatch):
    """Mitigation substrate: every event must carry host_id (0.6.8 B4).

    Without per-event host_id stamping, the cross-host divergence detector
    has nothing to compare. This test fails if the substrate regresses.
    """
    _make_keydir(tmp_path, "host_a_keys", monkeypatch)
    log = _populated_chain(tmp_path, n=4)
    lines = [json.loads(l) for l in log.log_file.read_text().splitlines() if l.strip()]
    missing = [i for i, o in enumerate(lines) if not o.get("host_id")]
    assert not missing, (
        "VULNERABILITY: events missing host_id stamp (0.6.8 B4 regression) "
        f"at positions {missing}. Per-host divergence detection has no input."
    )


def test_a2_resign_with_stolen_key_chain_passes_signature_check(
    tmp_path, monkeypatch
):
    """With the identity key, the attacker CAN re-sign a rewrite.

    Documents the residual T7 attack surface. This test asserts that the
    signature layer alone (which is all 0.6.8 has wired) is NOT a defense
    against an attacker who holds the identity key. The mitigation has to
    come from a SECOND signal — host_id divergence, an external transparency
    log, or hardware-bound keys.
    """
    _make_keydir(tmp_path, "host_a_keys", monkeypatch)
    log = _populated_chain(tmp_path, n=5)

    from rvnd import signing

    # Attacker (holding the key) deletes event[2] and re-links + re-signs
    # the rest.
    log_file = log.log_file
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    lines.pop(2)
    (
        _canonical_event_hash(lines[1]) if len(lines) > 1 else "GENESIS"
    )
    for i in range(2, len(lines)):
        if i > 0:
            lines[i]["prev_hash"] = (
                _canonical_event_hash(lines[i - 1]) if i > 0 else "GENESIS"
            )
        # Re-sign with the stolen key.
        signed = _signed_bytes({**lines[i], "signature": ""})
        lines[i]["signature"] = signing.sign_bytes(signed)
    log_file.write_text(
        "\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n"
    )

    # Signature + hash chain both validate — this is the documented T7 gap.
    post = log.verify_chain()
    assert post.ok, (
        "Unexpected: signature-only verification caught a re-signed rewrite. "
        "If this passes the test should be inverted — the mitigation just "
        f"landed. Result: {post}"
    )


def test_a2_cross_host_divergence_is_flagged(tmp_path, monkeypatch):
    """Full mitigation: an event re-stamped with host B in a chain whose
    earlier events carry host A must surface as divergence in verify_chain.

    The verifier surfaces host divergence while leaving it advisory unless
    strict mode is enabled.
    """
    # This test asserts the ADVISORY default; clear strict mode in case the
    # hardened profile (RVND_TEST_HARDENED=1) enabled it suite-wide. The
    # strict behaviour has its own test below.
    monkeypatch.delenv("WORKSPACE_STRICT_HOST_DIVERGENCE", raising=False)
    _make_keydir(tmp_path, "host_a_keys", monkeypatch)
    log = _populated_chain(tmp_path, n=3)

    from rvnd import signing

    # Forge a new event stamped with host B's id and signed with host A's key.
    log_file = log.log_file
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    last = lines[-1]
    forged = {
        "event": "ingest",
        "channel": "system",
        "folder_path": str(tmp_path / "ws"),
        "pair_id": "sha256:forged-cross-host",
        "lifecycle_state": "ingested",
        "problem_id": "",
        "source_hash": "",
        "actor": "system",
        "audit_id": "ffffffff-cross-host-aaaa-000000000000",
        "ts": 1.0,
        "extra": {},
        "prev_hash": _canonical_event_hash(last),
        "signature": "",
        "host_id": "ffeeddccbbaa",  # explicitly NOT this host's id
    }
    signed = _signed_bytes({**forged, "signature": ""})
    forged["signature"] = signing.sign_bytes(signed)
    lines.append(forged)
    log_file.write_text(
        "\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n"
    )

    post = log.verify_chain()
    divergence = getattr(post, "host_divergence_warning", None) or \
        getattr(post, "host_divergence", None) or \
        getattr(post, "cross_host_warnings", None)
    assert divergence, (
        "Cross-host event present but verify_chain reports no divergence "
        "warning."
    )
    # Advisory by default: divergence alone must not fail the chain — a
    # legitimate multi-host deployment signs from more than one machine.
    assert post.ok, "host divergence must stay advisory without strict mode"


def test_a2_strict_mode_fails_the_chain_on_divergence(tmp_path, monkeypatch):
    """WORKSPACE_STRICT_HOST_DIVERGENCE=1 turns the advisory warning into a
    verification failure: on a single-host deployment a mid-chain host shift
    has no legitimate explanation, so the chain must not verify."""
    monkeypatch.setenv("WORKSPACE_STRICT_HOST_DIVERGENCE", "1")
    _make_keydir(tmp_path, "host_a_keys", monkeypatch)
    log = _populated_chain(tmp_path, n=3)

    from rvnd import signing

    log_file = log.log_file
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    last = lines[-1]
    forged = {
        "event": "ingest",
        "channel": "system",
        "folder_path": str(tmp_path / "ws"),
        "pair_id": "sha256:forged-cross-host-strict",
        "lifecycle_state": "ingested",
        "problem_id": "",
        "source_hash": "",
        "actor": "system",
        "audit_id": "ffffffff-cross-host-bbbb-000000000000",
        "ts": 1.0,
        "extra": {},
        "prev_hash": _canonical_event_hash(last),
        "signature": "",
        "host_id": "ffeeddccbbaa",  # explicitly NOT this host's id
    }
    signed = _signed_bytes({**forged, "signature": ""})
    forged["signature"] = signing.sign_bytes(signed)
    lines.append(forged)
    log_file.write_text(
        "\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n"
    )

    post = log.verify_chain()
    assert post.host_divergence_warning
    assert not post.ok, "strict mode must fail verification on host divergence"
