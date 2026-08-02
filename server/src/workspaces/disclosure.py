# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Art. 50 disclosure — the egress surface for externally-bound output (C2).

The transparency obligation extends beyond the operator to every natural
person an agent's action touches: the recipient of the email, the audience of
a post, the holder of a modified account. For synthetic content, Art. 50(2)
additionally requires machine-readable marking. Workspaces performs no external
action itself today (dispatch-record pattern), so this surface costs nothing
now and is expensive to retrofit later — it is built before the gateway and
the contract obligation-runtime expose any externally-bound op.

What this module produces is a *disclosure envelope*: a machine-readable
declaration that the wrapped output originated from an AI system, which system
(per-host identity), and whom it concerns — signed with the host key so an
affected party can, in principle, verify origin (the §6.3 spoofing point for
outbound artifacts).

ONE swap point. The marking FORMAT is provisional. The Art. 50 Code of
Practice (2nd draft 5 Mar 2026; final expected ~June 2026) will specify the
machine-readable marking format; when it lands, only :data:`MARKING_PROFILE`
and :func:`_marker` change — every caller and the envelope schema stay put.
Pending expert review; not legal advice.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import signing

__all__ = ["DisclosureEnvelope", "make_envelope", "verify_envelope",
           "MARKING_PROFILE", "MARKING_PROFILE_PROVISIONAL"]

# Provisional marking profile. Swap to the Art. 50 Code's format when final;
# the profile string travels in every envelope so consumers can tell which
# scheme produced a given marker (and a future verifier can refuse stale ones).
MARKING_PROFILE_PROVISIONAL = "workspace-ai-origin-provisional-1"
MARKING_PROFILE = MARKING_PROFILE_PROVISIONAL

# Envelope schema version — independent of the marking profile, so the
# wrapper can evolve without implying the marker format changed.
ENVELOPE_VERSION = 1


def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _marker(profile: str) -> dict[str, Any]:
    """The machine-readable AI-origin marker (Art. 50(2)). Provisional shape;
    the single function the Code's final format will replace."""
    return {"profile": profile,
            "ai_generated": True,
            "statement": "This content was produced by an AI system."}


@dataclass
class DisclosureEnvelope:
    """A signed declaration wrapping one externally-bound output."""
    content_hash: str
    originating_system: str               # host identity fingerprint
    affected_parties: list[str]           # whom the output concerns (Art. 50(1))
    action_class: str
    marking: dict[str, Any]
    created_at: str
    envelope_version: int = ENVELOPE_VERSION
    signature: str = ""                   # set by make_envelope
    public_key_pem: str = ""              # set by make_envelope (for offline verify)
    meta: dict[str, Any] = field(default_factory=dict)

    def signing_payload(self) -> bytes:
        """Canonical bytes that the signature covers — every field except the
        signature itself, deterministically ordered."""
        body = {"content_hash": self.content_hash,
                "originating_system": self.originating_system,
                "affected_parties": sorted(self.affected_parties),
                "action_class": self.action_class,
                "marking": self.marking,
                "created_at": self.created_at,
                "envelope_version": self.envelope_version,
                "meta": self.meta}
        return json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        d = {"content_hash": self.content_hash,
             "originating_system": self.originating_system,
             "affected_parties": sorted(self.affected_parties),
             "action_class": self.action_class,
             "marking": self.marking,
             "created_at": self.created_at,
             "envelope_version": self.envelope_version,
             "signature": self.signature,
             "public_key_pem": self.public_key_pem,
             "meta": self.meta}
        return d


def make_envelope(content: str, *, affected_parties: list[str],
                  action_class: str = "",
                  marking_profile: str = MARKING_PROFILE,
                  meta: Optional[dict[str, Any]] = None) -> DisclosureEnvelope:
    """Build and sign a disclosure envelope for one externally-bound output.

    ``content`` is hashed (not stored — minimisation; the envelope discloses
    origin, it is not a copy of the payload). ``affected_parties`` must be
    non-empty: a disclosure that names nobody discloses to nobody. Signed with
    the host identity key; the public key travels along so a recipient can
    verify offline.
    """
    parties = [p for p in (affected_parties or []) if str(p).strip()]
    if not parties:
        raise ValueError(
            "a disclosure envelope must name at least one affected party — "
            "an output bound for a third party with no named recipient cannot "
            "be disclosed")
    env = DisclosureEnvelope(
        content_hash=_content_hash(content),
        originating_system=signing.public_key_fingerprint(),
        affected_parties=parties,
        action_class=action_class,
        marking=_marker(marking_profile),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        meta=meta or {})
    env.signature = signing.sign_bytes(env.signing_payload())
    env.public_key_pem = signing.public_key_pem()
    return env


def verify_envelope(envelope: dict[str, Any] | DisclosureEnvelope,
                    content: Optional[str] = None) -> dict[str, Any]:
    """Verify an envelope's signature and (optionally) that ``content`` matches
    its hash. Returns ``{signature_ok, content_ok, profile, stale_profile,
    reasons}``. Never raises — verification of untrusted input must not crash.
    """
    d = envelope.to_dict() if isinstance(envelope, DisclosureEnvelope) else dict(envelope)
    reasons: list[str] = []
    # Reconstruct the signing payload from the declared fields.
    env = DisclosureEnvelope(
        content_hash=d.get("content_hash", ""),
        originating_system=d.get("originating_system", ""),
        affected_parties=d.get("affected_parties", []) or [],
        action_class=d.get("action_class", ""),
        marking=d.get("marking", {}) or {},
        created_at=d.get("created_at", ""),
        envelope_version=int(d.get("envelope_version", ENVELOPE_VERSION)),
        meta=d.get("meta", {}) or {})
    sig = d.get("signature", "")
    pem = d.get("public_key_pem", "")
    signature_ok = False
    if not sig:
        reasons.append("no signature present")
    else:
        try:
            pub = None
            if pem:
                from cryptography.hazmat.primitives import serialization
                pub = serialization.load_pem_public_key(pem.encode("utf-8"))
            signature_ok = signing.verify_signature(env.signing_payload(), sig,
                                                     public_key=pub)
            if not signature_ok:
                reasons.append("signature does not verify against payload")
        except Exception as exc:                         # noqa: BLE001
            reasons.append(f"signature verification error: {exc}")
    content_ok: Optional[bool] = None
    if content is not None:
        content_ok = (_content_hash(content) == env.content_hash)
        if not content_ok:
            reasons.append("content hash does not match envelope")
    profile = (env.marking or {}).get("profile", "")
    stale = bool(profile) and profile != MARKING_PROFILE
    if stale:
        reasons.append(f"marking profile {profile!r} is not the current "
                       f"{MARKING_PROFILE!r} (re-mark when the governing "
                       f"marking standard is finalised)")
    return {"signature_ok": signature_ok, "content_ok": content_ok,
            "profile": profile, "stale_profile": stale, "reasons": reasons}
