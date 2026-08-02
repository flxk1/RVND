# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for 0.6.8 B4 — per-host identity keys + controller key + host_id stamping.

Per-host key layout regression:

- Identity keys live at ``<keydir>/<host_id>/identity.{priv,pub}`` where
  ``host_id`` is a 12-char hex prefix of ``sha256(hostname + "|" + machine_id)``.
- Every mutation-log event is stamped with the active ``host_id`` BEFORE the
  hash + signature are computed, so cross-host divergence is detectable on
  ``verify_chain``.
- The controller key is SEPARATE from the identity key: workspace-scoped
  (NOT per-host) at ``<keydir>/controller.{priv,pub}``. Generated via the
  explicit ``workspaces keys init-controller`` ceremony (or
  ``ensure_controller_keypair()`` for direct callers); idempotent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces.mutation_log import LogEvent, MutationLog


# ---------------------------------------------------------------------------
# Per-host identity key generation
# ---------------------------------------------------------------------------


def test_per_host_key_generation(tmp_path, monkeypatch):
    """ensure_keypair() must create the key at <root>/<host_id>/identity.priv,
    NOT at <root>/identity.priv. The per-host namespacing is the whole point
    of B4 — sharing a homedir across hosts no longer means sharing a key."""
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))

    from workspaces.signing import _host_id, ensure_keypair, _key_root_dir
    ensure_keypair()

    host_id = _host_id()
    assert (keydir / host_id / "identity.priv").exists(), (
        f"identity.priv should live under per-host subdir; "
        f"contents of {keydir}: {list(keydir.iterdir()) if keydir.exists() else 'missing'}"
    )
    assert (keydir / host_id / "identity.pub").exists()
    # The legacy flat path must NOT exist on a fresh install.
    assert not (keydir / "identity.priv").exists(), (
        "fresh installs must not create the legacy flat-root keypair"
    )
    # Sanity: _key_root_dir() points at the override.
    assert _key_root_dir() == keydir


def test_host_id_shape(monkeypatch, tmp_path):
    """_host_id() must return exactly 12 hex chars."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from workspaces.signing import _host_id
    hid = _host_id()
    assert isinstance(hid, str)
    assert len(hid) == 12
    int(hid, 16)  # must be valid hex


# ---------------------------------------------------------------------------
# Event stamping
# ---------------------------------------------------------------------------


def test_event_stamps_host_id(tmp_path, monkeypatch):
    """Every appended event must carry the 12-char host_id field. The stamp
    happens BEFORE hashing/signing so it's part of the canonical content."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from workspaces.signing import _host_id

    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    log.append(LogEvent(
        event="ingest",
        folder_path=str(tmp_path / "work"),
        pair_id="pair-host-id-test",
        actor="test",
    ))

    raw = log.log_file.read_text(encoding="utf-8").splitlines()
    obj = json.loads(raw[0])
    assert obj.get("host_id"), "event must carry a host_id field after 0.6.8 B4"
    assert obj["host_id"] == _host_id()
    assert len(obj["host_id"]) == 12

    # Read-back via from_dict round-trips the value.
    events = list(log.replay())
    assert events[0].host_id == _host_id()


def test_verify_chain_passes_with_host_id_stamped(tmp_path, monkeypatch):
    """Adding the host_id field must not break the existing chain + signature
    layers. The canonical hash now includes host_id; the signature signs over
    the new payload; verification still passes."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log = MutationLog(tmp_path / "work", log_root=tmp_path / ".workspaces")
    for i in range(3):
        log.append(LogEvent(
            event="ingest",
            folder_path=str(tmp_path / "work"),
            pair_id=f"pair-{i}",
            actor="test",
        ))
    result = log.verify_chain()
    assert result.ok, (
        f"chain failed verification after host_id stamping: "
        f"broken={result.broken_links} sig_fail={result.signature_failures}"
    )
    assert result.total_events == 3


# ---------------------------------------------------------------------------
# Controller key (D2)
# ---------------------------------------------------------------------------


def test_controller_key_separate_from_identity_key(tmp_path, monkeypatch):
    """The controller key fingerprint MUST differ from the identity key
    fingerprint — they're independent keypairs, not the same key reused."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from workspaces.signing import (
        ensure_controller_keypair,
        ensure_keypair,
        public_controller_key_fingerprint,
        public_key_fingerprint,
    )

    ensure_keypair()
    ensure_controller_keypair()

    identity_fp = public_key_fingerprint()
    controller_fp = public_controller_key_fingerprint()

    assert identity_fp
    assert controller_fp
    assert len(identity_fp) == 16
    assert len(controller_fp) == 16
    assert identity_fp != controller_fp, (
        "identity + controller fingerprints collide — keys are not "
        "separately generated"
    )


def test_controller_keypair_idempotent(tmp_path, monkeypatch):
    """ensure_controller_keypair() called twice must return the same key
    (same fingerprint). The second call must NOT regenerate."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from workspaces.signing import (
        ensure_controller_keypair,
        public_controller_key_fingerprint,
    )

    ensure_controller_keypair()
    fp_a = public_controller_key_fingerprint()
    ensure_controller_keypair()
    fp_b = public_controller_key_fingerprint()

    assert fp_a is not None
    assert fp_a == fp_b


def test_controller_key_not_initialised_returns_none_fingerprint(tmp_path, monkeypatch):
    """If the operator has never run init-controller, the fingerprint helper
    returns None so `workspaces status` can render `(none)` instead of silently
    auto-creating a controller key."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys-empty"))
    from workspaces.signing import public_controller_key_fingerprint
    assert public_controller_key_fingerprint() is None


def test_controller_lives_at_flat_root_not_under_host_subdir(tmp_path, monkeypatch):
    """Controller key is workspace-scoped, not per-host — must sit at the
    flat root, not under <host_id>/. Same controller signs from every host."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from workspaces.signing import (
        _host_id,
        ensure_controller_keypair,
        _controller_private_key_path,
    )
    ensure_controller_keypair()
    keydir = tmp_path / "keys"
    assert (keydir / "controller.priv").exists()
    assert not (keydir / _host_id() / "controller.priv").exists()
    assert _controller_private_key_path() == keydir / "controller.priv"


def test_controller_sign_and_verify_roundtrip(tmp_path, monkeypatch):
    """sign_with_controller() + verify_controller_signature() round-trip
    cleanly. A tampered payload fails verification."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from workspaces.signing import (
        sign_with_controller,
        verify_controller_signature,
    )
    payload = b"tombstone-content-bytes"
    sig = sign_with_controller(payload)
    assert verify_controller_signature(payload, sig) is True
    assert verify_controller_signature(b"tampered", sig) is False
