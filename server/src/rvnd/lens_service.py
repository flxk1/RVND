# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""JSON-boundary service for the in-vivo Lens.

The domain logic lives in :mod:`lens` (pure dataclasses + decisions); this module
adapts it to the surface — dict-in / dict-out wrappers shared by the MCP facade
(``workspace_lens``) and the CLI (``workspaces lens``), plus a reader over the signed audit
log for the admission feed. One impl, two surfaces.

Grounding: USP-2 in-vivo oversight (whitepaper §2.4) — Workspace is a *guard, not a
teacher*. It admits / holds / rejects learning objects (default-deny), transports
human judgment as revocable precedent (stare decisis for agents), and treats
learning as deliberate drift bounded by an update budget. It computes no gradients.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .lens import (LearningObject, LearningScope, Precedent,
                   classify_admission, select_precedent, UpdateBudget)

OPS = ("classify", "select_precedent", "budget", "log",
       "precedent_declare", "precedent_revoke", "precedent_list",
       "budget_cap_get", "budget_cap_set", "help")

_ADMISSION_EVENTS = {"admit", "hold", "reject"}


def _budget_cap_path(folder: str | Path,
                     log_root: Optional[str | Path] = None) -> Path:
    from .mutation_log import folder_hash
    root = Path(log_root) if log_root else (Path.home() / ".workspace" / "log")
    return root / folder_hash(folder) / "lens-budget.json"


def budget_cap_get(folder: str | Path, *,
                   log_root: Optional[str | Path] = None) -> Optional[float]:
    """The per-folder update-budget cap, or None when unset."""
    import json
    p = _budget_cap_path(folder, log_root)
    if not p.exists():
        return None
    try:
        cap = json.loads(p.read_text(encoding="utf-8")).get("cap")
        return float(cap) if cap is not None else None
    except (ValueError, OSError):
        return None


def budget_cap_set(folder: str | Path, cap: float, *,
                   log_root: Optional[str | Path] = None) -> dict:
    """Set the per-folder cap (must be > 0)."""
    import json
    if cap <= 0:
        return {"error": "cap must be > 0"}
    p = _budget_cap_path(folder, log_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({"cap": float(cap)}), encoding="utf-8")
    tmp.replace(p)
    return {"folder": str(folder), "cap": float(cap)}


def _scope_from(d: Optional[dict]) -> LearningScope:
    d = d or {}
    return LearningScope(
        allow=frozenset(d.get("allow") or ()),
        aggregate_only=frozenset(d.get("aggregate_only") or ()),
        forbid=frozenset(d.get("forbid") or ()))


def classify(params: dict, *, log_root: Optional[str | Path] = None) -> dict:
    """admit / hold / reject one learning object against a scope (default-deny).

    Optionally record the verdict to the folder's signed audit chain when
    ``record`` is set with a ``folder_context`` — so "the agent learned X" is a
    first-class log event, never an untracked mutation.
    """
    conf = params.get("confidence")
    obj = LearningObject(
        cls=str(params.get("cls", "")),
        content_hash=str(params.get("content_hash", "")),
        source_actor=str(params.get("source_actor", "")),
        channel_token=str(params.get("channel_token", "")),
        signature=str(params.get("signature", "")),
        confidence=(float(conf) if conf is not None else None),
        magnitude=float(params.get("magnitude", 1.0)),
        payload_summary=str(params.get("payload_summary", "")))
    verdict = classify_admission(
        obj, _scope_from(params.get("scope")),
        confidence_floor=float(params.get("confidence_floor", 0.85)),
        known_teachers=params.get("known_teachers"))
    out = verdict.to_dict()
    out["content_hash"] = obj.content_hash
    if params.get("record") and params.get("folder_context"):
        from .oversight_log import record_admission
        out["audit"] = record_admission(
            verdict, folder=params["folder_context"],
            content_hash=obj.content_hash,
            actor=str(params.get("actor", "system")),
            log_root=log_root, extra={"magnitude": obj.magnitude})
    return out


def select(params: dict) -> dict:
    """Pick the applicable precedent with the highest similarity, or none. The
    caller supplies the similarity per candidate — the Lens owns no metric."""
    cands: list[tuple[Precedent, float]] = []
    for c in params.get("candidates") or []:
        cands.append((Precedent(
            id=str(c.get("id", "")),
            query_features=c.get("query_features") or {},
            chosen_option=str(c.get("chosen_option", "")),
            rationale=str(c.get("rationale", "")),
            actor=str(c.get("actor", "")),
            learnable=bool(c.get("learnable", False)),
            similarity_threshold=float(c.get("similarity_threshold", 0.9)),
            expires_at=c.get("expires_at"),
            revoked=bool(c.get("revoked", False))),
            float(c.get("similarity", 0.0))))
    picked = select_precedent(params.get("features") or {}, cands,
                              now=params.get("now"))
    if picked is None:
        return {"selected": None,
                "reason": "no applicable precedent (needs learnable, not "
                          "revoked/expired, similarity ≥ threshold)"}
    prec, sim = picked
    return {"selected": prec.to_dict(), "similarity": sim,
            "actor_stamp": prec.actor_stamp()}


