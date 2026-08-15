# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Effective-posture attestation (P0a).

Compute RVND's effective *enforcement posture* — the runtime value of the switches
that decide how strictly the engine enforces — and attest it onto the per-folder
signed chain, so a body of evidence records the configuration under which it was
produced. EVIDENCE, not control: nothing here reads or changes a verdict. The claim
is deliberately narrow — *the enforcement configuration under which this evidence was
recorded, unaltered and externally checkable* — never proof of what happened
(integrity, not veracity).

Composes on the ``enforcement-posture`` package (the DSSE / in-toto posture
predicate) with RVND's own Ed25519 signer and the RFC 8785 canonicaliser injected;
no posture logic is reimplemented here.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from enforcement_posture import (
    Control, EvidenceWindow, Posture, attest, posture_id, verify,
)
from rfc8785 import dumps as _canonicalize

from . import signing
from .mutation_log import LogEvent, MutationLog

ENGINE = "rvnd"
POSTURE_EVENT_KIND = "posture-attested"
_ALGORITHM = "ed25519"

# (folder_id, posture_id) pairs already attested in THIS process. Lets a hot-path
# caller (e.g. operate) invoke attest_posture on every run without re-scanning the
# chain after the first attestation per folder+posture. Cross-process idempotence
# still rests on the chain scan below.
_ATTESTED_THIS_PROCESS: set[tuple[str, str]] = set()


def _truthy(val: str) -> bool:
    return val.strip().lower() in ("1", "on", "true", "yes")


def effective_posture(*, now: Optional[str] = None) -> Posture:
    """The effective enforcement posture from the LIVE environment.

    Reads ``os.environ`` at call time — the POST-``setdefault`` runtime value, not
    the configured intent. ``hook.py`` force-sets ``WORKSPACES_ALLOW_UNREGISTERED=1``
    on its path; attesting that path must report ``folder_allowlist`` as DISABLED,
    else the attestation would launder the very gap it exists to expose. Each
    control's ``enabled=True`` denotes the stricter / enforcing state.
    """
    env = os.environ.get
    controls = (
        Control("folder_allowlist", enabled=env("WORKSPACES_ALLOW_UNREGISTERED") != "1"),
        Control("host_divergence", enabled=env("WORKSPACE_STRICT_HOST_DIVERGENCE") == "1"),
        Control("verified_egress", enabled=_truthy(env("RVND_REQUIRE_VERIFIED_EGRESS", ""))),
        Control("key_pinning", enabled=env("WORKSPACE_STRICT_KEY_PINNING") == "1"),
        Control("hook_enforce", enabled=env("RVND_HOOK_MODE", "enforce") == "enforce"),
        Control("egress_policy", enabled=_truthy(env("RVND_EGRESS_POLICY", ""))),
        Control("autonomy_ceiling", enabled=True, mode=env("RVND_AUTONOMY_GRADE", "L2")),
    )
    return Posture(
        engine=ENGINE, controls=controls,
        effective_from=now or datetime.now(timezone.utc).isoformat(),
    )


def _sign(message: bytes) -> bytes:
    """enforcement-posture ``sign`` contract (bytes -> bytes) over the chain Ed25519."""
    return bytes.fromhex(signing.sign_bytes(message))


def _verify_sig(message: bytes, signature: bytes) -> bool:
    """enforcement-posture ``verify_sig`` contract ((bytes, bytes) -> bool)."""
    try:
        return signing.verify_signature(message, signature.hex())
    except Exception:                       # noqa: BLE001 — fail closed to unverified
        return False


def current_posture_id() -> str:
    """Content-addressed id of the current effective posture (engine + controls)."""
    return posture_id(effective_posture(), canonicalize=_canonicalize)


def _last_attested_posture_id(log: MutationLog) -> Optional[str]:
    latest: Optional[str] = None
    for e in log.replay():
        x = e.extra or {}
        if x.get("kind") == POSTURE_EVENT_KIND:
            latest = x.get("posture_id")
    return latest


def attest_posture(folder: str | Path, *,
                   log_root: Optional[str | Path] = None) -> Optional[str]:
    """Attest the current effective posture onto the folder's signed chain.

    Idempotent: appends a ``posture-attested`` event only when the effective posture
    has changed since the last attestation (by content-addressed ``posture_id``);
    returns the new event's ``audit_id``, or ``None`` when nothing changed. EVIDENCE
    ONLY — reads and alters no verdict; the chain signs the event on append.
    """
    log = MutationLog(Path(folder), log_root=Path(log_root) if log_root else None)
    posture = effective_posture()
    pid = posture_id(posture, canonicalize=_canonicalize)
    memo_key = (log.folder_id, pid)
    if memo_key in _ATTESTED_THIS_PROCESS:
        return None                          # cheap: already attested this process
    if _last_attested_posture_id(log) == pid:
        _ATTESTED_THIS_PROCESS.add(memo_key)
        return None
    window = EvidenceWindow(
        log_id=log.folder_id, start=posture.effective_from,
        end=posture.effective_from, digest=log.head_hash(),
    )
    envelope = attest(
        posture, window, canonicalize=_canonicalize, sign=_sign,
        keyid=signing.public_key_fingerprint(), algorithm=_ALGORITHM,
    )
    audit_id = log.append(LogEvent(
        event="system", folder_path=str(folder), pair_id="posture:attest",
        channel="system", actor="system:posture",
        extra={"kind": POSTURE_EVENT_KIND, "posture_id": pid,
               "posture": posture.to_dict(), "envelope": envelope},
    ))
    _ATTESTED_THIS_PROCESS.add(memo_key)
    return audit_id


def verify_attestation(envelope: dict[str, Any]):
    """Offline re-verify a posture attestation envelope with RVND's active Ed25519
    key. (Verifies against the current key; cross-key lookup by ``keyid`` is future
    work — the ``keyid`` is recorded on every attestation for that.)"""
    return verify(envelope, canonicalize=_canonicalize, verify_sig=_verify_sig)
