# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Short-lived, fail-closed session capabilities for governed execution."""
from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import asdict, dataclass
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import signing

PREFIX = "RVSC1"
MAX_TTL_SECONDS = 900
CLOCK_SKEW_SECONDS = 30


class CapabilityError(ValueError):
    """A stable admission refusal safe to record in an incident."""


@dataclass(frozen=True)
class SessionClaims:
    party: str
    lane_id: str
    folder: str
    grade: str
    policy_fingerprint: str
    spec_fingerprint: str
    uid: int
    nonce: str
    iat: int
    exp: int
    kid: str


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.b64decode(
        padded.replace("-", "+").replace("_", "/"), validate=True
    )


def mint(
    *,
    party: str,
    lane_id: str,
    folder: str,
    grade: str,
    policy_fingerprint: str,
    spec_fingerprint: str,
    uid: int,
    ttl_seconds: int = MAX_TTL_SECONDS,
) -> tuple[str, SessionClaims]:
    """Mint with the host identity key; never log the returned bearer token."""
    if not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise CapabilityError("ttl outside allowed range")
    private, public = signing.ensure_keypair()
    now = int(time.time())
    claims = SessionClaims(
        party=party,
        lane_id=lane_id,
        folder=folder,
        grade=grade,
        policy_fingerprint=policy_fingerprint,
        spec_fingerprint=spec_fingerprint,
        uid=uid,
        nonce=secrets.token_hex(16),
        iat=now,
        exp=now + ttl_seconds,
        kid=signing.fingerprint_of(public),
    )
    payload = _canonical(asdict(claims))
    token = f"{PREFIX}.{_encode(payload)}.{signing.sign_bytes(payload, private)}"
    return token, claims


class CapabilityVerifier:
    """Verify against a pinned trust root and a nonce revocation set."""

    def __init__(
        self,
        trust_root: Ed25519PublicKey,
        *,
        revoked_nonces: set[str] | None = None,
    ):
        self._root = trust_root
        self._kid = signing.fingerprint_of(trust_root)
        self._revoked = revoked_nonces if revoked_nonces is not None else set()

    @classmethod
    def from_key_dir(cls, **kwargs) -> "CapabilityVerifier":
        public = signing.identity_public_key_or_none()
        if public is None:
            raise CapabilityError("identity trust root unavailable")
        if "revoked_nonces" not in kwargs:
            kwargs["revoked_nonces"] = FileRevocationStore.default()
        return cls(public, **kwargs)

    def revoke(self, nonce: str) -> None:
        self._revoked.add(nonce)

    def verify(
        self,
        token: str,
        *,
        expected_folder: str | None = None,
        expected_uid: int | None = None,
        now: int | None = None,
    ) -> SessionClaims:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != PREFIX:
            raise CapabilityError("malformed capability")
        try:
            payload = _decode(parts[1])
        except Exception as exc:
            raise CapabilityError("invalid capability encoding") from exc
        if not signing.verify_signature(payload, parts[2], self._root):
            raise CapabilityError("invalid capability signature")
        try:
            claims = SessionClaims(**json.loads(payload))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CapabilityError("invalid capability claims") from exc
        current = int(time.time()) if now is None else int(now)
        if claims.kid != self._kid:
            raise CapabilityError("capability trust root mismatch")
        if claims.iat > current + CLOCK_SKEW_SECONDS:
            raise CapabilityError("capability issued in the future")
        if current > claims.exp + CLOCK_SKEW_SECONDS:
            raise CapabilityError("capability expired")
        if claims.exp - claims.iat > MAX_TTL_SECONDS:
            raise CapabilityError("capability ttl exceeds ceiling")
        if claims.nonce in self._revoked:
            raise CapabilityError("capability revoked")
        if expected_folder is not None and claims.folder != expected_folder:
            raise CapabilityError("capability folder mismatch")
        if expected_uid is not None and claims.uid != expected_uid:
            raise CapabilityError("capability uid mismatch")
        return claims


class FileRevocationStore(set):
    """Append-only nonce revocations shared across broker processes/restarts."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        super().__init__()
        self._reload()

    @classmethod
    def default(cls) -> "FileRevocationStore":
        configured = os.environ.get("RVND_CAPABILITY_REVOCATIONS", "").strip()
        if configured:
            return cls(configured)
        key_root = Path(
            os.environ.get("WORKSPACE_KEY_DIR", str(Path.home() / ".workspace" / "keys"))
        ).expanduser()
        return cls(key_root / "revoked-session-nonces")

    def _reload(self) -> None:
        if not self.path.exists():
            return
        for value in self.path.read_text(encoding="utf-8").splitlines():
            if value.strip():
                super().add(value.strip())

    def add(self, nonce: str) -> None:
        self._reload()
        if nonce in self:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write(nonce + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        super().add(nonce)

    def __contains__(self, nonce: object) -> bool:
        self._reload()
        return super().__contains__(nonce)
