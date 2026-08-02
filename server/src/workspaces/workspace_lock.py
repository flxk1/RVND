# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace Lock — session unlock + served reads (the 'one wall' switch).

This is the runtime glue over :mod:`workspaces.seal`. The wall (Workspace Lock) has one
state per workspace:

- **locked** — the workspace is sealed on disk (ciphertext) and no session key is
  held; reads are refused until you unlock.
- **unlocked (this session)** — you supplied the passphrase once; the derived
  key lives in memory for the process lifetime, so Workspaces can *serve* the workspace's
  memory by decrypting in memory (read-through) without unsealing to disk and
  without re-deriving the key on every read.

The escape hatch for direct file access is the full ``seal.unseal_folder``
(passphrase-gated, writes plaintext back) — that is a separate, deliberate act.

Key handling: the session key is held only in this process's memory, never
written to disk; ``lock()`` (or process exit) drops it. This module deliberately
has no persistence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import seal

# folder-hash (the log-dir name) -> derived AES key. In-memory only.
_SESSION: dict[str, bytes] = {}


def _name(folder: str | Path, log_root: str | Path | None) -> str:
    return seal._resolve_log_dir(folder, log_root).name


def is_sealed(folder: str | Path, *, log_root: str | Path | None = None) -> bool:
    return seal.is_sealed(folder, log_root=log_root)


def is_unlocked(folder: str | Path, *, log_root: str | Path | None = None) -> bool:
    """True if a session key is held for this workspace (it can be served)."""
    return _name(folder, log_root) in _SESSION


def unlock(
    folder: str | Path, *, passphrase: str, log_root: str | Path | None = None,
) -> dict[str, Any]:
    """Verify the passphrase against the sealed blob and cache the derived key
    for this session. Raises :class:`seal.SealError` on wrong passphrase /
    not-sealed. Does NOT write plaintext to disk."""
    mapping, key = seal.open_sealed_store(
        folder, passphrase=passphrase, log_root=log_root)
    _SESSION[_name(folder, log_root)] = key
    return {"unlocked": True, "files": len(mapping)}


def lock(folder: str | Path, *, log_root: str | Path | None = None) -> dict[str, Any]:
    """Drop the cached session key (re-locks the workspace for reading)."""
    existed = _SESSION.pop(_name(folder, log_root), None) is not None
    return {"locked": True, "was_unlocked": existed}


def lock_all() -> int:
    """Drop every cached session key. Returns how many were held."""
    n = len(_SESSION)
    _SESSION.clear()
    return n


def state(folder: str | Path, *, log_root: str | Path | None = None) -> dict[str, Any]:
    """The workspace's wall state, for the UI / policy_snapshot."""
    sealed = is_sealed(folder, log_root=log_root)
    return {
        "sealed": sealed,
        "unlocked": is_unlocked(folder, log_root=log_root),
        # one switch: a sealed workspace's reads are mediated by Workspaces (egress still
        # gated); "wall down" is the deliberate full unseal.
        "wall": "up" if sealed else "down",
    }


def serve(folder: str | Path, *, log_root: str | Path | None = None) -> dict[str, bytes]:
    """Return the workspace's memory store as ``{relpath: bytes}`` for reading.

    - Not sealed → read straight from disk (normal case).
    - Sealed + unlocked → decrypt in memory with the cached session key.
    - Sealed + locked → refuse (caller must :func:`unlock` first).

    This is the single entry a read path calls so a sealed-but-unlocked workspace is
    served transparently while the disk stays ciphertext.
    """
    if not is_sealed(folder, log_root=log_root):
        log_dir = seal._resolve_log_dir(folder, log_root)
        if not log_dir.exists():
            return {}
        return {p.relative_to(log_dir).as_posix(): p.read_bytes()
                for p in sorted(log_dir.rglob("*")) if p.is_file()}
    key = _SESSION.get(_name(folder, log_root))
    if key is None:
        raise seal.SealError(
            "workspace is locked — unlock(folder, passphrase=…) to read it, "
            "or unseal it for direct access")
    mapping, _ = seal.open_sealed_store(folder, key=key, log_root=log_root)
    return mapping


def serve_file(
    folder: str | Path, relpath: str, *, log_root: str | Path | None = None,
) -> bytes:
    """Serve one file (e.g. ``"events.jsonl"``) from the workspace's store."""
    store = serve(folder, log_root=log_root)
    if relpath not in store:
        raise seal.SealError(f"{relpath!r} is not in the workspace's store")
    return store[relpath]


def replay(folder: str | Path, *, log_root: str | Path | None = None):
    """Iterate a workspace's LogEvents from its served store (sealed+unlocked or
    unsealed). Raises :class:`seal.SealError` if the workspace is sealed + locked."""
    from .mutation_log import events_from_bytes
    store = serve(folder, log_root=log_root)
    raw = store.get("events.jsonl")
    return events_from_bytes(raw) if raw else iter(())


def read_pairs(folder: str | Path, *, log_root: str | Path | None = None) -> dict:
    """Return ``{pair_id: pair}`` from the served chain (last-write-wins over
    pair-creating events). Serves a sealed+unlocked workspace from memory without
    unsealing to disk; reads an unsealed workspace from disk. Raises if locked.

    Note: this is the raw created/updated-pair view from the chain, not the
    full lifecycle projection (deletes/publishes) that ``WorkspaceMemory`` computes.
    """
    from .memory import _pair_from_event
    out: dict = {}
    for evt in replay(folder, log_root=log_root):
        pair = _pair_from_event(evt)
        if pair is not None:
            pid = pair.get("id") or (pair.get("problem") or {}).get("id")
            out[pid] = pair
    return out
