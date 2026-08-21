# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Strict capability-token verification accepts only trusted issuer signatures."""

import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from rvnd.lock.core import CapabilityToken, ToolCall, validate_token


def _token(private_key=None):
    now = int(time.time())
    token = CapabilityToken(
        iss="issuer.example", sub="agent:test", aud="hr.get_employee",
        iat=now, exp=now + 60, scope={"fields": ["employee_id"]},
        controller="controller.example", task_id="task-1",
    )
    if private_key is not None:
        token.sign(private_key)
    return token


def _trust_store(tmp_path, issuer, public_key):
    pem = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    path = tmp_path / "capability-issuers.json"
    path.write_text(json.dumps({issuer: pem}), encoding="utf-8")
    return path


def _validate(token):
    call = ToolCall("hr.get_employee", {"employee_id": "E-1"}, token)
    return validate_token(token, call)


def test_strict_mode_accepts_trusted_signed_token(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    token = _token(private_key)
    monkeypatch.setenv("LOCK_BETA_STRICT_TOKEN_SIG", "1")
    monkeypatch.setenv(
        "LOCK_CAPABILITY_TRUST_STORE",
        str(_trust_store(tmp_path, token.iss, private_key.public_key())),
    )
    assert _validate(token).valid


def test_strict_mode_rejects_unsigned_token(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    token = _token()
    monkeypatch.setenv("LOCK_BETA_STRICT_TOKEN_SIG", "1")
    monkeypatch.setenv(
        "LOCK_CAPABILITY_TRUST_STORE",
        str(_trust_store(tmp_path, token.iss, private_key.public_key())),
    )
    assert not _validate(token).valid


def test_strict_mode_rejects_tampered_claim(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    token = _token(private_key)
    token.aud = "payroll.export"
    monkeypatch.setenv("LOCK_BETA_STRICT_TOKEN_SIG", "1")
    monkeypatch.setenv(
        "LOCK_CAPABILITY_TRUST_STORE",
        str(_trust_store(tmp_path, token.iss, private_key.public_key())),
    )
    result = _validate(token)
    assert not result.valid
    assert any("signature is invalid" in finding.detail for finding in result.findings)


def test_strict_mode_rejects_unknown_issuer(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    token = _token(private_key)
    monkeypatch.setenv("LOCK_BETA_STRICT_TOKEN_SIG", "1")
    monkeypatch.setenv(
        "LOCK_CAPABILITY_TRUST_STORE",
        str(_trust_store(tmp_path, "other.example", private_key.public_key())),
    )
    assert not _validate(token).valid


def test_default_mode_remains_semantic_only(monkeypatch):
    monkeypatch.delenv("LOCK_BETA_STRICT_TOKEN_SIG", raising=False)
    assert _validate(_token()).valid
