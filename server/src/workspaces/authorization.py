# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""check_access — opt-in, fail-closed authorization for governed reads (M5/A6).

The audit (A6) flagged that reads of governed state were not access-controlled: any
caller with a folder path could read everything. A single-operator, local-first
folder does not need RBAC, so enforcement is OPT-IN per folder
(``policy.access_control_enabled``):

  * OFF (the default) — every read is permitted. The operator owns the folder; the
    `actor` is an audit label, as before. No behaviour change for local-first use.
  * ON — a read by a NAMED party is permitted only if that party is registered,
    active, and (when required) holds the competence; an unknown/suspended/unnamed
    actor is DENIED. The runtime's own principals (the builtin actors + the
    configured default MCP actor) pass — they are the system, not external callers.

Fail-closed: if the policy or party register cannot be read, we cannot verify the
actor is authorized, so access is DENIED — never defaulted open.
"""
from __future__ import annotations

from typing import Optional


def _runtime_principals() -> set[str]:
    """The non-external principals that always pass an access check: the builtin
    actors plus the configured default MCP actor (the runtime itself)."""
    import os
    from .parties import BUILTIN_ACTORS
    principals = set(BUILTIN_ACTORS)
    principals.add(os.environ.get("WORKSPACE_L0_DEFAULT_ACTOR", "").strip() or "mcp:l0")
    return principals


def access_control_on(folder_context: str) -> bool:
    """True when the folder has opted into access control. Fail-closed: a policy
    that is PRESENT but cannot be read/parsed is treated as access-control-ON, so a
    corrupt or unverifiable policy gates rather than silently dropping the gate. An
    ABSENT policy is the legitimate local-first default (OFF)."""
    try:
        from .policy import load_policy, POLICY_FILENAME, LEGACY_POLICY_FILENAME
        import json
        from pathlib import Path
        folder = Path(folder_context).expanduser().resolve()
        for fn in (POLICY_FILENAME, LEGACY_POLICY_FILENAME):
            p = folder / fn
            if p.exists():
                try:
                    if not isinstance(json.loads(p.read_text(encoding="utf-8")), dict):
                        return True   # present but not a JSON object → unverifiable → fail-closed
                except Exception:
                    return True       # present but unreadable/unparseable → fail-closed
                break
        return bool(getattr(load_policy(folder_context), "access_control_enabled", False))
    except Exception:
        return True


def check_access(folder_context: str, actor: str, action: str = "read", *,
                 competence: Optional[str] = None,
                 log_root: Optional[str] = None) -> bool:
    """Fail-closed authorization gate for a governed read/write.

    Returns True when access control is OFF for the folder (the local-first
    default), or when ON and ``actor`` is a runtime principal, or a registered,
    active party that holds ``competence`` (if required). Returns False otherwise —
    including any error reading the policy or the party register (fail-closed)."""
    if action not in ("read", "write", "delete"):
        return False
    # Opt-in: a folder that has not enabled access control permits everything. A
    # policy we cannot read is treated as ON (gated), per access_control_on.
    try:
        from .policy import load_policy
        enabled = bool(getattr(load_policy(folder_context), "access_control_enabled", False))
    except Exception:
        return False                       # cannot verify → fail closed
    if not enabled:
        return True

    a = (actor or "").strip()
    if not a:
        return False                       # access control ON requires a named actor
    if a in _runtime_principals():
        return True
    try:
        from .parties import list_parties
        parties = list_parties(folder_context, log_root=log_root).get("parties", [])
        # The lookup is INSIDE the try: a corrupt register (a non-dict row) must
        # fail closed (return False), never crash the caller.
        party = next((p for p in parties
                      if isinstance(p, dict) and p.get("party_id") == a), None)
        if party is None or party.get("status") != "active":
            return False
        if competence and competence not in (party.get("competences") or []):
            return False
        return True
    except Exception:
        return False                       # cannot verify the register → fail closed
