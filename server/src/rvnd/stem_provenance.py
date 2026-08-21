# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Stem provenance — the production log on the signed chain.

Concept § 1.7 / design § 1: which stems were played, generated, or hybrid,
by whom, with which tool — recorded as chain events (no new store), and
projected into an authorship-evidence report (the provenance premium).
The report is evidence of the production process; whether a work is
copyrightable is the rights-holder's call, never this module's claim.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog

ORIGIN_VALUES = ("played", "generated", "hybrid")
"""played = human performance; generated = AI tool output; hybrid = both."""


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_stem(
    folder_context: str,
    file_path: str,
    origin: str,
    tool_id: str = "",
    actor: str = "user",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Record one stem's provenance on the folder's chain.

    ``origin`` must be one of ``played | generated | hybrid``; ``tool_id``
    names the instrument/plugin/model for generated and hybrid stems.
    """
    if origin not in ORIGIN_VALUES:
        raise ValueError(
            f"origin must be one of {ORIGIN_VALUES}, got {origin!r}")
    p = Path(file_path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"not a file: {file_path}")
    stem_hash = _hash_file(p)
    log = MutationLog(folder_context, log_root=log_root)
    audit_id = log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"stem:{stem_hash[:16]}",
        channel="system",
        actor=actor,
        extra={
            "kind":      "StemIngested",
            "stem_hash": stem_hash,
            "origin":    origin,
            "tool_id":   tool_id,
            "file_name": p.name,
        },
    ))
    return {"ok": True, "stem_hash": stem_hash, "origin": origin,
            "audit_id": audit_id}


def assemble_work(
    folder_context: str,
    work_id: str,
    stem_hashes: list[str],
    actor: str = "user",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Record that a work was assembled from the named stems."""
    if not work_id or not stem_hashes:
        raise ValueError("work_id and a non-empty stem_hashes list required")
    log = MutationLog(folder_context, log_root=log_root)
    audit_id = log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"work:{work_id}",
        channel="system",
        actor=actor,
        extra={
            "kind":        "WorkAssembled",
            "work_id":     work_id,
            "stem_hashes": list(stem_hashes),
        },
    ))
    return {"ok": True, "work_id": work_id, "stems": len(stem_hashes),
            "audit_id": audit_id}


def authorship_evidence(
    folder_context: str,
    work_id: str,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Project the chain into a per-work authorship-evidence report.

    Origin shares are computed over the work's stems; a stem hash with no
    ``StemIngested`` event is reported as ``unknown`` rather than guessed.
    The latest ``WorkAssembled`` event for the work wins (re-assembly is
    a new event, never an edit).
    """
    log = MutationLog(folder_context, log_root=log_root)
    stems_seen: dict[str, dict[str, Any]] = {}
    assembly: dict[str, Any] | None = None
    assembly_audit = ""
    for evt in log.replay():
        extra = evt.extra or {}
        kind = extra.get("kind")
        if kind == "StemIngested":
            stems_seen[str(extra.get("stem_hash", ""))] = {
                "origin":  extra.get("origin", "unknown"),
                "tool_id": extra.get("tool_id", ""),
                "file_name": extra.get("file_name", ""),
            }
        elif kind == "WorkAssembled" and extra.get("work_id") == work_id:
            assembly = extra
            assembly_audit = evt.audit_id
    if assembly is None:
        return {"ok": False, "work_id": work_id,
                "reason": "no WorkAssembled event for this work_id"}

    stems: list[dict[str, Any]] = []
    counts = {"played": 0, "generated": 0, "hybrid": 0, "unknown": 0}
    for h in assembly.get("stem_hashes", []):
        info = stems_seen.get(h)
        origin = info["origin"] if info else "unknown"
        counts[origin] = counts.get(origin, 0) + 1
        stems.append({"stem_hash": h, "origin": origin,
                      "tool_id": (info or {}).get("tool_id", ""),
                      "file_name": (info or {}).get("file_name", "")})
    total = max(len(stems), 1)
    shares = {k: round(v / total, 4) for k, v in counts.items()}
    return {
        "ok": True,
        "work_id": work_id,
        "stems": stems,
        "shares": shares,
        "assembled_audit_id": assembly_audit,
        "statement": (
            "Evidence of the production process as recorded on the signed "
            "chain. Not a determination of authorship or copyright — that "
            "assessment is the rights-holder's."
        ),
    }
