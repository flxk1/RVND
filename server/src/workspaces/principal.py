# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Request principal — who is calling, and what that entitles them to.

A leaf module by design. These helpers were part of ``mcp_serving``, which
also carries the lock/view/fingerprint helpers; modules that needed only the
principal helpers had to import the whole serving layer, and three of them
(``cross_workspace``, ``workspace_registry``, ``session_admission``) are
themselves imported by it. That closed an import cycle each.

Nothing here imports another first-party module at module scope, so it cannot
participate in a cycle. Keep it that way: a module-level import added below is
a cycle waiting to reappear.

``mcp_serving`` re-exports every public name here, so existing importers and
``workspaces.mcp_serving.<name>`` patch targets keep working.
"""

from __future__ import annotations

import os
from pathlib import Path


def _log_root() -> Path | None:
    """Operator-set log root, or None.

    Lives here, not in ``mcp_serving``, because ``principal_workspace_member``
    below calls it -- when it lived there and this module did not, the call
    raised NameError, the fail-closed handler swallowed it, and every party
    silently read as a non-member of every workspace.
    """
    v = os.environ.get("WORKSPACE_L0_LOG_ROOT")
    return Path(v) if v else None

# ---------------------------------------------------------------------------
# Request principal. In a deployment, identity rung 2 means a trusted fronting
# proxy verified who is calling. The loopback-only app may instead bind the
# agent for one governance_open call to its authenticated local bridge session.
# Both are set inside the request thread and cleared in its finally block; the
# MCP-native path never sets either. contextvars are per-thread, matching the
# bridge's thread-per-request model.
# ---------------------------------------------------------------------------
import contextvars as _contextvars

_request_principal: _contextvars.ContextVar[dict | None] = \
    _contextvars.ContextVar("request_principal", default=None)


def set_request_principal(principal: str, party: str | None,
                          rung: str = "proxy-verified") -> None:
    """The bridge verified ``principal`` via its declared trusted header and
    resolved it (or failed to) against the party registry. ``party`` is None
    when unresolved — governed (actor-accepting) operations then refuse."""
    _request_principal.set({"principal": principal, "party": party,
                            "rung": rung if party else ""})


def get_request_principal() -> dict | None:
    return _request_principal.get()


def clear_request_principal() -> None:
    _request_principal.set(None)


# Param keys by which an operation names the folder it acts on. An unresolved
# principal is refused any operation carrying one of these, reads included. The
# filesystem browser addresses by ``path``; older facades used ``folder`` before
# the rename to ``folder_context``. All three must gate, or a rename or a
# differently-named param reopens the fail-open.
_FOLDER_ADDRESSING_KEYS = ("folder_context", "folder", "path")

# Server-owned storage roots are deployment configuration, never request
# parameters.  The Python API deliberately keeps these arguments for local
# single-operator use and tests, but a browser/MCP request behind a verified
# proxy must not redirect journals, registries, keys, or stores elsewhere on
# the host.  Reject presence rather than truthiness so ``null`` and ``""`` do
# not create a second, subtly different request contract.
_REMOTE_STORAGE_ROOT_KEYS = ("log_root", "user_root", "store_root", "key_dir")


def apply_principal_to_params(fn, params: dict) -> dict | None:
    """Enforcement for one facade operation under an active request principal.

    With a resolved party, an operation that accepts an ``actor`` parameter
    gets the party injected — a client-sent actor is overridden, the proxy's
    word beats the browser's. A caller that cannot name the target function
    (a facade that dispatches internally) passes ``fn=None`` and gets the
    injection unconditionally; operations without an actor ignore the key.

    With an unresolved principal, enforcement is fail-closed: any operation
    addressed to a folder is refused, reads included — an unmatched principal
    reads nothing in that workspace, never everything — and an actor-accepting
    operation is refused even without a folder. A folder is addressed by any of
    the keys in ``_FOLDER_ADDRESSING_KEYS``; keying the refusal on the sole name
    ``folder_context`` let path- and folder-addressed reads (the filesystem
    browser, card ops before their rename) slip past the gate. Returns a refusal
    dict or None. Without a request principal (local single-operator mode)
    nothing changes."""
    ctx = get_request_principal()
    if ctx is None:
        return None
    supplied_roots = sorted(set(params or {}).intersection(_REMOTE_STORAGE_ROOT_KEYS))
    if supplied_roots:
        return {
            "ok": False,
            "error": "server-owned storage roots cannot be overridden by a remote request",
            "refused_params": supplied_roots,
        }
    if fn is None:
        accepts_actor = True
    else:
        import inspect
        try:
            accepts_actor = "actor" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            accepts_actor = False
    if ctx.get("party"):
        if accepts_actor:
            params["actor"] = ctx["party"]
        return None
    addresses_folder = any((params or {}).get(k) for k in _FOLDER_ADDRESSING_KEYS)
    if addresses_folder or (fn is not None and accepts_actor):
        return {"ok": False,
                "error": f"principal {ctx.get('principal')!r} is not a"
                         " registered party in this workspace — the operation"
                         " is refused, reads included (fail-closed: an"
                         " unmatched principal reads nothing here, never"
                         " everything). Register the party or map its group"
                         " (identity map)."}
    return None


def principal_workspace_member(principal: str, folder: str,
                               log_root: str | Path | None = None) -> bool:
    """Membership test behind per-principal read scoping: true iff the
    workspace folder's party registry carries an active party whose
    ``party_id`` equals the principal string exactly. A suspended or killed
    party is not a member; any error reading the registry counts as
    non-membership (fail-closed)."""
    if not principal or not folder:
        return False
    try:
        from .parties import list_parties
        roster = list_parties(str(folder),
                              log_root=str(log_root) if log_root is not None
                              else (str(_log_root()) if _log_root() else None))
        return any(p.get("party_id") == principal
                   and p.get("status", "active") == "active"
                   for p in roster.get("parties", []))
    except (NameError, AttributeError):
        # Fail-closed is for an unreadable REGISTRY, not for a bug in this
        # function. Swallowing these once turned a missing `_log_root` into a
        # silent "nobody is a member of anything", which reads exactly like a
        # correct deny. Let a defect crash instead of impersonating a decision.
        raise
    except Exception:
        return False


def principal_member_filter():
    """Per-request membership predicate for operations that span workspaces
    without a ``folder_context`` (queue listings, run_id-addressed run
    lifecycle, stuck-run scans). Returns None without a request principal
    (local single-operator mode: nothing is filtered). Otherwise returns
    ``member(folder) -> bool`` bound to the request principal, caching one
    registry lookup per folder for the duration of the call. Fail-closed:
    an unmatched principal is a member of no workspace."""
    ctx = get_request_principal()
    if ctx is None:
        return None
    principal = ctx.get("principal") or ""
    cache: dict[str, bool] = {}

    def member(folder: str) -> bool:
        key = str(folder or "")
        if key not in cache:
            cache[key] = principal_workspace_member(principal, key)
        return cache[key]

    return member


# ---------------------------------------------------------------------------
# Console units by party role. The console asks (via /whoami) which of the
# five units — chat, patchbay, mixdesk, screen, sign-off widget — a party's
# role warrants rendering. Chrome gating is comfort, not protection: read
# scoping and the write gates above are the enforcement; hiding a frame never
# substitutes for the server refusing the call.
# ---------------------------------------------------------------------------
CONSOLE_UNITS: tuple[str, ...] = ("chat", "mixdesk", "patchbay", "screen",
                                  "widget")

_ROLE_UNITS: dict[str, tuple[str, ...]] = {
    "owner":    CONSOLE_UNITS,
    "builder":  ("patchbay", "chat"),
    "operator": ("mixdesk", "screen"),
    "auditor":  ("screen",),
    "approver": ("widget",),
}


def units_for_role(role: str | None) -> list[str]:
    """Console units the given party role warrants. An unknown or absent
    role warrants none (fail-closed chrome). Local single-operator mode
    never asks per role — the bridge answers all units there."""
    return list(_ROLE_UNITS.get((role or "").strip().lower(), ()))
