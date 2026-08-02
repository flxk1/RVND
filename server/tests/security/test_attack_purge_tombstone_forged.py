# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""A7 — Forged purge tombstone WITHOUT controller key.

Forged purge-tombstone regression.
Tier:   T7 (host shell + identity key, NO controller key).
Status: MITIGATED (0.6.8 B1). Two-signature tombstone requires both
        operator and controller signatures. Regression locks the
        segregation-of-duties claim.

PASS = attack failed. A forged tombstone with only the operator signature
(or with a controller_sig signed by an unauthorised key) must NOT validate
when ``verify_chain`` runs.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest

from workspaces.mutation_log import (
    LogEvent,
    MutationLog,
    _canonical_event_hash,
    _signed_bytes,
)


pytestmark = pytest.mark.security


@pytest.fixture
def operator_only_keys(tmp_path, monkeypatch):
    """Operator (identity) key present, controller key absent."""
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    from workspaces import signing
    signing.ensure_keypair()
    # Deliberately NOT calling ensure_controller_keypair() — that's the
    # attacker capability we're modelling.
    return keydir


def _populated_chain(tmp_path: Path, n: int = 3) -> MutationLog:
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


def _forge_tombstone(
    log: MutationLog,
    pair_id: str,
    controller_sig_value: str,
    operator_keyid: str,
) -> dict:
    """Build a tombstone-shaped event with operator-only signing."""
    from workspaces import signing

    log_file = log.log_file
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    last = lines[-1]
    tombstone = {
        "ts": time.time(),
        "event": "purge",
        "folder_path": str(log.folder_path),
        "pair_id": pair_id,
        "channel": "system",
        "actor": "system:purge",
        "audit_id": str(uuid.uuid4()),
        "lifecycle_state": "purged",
        "problem_id": "",
        "source_hash": "",
        "extra": {
            "kind": "purge_tombstone",
            "purged_event_audit_ids": [],
            "purged_event_count": 0,
            "legal_basis": "art_17_1_b",
            "requester_ref": "req:forged",
            "reason": "attacker forgery",
            "operator_keyid": operator_keyid,
            "controller_keyid": "ffffffffffffffff" * 4,
            "controller_sig": controller_sig_value,
        },
        "prev_hash": _canonical_event_hash(last),
        "signature": "",
        "host_id": "",
    }
    # Sign operator-side with the real (stolen) identity key.
    signed = _signed_bytes({**tombstone, "signature": ""})
    tombstone["signature"] = signing.sign_bytes(signed)
    return tombstone


def _append_raw_event(log: MutationLog, event_obj: dict) -> None:
    """Write a raw event dict to the log file, bypassing the API."""
    with log.log_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event_obj, ensure_ascii=False) + "\n")


@pytest.mark.parametrize(
    "variant,controller_sig",
    [
        ("empty_string", ""),
        ("garbage_hex", "deadbeef" * 16),
        ("copy_of_operator_sig", "PLACEHOLDER"),
        ("self_generated_key_sig", "PLACEHOLDER_SELF_GEN"),
    ],
)
def test_a7_forged_tombstone_does_not_silently_disappear_data(
    tmp_path, operator_only_keys, variant, controller_sig
):
    """A forged tombstone must NOT cause verify_chain to return ok=True with
    no signature failure on the forged event.

    The defense surface is twofold:
      1. The forged event's operator signature was computed over a payload
         that includes ``controller_sig``; if the forger picks a placeholder
         and doesn't re-sign properly, the operator sig fails.
      2. Even if the operator sig validates, a downstream consumer that
         honours the segregation-of-duties claim must NOT accept the
         tombstone without verifying ``controller_sig`` against the
         registered controller pubkey.
    """
    log = _populated_chain(tmp_path, n=3)
    from workspaces import signing

    operator_keyid = signing.public_key_fingerprint() or ""

    # Resolve PLACEHOLDER variants now that we have the chain in hand.
    actual_controller_sig = controller_sig
    if controller_sig == "PLACEHOLDER":
        # Re-use the operator signature shape — looks valid, isn't.
        actual_controller_sig = "ab" * 32
    elif controller_sig == "PLACEHOLDER_SELF_GEN":
        # Attacker generates their own keypair and signs with it. The bytes
        # are a real Ed25519 signature but from the WRONG key.
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
            attacker_priv = Ed25519PrivateKey.generate()
            actual_controller_sig = attacker_priv.sign(b"attacker-payload").hex()
        except Exception:
            actual_controller_sig = "cd" * 32

    forged = _forge_tombstone(log, "sha256:pair-1", actual_controller_sig,
                              operator_keyid)
    _append_raw_event(log, forged)

    # NEW MODEL (0.6.8.2): controller co-signature is opt-in. Single-key
    # purge by the operator alone is legitimate, so the WRITE-time refusal is
    # gone. The forged tombstone here, however, CLAIMS a controller
    # co-signature (it sets controller_keyid + controller_sig). That claim is
    # now validated at VERIFY time against the registered controller pubkey.
    # Since this attacker has NO controller key, the claimed co-signature
    # cannot be verified — verify_chain MUST flag it. (This is strictly
    # stronger than 0.6.8, which only gated at write time.)
    post = log.verify_chain()

    assert not post.ok, (
        f"VULNERABILITY: forged two-key tombstone passed verify_chain. "
        f"variant={variant} result={post}"
    )
    forged_audit = forged["audit_id"]
    flagged = any(
        f.get("audit_id") == forged_audit for f in post.signature_failures
    )
    assert flagged, (
        f"VULNERABILITY: forged tombstone {forged_audit} not in "
        f"signature_failures. variant={variant} failures={post.signature_failures}"
    )

    # And the operator can still perform a LEGITIMATE single-key purge via the
    # API without a controller key — the relaxation we deliberately shipped.
    n = log.purge(
        "sha256:pair-2",
        legal_basis="art_17_1_b",
        requester_ref="req:legit",
        reason="legit single-key",
    )
    assert n == 1


def test_a7_legit_two_key_tombstone_validates(tmp_path, monkeypatch):
    """Sanity: with BOTH keys present, purge writes a valid tombstone and
    the chain re-verifies as ok=True."""
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    from workspaces import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()

    log = _populated_chain(tmp_path, n=3)
    n_purged = log.purge(
        "sha256:pair-1",
        legal_basis="art_17_1_b",
        requester_ref="req:42",
        reason="subject withdrew consent",
    )
    assert n_purged == 1, f"expected 1 event purged, got {n_purged}"

    result = log.verify_chain()
    assert result.ok, f"legit purge broke verify_chain: {result}"
    assert result.purged_with_tombstone >= 1, (
        f"tombstone not recognised by verifier: {result}"
    )
