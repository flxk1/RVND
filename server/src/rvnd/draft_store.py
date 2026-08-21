# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Draft store — per-workspace persistence for authoring working state.

Drafts are unsigned working state (pasted policy text, map view, in-progress
cards, officer roster edits, chat transcript) that would otherwise live only in
the DOM and evaporate on reload. They are not governed acts: no chain event is
written on save (the commit act — apply a twin, register a party, save a card —
gets its audit when it happens). Files live beside the chain at
``<log_root>/<folder_hash>/drafts/<surface>.json``, one file per surface from a
fixed whitelist, so sealing a workspace covers them and session capture reads
them server-side. Writes refuse on a sealed workspace, an unknown surface, or
an oversize payload; a corrupt file is reported and preserved, never replaced.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Optional

from . import seal
from .mutation_log import LOG_ROOT_DEFAULT, folder_hash
from .redaction import count_ci, redact_value, walk_strings

#: The draftable surfaces. Fixed whitelist: the surface name becomes a file
#: name, so this doubles as the path-traversal guard. ``officers`` is reserved
#: for a roster editor; capture/restore round-trip it like any other surface.
SURFACES = ("policy_paste", "map", "cards", "officers", "chat")

#: Per-surface cap on the canonical JSON payload. Drafts are scratch state,
#: not a document store; a legible refusal beats an unbounded file.
MAX_PAYLOAD_BYTES = 512 * 1024


def drafts_dir(folder_context: str | Path,
               log_root: Optional[str | Path] = None) -> Path:
    """The workspace's drafts directory, beside its chain log."""
    resolved = str(Path(folder_context).expanduser().resolve())
    root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
    return root / folder_hash(resolved) / "drafts"


def draft_path(folder_context: str | Path, surface: str,
               log_root: Optional[str | Path] = None) -> Path:
    return drafts_dir(folder_context, log_root) / f"{surface}.json"


def _refuse(error: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, **extra}


def _check_surface(surface: str) -> Optional[dict[str, Any]]:
    if surface not in SURFACES:
        return _refuse(f"unknown draft surface {surface!r} — one of {list(SURFACES)}")
    return None


def _check_unsealed(folder_context: str | Path,
                    log_root: Optional[str | Path]) -> Optional[dict[str, Any]]:
    if seal.is_sealed(folder_context, log_root=log_root):
        return _refuse("workspace is sealed — unseal it to touch drafts")
    return None


