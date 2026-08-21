# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""credential_resolver — resolve an egress track's credential *reference* to the
secret, at call time, fail-closed.

Per-track binding: the signed chain
stores only a *reference* (``env:JIRA_TOKEN``), never the secret. The secret is
resolved here at the moment of egress and injected by the broker; it is never
persisted, never logged, and never returned by any status/projection path — only
``resolve_secret`` returns it, and only for immediate injection at the boundary.

Schemes::

    env:NAME        read from the process environment (local-first default)
    keydir:relpath  a file under WORKSPACE_KEY_DIR, must be regular + mode 0600
    oidc:realm      resolved through an IdP adapter (enterprise; no adapter yet)
    spiffe://…      workload identity (no adapter yet)

Arm status — what the channel-strip LED shows, never the secret::

    no_cable   no reference at all — fail-closed, "cannot reach outside"
    armed      the reference resolves to a present secret
    unplugged  a reference is set but does NOT resolve (missing env / bad file
               mode / unknown scheme / no adapter) — revoked / unresolvable,
               fail-closed

Security invariants:
  * a malformed or unknown-scheme ref is NEVER treated as armed (fail-closed);
  * ``keydir:`` refuses a world/group-readable file (mode must be 0600) so a
    loose secret file cannot silently arm a track;
  * ``resolve_secret`` returns ``None`` (never raises with the value) whenever the
    status is not ``armed`` — the broker treats ``None`` as "refuse the call".
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional

# The schemes a reference may name. env/keydir resolve locally today; oidc/spiffe
# are valid references but have no adapter yet, so they resolve to `unplugged`
# (honest fail-closed) rather than being rejected at registration.
SCHEMES = ("env", "keydir", "oidc", "spiffe")
LOCAL_SCHEMES = ("env", "keydir")

NO_CABLE = "no_cable"
ARMED = "armed"
UNPLUGGED = "unplugged"


def parse_ref(ref: Optional[str]) -> Optional[tuple[str, str]]:
    """Split ``scheme:locator`` → ``(scheme, locator)``; ``None`` if malformed or
    the scheme is unknown. ``spiffe://…`` keeps its ``//`` in the locator."""
    if not ref or not isinstance(ref, str):
        return None
    s = ref.strip()
    if ":" not in s:
        return None
    scheme, locator = s.split(":", 1)
    scheme = scheme.strip().lower()
    locator = locator.strip()
    if scheme not in SCHEMES or not locator:
        return None
    return (scheme, locator)


def is_valid_ref(ref: Optional[str]) -> bool:
    """A syntactically valid, known-scheme reference (does NOT resolve it)."""
    return parse_ref(ref) is not None


def _key_root() -> Optional[Path]:
    """The base for ``keydir:`` refs — the same root signing uses. ``None`` if the
    default cannot be located (then keydir refs are unresolvable → fail-closed)."""
    try:
        from . import host_deps
        host_deps.ensure_wired()
        if host_deps.key_root_dir is None:
            raise LookupError("key-root hook not wired")
        return host_deps.key_root_dir()
    except Exception:
        override = os.environ.get("WORKSPACE_KEY_DIR")
        return Path(override).expanduser() if override else None


def _resolve_keydir(locator: str) -> Optional[str]:
    """Read a secret file under the key root. Fail-closed on: escape, missing,
    non-regular, or looser-than-0600 mode. Returns the stripped secret or None."""
    root = _key_root()
    if root is None:
        return None
    root = root.resolve()
    # No path escape: the resolved target must stay under the key root.
    target = (root / locator).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if not target.is_file():
        return None
    mode = stat.S_IMODE(target.stat().st_mode)
    if mode & 0o077:                       # any group/other bit set → refuse
        return None
    try:
        return target.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def resolve_secret(ref: Optional[str]) -> Optional[str]:
    """Resolve a reference to its secret for immediate injection, or ``None`` if it
    does not resolve (fail-closed). NEVER log the return value."""
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    scheme, locator = parsed
    if scheme == "env":
        val = os.environ.get(locator)
        return val if val else None
    if scheme == "keydir":
        return _resolve_keydir(locator)
    # oidc / spiffe — no adapter yet: unresolvable, fail-closed.
    return None


def arm_status(ref: Optional[str]) -> str:
    """The LED state for a track's egress cable — ``no_cable`` / ``armed`` /
    ``unplugged``. Resolves the ref but never exposes the secret."""
    if not (ref or "").strip():
        return NO_CABLE
    if parse_ref(ref) is None:
        return UNPLUGGED                   # malformed / unknown scheme → fail-closed
    return ARMED if resolve_secret(ref) is not None else UNPLUGGED


def describe(ref: Optional[str]) -> dict:
    """A secret-free description for the UI/projection: the reference, its scheme,
    and its arm status. Deliberately returns NO secret material."""
    parsed = parse_ref(ref)
    return {
        "credential_ref": (ref or "").strip() or None,
        "scheme": parsed[0] if parsed else None,
        "status": arm_status(ref),
        # whether RVND can actually inject this (local broker) vs merely attest it
        "enforceable": bool(parsed and parsed[0] in LOCAL_SCHEMES),
    }