def budget(params: dict, *, log_root: Optional[str | Path] = None) -> dict:
    """Replay admitted-learning magnitudes into a budget counter. Over the cap
    means the next admit must re-gate (a candidate substantial modification).
    ``cap`` may be passed explicitly or read from the folder's stored cap."""
    cap = params.get("cap")
    if cap is None and params.get("folder_context"):
        cap = budget_cap_get(params["folder_context"], log_root=log_root)
    cap = float(cap or 0.0)
    if cap <= 0:
        return {"error": "cap must be > 0 (pass cap or set one for the folder)"}
    b = UpdateBudget.from_admitted(cap, params.get("admitted") or [])
    return {"cap": cap, "spent": b.spent,
            "remaining": max(0.0, cap - b.spent),
            "over": b.spent > cap,
            "fraction": (b.spent / cap) if cap else 0.0}


def admission_log(folder: str | Path, *,
                  log_root: Optional[str | Path] = None,
                  limit: int = 50) -> dict:
    """Read learning-admission events (admit/hold/reject on ``learn:*`` pairs)
    for a folder — the audit feed behind the admission queue + precedent shelf."""
    from .mutation_log import MutationLog
    log = MutationLog(Path(folder),
                      log_root=Path(log_root) if log_root else None)
    rows: list[dict[str, Any]] = []
    for evt in log.replay():
        if evt.event not in _ADMISSION_EVENTS:
            continue
        if not str(evt.pair_id).startswith("learn:"):
            continue
        ex = evt.extra or {}
        mag = ex.get("magnitude", 0.0)
        rows.append({"admission": evt.event, "class": ex.get("class", ""),
                     "reason": ex.get("reason", ""),
                     "aggregate_only": ex.get("aggregate_only", False),
                     "triggers": ex.get("triggers", []),
                     "magnitude": float(mag) if isinstance(mag, (int, float)) else 0.0,
                     "pair_id": evt.pair_id, "actor": evt.actor,
                     "ts": evt.ts, "audit_id": evt.audit_id})
    rows.sort(key=lambda r: r["ts"], reverse=True)
    held = sum(1 for r in rows if r["admission"] == "hold")
    # update-budget meter input: cumulative admitted magnitude this feed
    spent = sum(r["magnitude"] for r in rows if r["admission"] == "admit")
    cap = budget_cap_get(folder, log_root=log_root)
    return {"folder": str(folder), "count": len(rows), "held": held,
            "spent": spent, "cap": cap,
            "over_budget": (cap is not None and spent > cap),
            "events": rows[:max(1, limit)]}


def precedent_declare(params: dict, *,
                      log_root: Optional[str | Path] = None) -> dict:
    """Declare a human origination learnable — the agent may follow it in
    matching cases (revocable, TTL'd). Written to the signed chain."""
    from .lens import Precedent
    from .oversight_log import record_precedent
    folder = params.get("folder_context")
    if not folder:
        return {"error": "precedent_declare needs folder_context"}
    prec = Precedent(
        id=str(params.get("id", "")),
        query_features=params.get("query_features") or {},
        chosen_option=str(params.get("chosen_option", "")),
        rationale=str(params.get("rationale", "")),
        actor=str(params.get("actor", "")),
        learnable=bool(params.get("learnable", True)),
        similarity_threshold=float(params.get("similarity_threshold", 0.9)),
        expires_at=params.get("expires_at"),
        revoked=False)
    if not prec.id:
        return {"error": "precedent_declare needs id"}
    rec = record_precedent(prec, folder=folder, action="declare",
                           actor=str(params.get("actor", "system")),
                           log_root=log_root)
    return {"declared": prec.to_dict(), "audit": rec}


def precedent_revoke(params: dict, *,
                     log_root: Optional[str | Path] = None) -> dict:
    """Revoke a precedent — the agent may no longer follow it. Recorded."""
    from .lens import Precedent
    from .oversight_log import record_precedent
    folder = params.get("folder_context")
    pid = str(params.get("id", ""))
    if not folder or not pid:
        return {"error": "precedent_revoke needs folder_context + id"}
    prec = Precedent(id=pid, query_features={}, chosen_option="",
                     rationale=str(params.get("reason", "")),
                     actor=str(params.get("actor", "")),
                     learnable=False, revoked=True)
    rec = record_precedent(prec, folder=folder, action="revoke",
                           actor=str(params.get("actor", "system")),
                           log_root=log_root)
    return {"revoked": pid, "audit": rec}


def precedent_list(folder: str | Path, *,
                   log_root: Optional[str | Path] = None,
                   now: Optional[float] = None,
                   include_inactive: bool = False) -> dict:
    """The live precedent shelf, reconstructed from the signed chain."""
    from .oversight_log import list_precedents
    precs = list_precedents(folder, log_root=log_root, now=now,
                            include_inactive=include_inactive)
    return {"folder": str(folder), "count": len(precs),
            "precedents": [p.to_dict() for p in precs]}