def save(folder_context: str | Path, surface: str, payload: dict,
         *, log_root: Optional[str | Path] = None) -> dict[str, Any]:
    """Persist one surface's draft payload. Atomic write, no chain event.

    Returns ``{ok, surface, path, updated}``; refuses (``ok=False``) on an
    unknown surface, a non-dict payload, a sealed workspace, or a payload
    over :data:`MAX_PAYLOAD_BYTES`.
    """
    bad = _check_surface(surface) or _check_unsealed(folder_context, log_root)
    if bad:
        return bad
    if not isinstance(payload, dict):
        return _refuse("draft payload must be a JSON object")
    try:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as e:
        return _refuse(f"draft payload is not JSON-serializable: {e}")
    size = len(body.encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        return _refuse(f"draft payload is {size} bytes — the per-surface cap "
                       f"is {MAX_PAYLOAD_BYTES}")
    updated = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path = draft_path(folder_context, surface, log_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"surface": surface, "updated": updated, "payload": payload}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(envelope, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)
    return {"ok": True, "surface": surface, "path": str(path), "updated": updated}


def _read_surface(folder_context: str | Path, surface: str,
                  log_root: Optional[str | Path]) -> dict[str, Any]:
    """One surface, structured: missing is an empty draft, corrupt is a
    located refusal with the file preserved (``discard`` is the recovery)."""
    path = draft_path(folder_context, surface, log_root)
    if not path.exists():
        return {"ok": True, "surface": surface, "payload": {}, "updated": ""}
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        payload = envelope["payload"]
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except Exception:
        return _refuse("unreadable draft — discard it or restore the file",
                       surface=surface, path=str(path))
    return {"ok": True, "surface": surface, "payload": payload,
            "updated": str(envelope.get("updated", ""))}


def load(folder_context: str | Path, surface: Optional[str] = None,
         *, log_root: Optional[str | Path] = None) -> dict[str, Any]:
    """Read one surface, or all of them when ``surface`` is omitted.

    All-surfaces shape: ``{ok, drafts: {surface: payload}, updated: {surface:
    ts}, unreadable: {surface: path}}`` — corrupt files are named, never
    silently dropped, and never block the readable ones.
    """
    bad = _check_unsealed(folder_context, log_root)
    if bad:
        return bad
    if surface is not None:
        return _check_surface(surface) or _read_surface(folder_context, surface, log_root)
    drafts: dict[str, dict] = {}
    updated: dict[str, str] = {}
    unreadable: dict[str, str] = {}
    for s in SURFACES:
        r = _read_surface(folder_context, s, log_root)
        if not r["ok"]:
            unreadable[s] = r["path"]
        elif r["payload"]:
            drafts[s] = r["payload"]
            updated[s] = r["updated"]
    return {"ok": True, "drafts": drafts, "updated": updated,
            "unreadable": unreadable}


def load_all(folder_context: str | Path,
             *, log_root: Optional[str | Path] = None) -> dict[str, dict]:
    """Readable payloads by surface — the capture-side view (session bundle).
    Corrupt or sealed drafts are omitted here; ``load`` names them."""
    if seal.is_sealed(folder_context, log_root=log_root):
        return {}
    out: dict[str, dict] = {}
    for s in SURFACES:
        r = _read_surface(folder_context, s, log_root)
        if r["ok"] and r["payload"]:
            out[s] = r["payload"]
    return out


def discard(folder_context: str | Path, surface: Optional[str] = None,
            *, log_root: Optional[str | Path] = None) -> dict[str, Any]:
    """Delete one surface's draft file, or every surface's when omitted.
    Idempotent; the explicit recovery for a corrupt draft."""
    bad = _check_unsealed(folder_context, log_root)
    if bad:
        return bad
    if surface is not None:
        bad = _check_surface(surface)
        if bad:
            return bad
    targets = [surface] if surface else list(SURFACES)
    discarded: list[str] = []
    for s in targets:
        path = draft_path(folder_context, s, log_root)
        if path.exists():
            path.unlink()
            discarded.append(s)
    return {"ok": True, "discarded": discarded}


# ---------------------------------------------------------------------------
# Erasure hooks — drafts are free text and must not outlive an erased subject.
# The string walking/rewriting itself lives in ``redaction`` (shared with the
# card store) so every erasable store counts and rewrites identically.
# ---------------------------------------------------------------------------

def scan(folder_context: str | Path, subject: str,
         *, log_root: Optional[str | Path] = None) -> dict[str, Any]:
    """Erasure preview: subject occurrences per surface, plus the files that
    cannot be certified clean of the subject (unparseable drafts, stale
    rewrite scratch a crash left beside them) — those are deleted on
    :func:`redact`. A sealed workspace cannot be inspected: ``sealed=True``
    with empty hits means the drafts are unknown, not clean — callers must
    report the blind spot."""
    needle = (subject or "").strip().lower()
    hits: dict[str, int] = {}
    unreadable: list[str] = []
    if seal.is_sealed(folder_context, log_root=log_root):
        return {"hits": hits, "unreadable": unreadable, "sealed": True}
    if not needle:
        return {"hits": hits, "unreadable": unreadable, "sealed": False}
    d = drafts_dir(folder_context, log_root)
    if d.is_dir():
        for path in sorted(d.glob("*.json.tmp")):
            unreadable.append(path.name)
    for s in SURFACES:
        r = _read_surface(folder_context, s, log_root)
        if not r["ok"]:
            unreadable.append(s)
            continue
        n = sum(count_ci(t, needle) for t in walk_strings(r["payload"]))
        if n:
            hits[s] = n
    return {"hits": hits, "unreadable": unreadable, "sealed": False}


def redact(folder_context: str | Path, subject: str,
           *, log_root: Optional[str | Path] = None) -> dict[str, Any]:
    """Erasure execute: rewrite every string occurrence of the subject to
    ``[REDACTED]`` across all surfaces; delete unparseable draft files and
    stale rewrite scratch (neither can be certified clean of the subject).
    Returns ``{ok, redacted: {surface: count}, deleted: [surface]}``."""
    bad = _check_unsealed(folder_context, log_root)
    if bad:
        return bad
    needle = (subject or "").strip()
    if not needle:
        return _refuse("erasure subject must be non-empty")
    redacted: dict[str, int] = {}
    deleted: list[str] = []
    d = drafts_dir(folder_context, log_root)
    if d.is_dir():
        for path in sorted(d.glob("*.json.tmp")):
            path.unlink()
            deleted.append(path.name)
    for s in SURFACES:
        path = draft_path(folder_context, s, log_root)
        if not path.exists():
            continue
        r = _read_surface(folder_context, s, log_root)
        if not r["ok"]:
            path.unlink()
            deleted.append(s)
            continue
        payload, n = redact_value(r["payload"], needle)
        if n:
            written = save(folder_context, s, payload, log_root=log_root)
            if not written["ok"]:
                # Fail closed toward erasure: a draft that cannot be
                # rewritten clean does not get to keep the subject.
                path.unlink()
                deleted.append(s)
                continue
            redacted[s] = n
    return {"ok": True, "redacted": redacted, "deleted": deleted}
