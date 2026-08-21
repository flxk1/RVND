# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Agent public-key registry — the trust root for VERIFIED agent identity.

Internal by design: this is the key store the Web Bot Auth / RFC 9421 verifier
(:mod:`rvnd.web_bot_auth`) resolves a ``keyid`` against; it is not an MCP
operation. It maps an agent identity to the Ed25519 public key(s) that agent
signs its requests with, so a per-request ``Signature-Agent`` claim can be
CRYPTOGRAPHICALLY verified rather than merely declared (the escalate-only-safe
declared layer is the egress proxy's ``request_agent_identity``; this upgrades
it to proof).

Local-first, mirroring :mod:`rvnd.connected_agents`: a small directory of
per-key files under ``~/.workspace/agents/keys/`` (override with
``WORKSPACE_AGENTS_DIR`` or the ``root`` argument). One file per key, so rotation
is additive and revocation is a single-file edit. A key is LIVE when it is not
revoked and not past its ``expires``. Only PUBLIC keys are ever stored — a
private key never touches this registry.

This records the PRESENCE OF A KEY, not authority: what a verified agent may then
DO stays the per-folder chain's concern (its governance lane). Verification only
answers "is this really that agent?", never "may it act?".
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

# A keyid is base64url(sha256(raw pubkey)) — filename-safe by construction. On
# LOOKUP the value is untrusted (it arrives in a request signature), so it is
# validated against this shape before it can ever touch the filesystem.
_KEYID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _keys_dir(root: Optional[str] = None) -> Path:
    """The agent-key directory. ``root`` (tests) or ``WORKSPACE_AGENTS_DIR`` wins;
    otherwise it sits beside ``connected/`` under ``~/.workspace/agents/``."""
    if root:
        base = Path(root)
    else:
        env = os.environ.get("WORKSPACE_AGENTS_DIR")
        base = Path(env) if env else Path.home() / ".workspace" / "agents"
    return base / "keys"


def _safe_keyid(keyid: str) -> str:
    """Return ``keyid`` iff it matches the keyid shape, else ``""`` — so an
    attacker-supplied keyid can never escape the key directory (path traversal)."""
    keyid = (keyid or "").strip()
    return keyid if _KEYID_RE.fullmatch(keyid) else ""


def _load_ed25519_public(pem):
    """Load a PEM public key, requiring Ed25519. Raises ``ValueError`` otherwise —
    the registry's trust root must be exactly the algorithm the verifier checks."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    data = pem.encode("utf-8") if isinstance(pem, str) else pem
    try:
        key = load_pem_public_key(data)
    except Exception as exc:
        raise ValueError(f"not a valid PEM public key: {exc}") from None
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("agent identity keys must be Ed25519")
    return key


def key_id_for(pem) -> str:
    """Stable thumbprint of an Ed25519 public key: ``base64url(sha256(raw))`` with
    no padding. Independent of PEM whitespace, deterministic, filename-safe."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    key = _load_ed25519_public(pem)
    raw = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")


def _is_live(rec: dict, now: float) -> bool:
    if rec.get("revoked"):
        return False
    exp = rec.get("expires")
    return not (exp is not None and now > float(exp))


def register_agent_key(agent: str, public_key_pem, *,
                       expires: Optional[float] = None,
                       now: Optional[float] = None,
                       root: Optional[str] = None) -> dict:
    """Register an agent's Ed25519 public key; return the stored record.

    An explicit admin action, so it FAILS CLOSED by raising ``ValueError`` on an
    empty agent id or a bad/non-Ed25519 key — a malformed trust root must never be
    silently stored. Rotation is additive: a new key is a new keyid is a new file,
    leaving the prior key live until it is revoked or expires.
    """
    agent = (agent or "").strip()
    if not agent:
        raise ValueError("agent id is required")
    _load_ed25519_public(public_key_pem)                  # validate (raises on bad)
    keyid = key_id_for(public_key_pem)
    pem_str = (public_key_pem if isinstance(public_key_pem, str)
               else public_key_pem.decode("utf-8"))
    rec = {
        "agent": agent,
        "keyid": keyid,
        "alg": "ed25519",
        "public_key_pem": pem_str,
        "created": float(now if now is not None else time.time()),
        "expires": (float(expires) if expires is not None else None),
        "revoked": False,
    }
    d = _keys_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{keyid}.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def get_agent_key(keyid: str, *, now: Optional[float] = None,
                  root: Optional[str] = None) -> Optional[dict]:
    """Return the LIVE key record for ``keyid`` (not revoked, not expired) or
    ``None``. Read-only and never raises — a missing, corrupt, or dead record, or
    an unsafe keyid, all resolve to ``None`` (fail-closed)."""
    kid = _safe_keyid(keyid)
    if not kid:
        return None
    now = float(now if now is not None else time.time())
    try:
        f = _keys_dir(root) / f"{kid}.json"
        if not f.exists():
            return None
        rec = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    return rec if _is_live(rec, now) else None


def list_agent_keys(*, agent: Optional[str] = None, include_dead: bool = False,
                    now: Optional[float] = None,
                    root: Optional[str] = None) -> list[dict]:
    """Registered keys, newest first. Live-only unless ``include_dead``; optionally
    filtered to one ``agent``. Read-only projection; never raises."""
    now = float(now if now is not None else time.time())
    out: list[dict] = []
    try:
        d = _keys_dir(root)
        if not d.exists():
            return []
        for f in d.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if agent is not None and rec.get("agent") != agent:
                continue
            if not include_dead and not _is_live(rec, now):
                continue
            out.append(rec)
    except Exception:
        return out
    out.sort(key=lambda r: r.get("created", 0), reverse=True)
    return out


def revoke_agent_key(keyid: str, *, root: Optional[str] = None) -> bool:
    """Mark a key revoked (kept for audit, not deleted). Returns ``True`` if a
    record was found and revoked, else ``False``. Never raises."""
    kid = _safe_keyid(keyid)
    if not kid:
        return False
    try:
        f = _keys_dir(root) / f"{kid}.json"
        if not f.exists():
            return False
        rec = json.loads(f.read_text(encoding="utf-8"))
        rec["revoked"] = True
        f.write_text(json.dumps(rec), encoding="utf-8")
        return True
    except Exception:
        return False
