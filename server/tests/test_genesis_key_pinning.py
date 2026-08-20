# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Genesis key pinning — bind a chain to the identity key that created it.

An attacker with write access to the key directory and the log can generate a
fresh keypair, rewrite and re-sign the whole chain, and verify_chain would
return ok=True — nothing pinned the chain to its original signing key. These
tests pin the mitigation: an opt-in genesis key_registration event plus a
relocatable TOFU pin file, enforced at verify time.

Run: PYTHONPATH=server/src python3 -m pytest tests/test_genesis_key_pinning.py -q
"""
from __future__ import annotations

import json

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

from workspaces import signing
from workspaces.mutation_log import (
    LogEvent,
    MutationLog,
    _canonical_event_hash,
    _signed_bytes,
)


@pytest.fixture(autouse=True)
def _keydir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))


def _log(tmp_path, name="ws"):
    return MutationLog(tmp_path / name, log_root=tmp_path / "logs")


def _ingest(folder, i):
    return LogEvent(event="ingest", folder_path=str(folder),
                    pair_id=f"sha256:p{i}", actor="w", extra={"i": i})


def test_pinning_off_by_default_no_registration_event(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKSPACE_KEY_PINNING", raising=False)
    log = _log(tmp_path)
    log.append(_ingest(tmp_path / "ws", 0))
    events = [json.loads(l) for l in log.log_file.read_text().splitlines() if l.strip()]
    assert [e["event"] for e in events] == ["ingest"]      # no genesis injection
    r = log.verify_chain()
    assert r.ok and r.key_pin is None                      # unpinned, tolerated


def test_pinning_on_registers_at_genesis_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_PINNING", "1")
    log = _log(tmp_path)
    log.append(_ingest(tmp_path / "ws", 0))
    log.append(_ingest(tmp_path / "ws", 1))
    events = [json.loads(l) for l in log.log_file.read_text().splitlines() if l.strip()]
    assert events[0]["event"] == "key_registration"
    assert events[0]["extra"]["identity_fingerprint"] == signing.identity_fingerprint_or_none()
    r = log.verify_chain()
    assert r.ok
    assert r.key_pin["registered"] and r.key_pin["pin_file"] == "match"


def test_pin_file_written_once_and_relocatable(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_PINNING", "1")
    pin_dir = tmp_path / "readonly_pins"
    monkeypatch.setenv("WORKSPACE_KEY_PIN_DIR", str(pin_dir))
    log = _log(tmp_path)
    log.append(_ingest(tmp_path / "ws", 0))
    # The pin lives under the relocated dir, not the log tree.
    pins = list(pin_dir.rglob("identity.pin"))
    assert pins and json.loads(pins[0].read_text())["fingerprint"] == \
        signing.identity_fingerprint_or_none()


def _rewrite_rekeyed(log, monkeypatch, tmp_path):
    """Simulate the T7 attack: a fresh identity key rewrites and re-signs the
    whole chain, including the embedded registration, and drops its pubkey on
    disk so per-event signatures verify."""
    lines = [json.loads(l) for l in log.log_file.read_text().splitlines() if l.strip()]
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "attacker_keys"))
    signing.ensure_keypair()                               # attacker key on disk
    new_fp = signing.identity_fingerprint_or_none()
    new_pem = signing.identity_public_pem_or_none()
    prev = "GENESIS"
    out = []
    for o in lines:
        o = dict(o)
        if o["event"] == "key_registration":
            o["extra"] = dict(o["extra"], identity_fingerprint=new_fp,
                              identity_pub=new_pem)
        o["prev_hash"] = prev
        o["signature"] = ""
        o["signature"] = signing.sign_bytes(_signed_bytes({**o, "signature": ""}))
        out.append(o)
        prev = _canonical_event_hash(o)
    log.log_file.write_text("\n".join(json.dumps(x) for x in out) + "\n")


def test_rekeyed_rewrite_is_caught_by_the_relocated_pin(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_PINNING", "1")
    monkeypatch.setenv("WORKSPACE_KEY_PIN_DIR", str(tmp_path / "readonly_pins"))
    log = _log(tmp_path)
    log.append(_ingest(tmp_path / "ws", 0))
    assert log.verify_chain().ok

    _rewrite_rekeyed(log, monkeypatch, tmp_path)

    r = log.verify_chain()
    reasons = {f["reason"] for f in r.signature_failures}
    assert not r.ok, "a re-keyed rewrite must fail once the chain is pinned"
    assert "key_pin_tampered" in reasons                   # the relocated pin's teeth
    assert r.key_pin["pin_file"] == "mismatch"


def test_enforcement_is_not_downgradable_by_unsetting_the_env(tmp_path, monkeypatch):
    """Once a chain is registered, unsetting WORKSPACE_KEY_PINNING must not stop
    verify from enforcing the pin — otherwise an attacker just clears the var."""
    monkeypatch.setenv("WORKSPACE_KEY_PINNING", "1")
    monkeypatch.setenv("WORKSPACE_KEY_PIN_DIR", str(tmp_path / "readonly_pins"))
    log = _log(tmp_path)
    log.append(_ingest(tmp_path / "ws", 0))
    _rewrite_rekeyed(log, monkeypatch, tmp_path)

    monkeypatch.delenv("WORKSPACE_KEY_PINNING", raising=False)   # attacker clears it
    r = log.verify_chain()
    assert not r.ok, "registered chain must stay enforced regardless of the env"


def test_unregistered_chain_tolerated_unless_strict(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKSPACE_KEY_PINNING", raising=False)
    log = _log(tmp_path)
    log.append(_ingest(tmp_path / "ws", 0))
    assert log.verify_chain().ok                            # legacy, tolerated

    monkeypatch.setenv("WORKSPACE_STRICT_KEY_PINNING", "1")
    r = log.verify_chain()
    assert not r.ok
    assert any(f["reason"] == "chain_unregistered" for f in r.signature_failures)
