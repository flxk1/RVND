# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Log-write glue + ground-id taint queries.

Two thin couplings between the oversight decision layers and the mutation log:

  * :func:`record_admission` — turns a Lens :class:`AdmissionVerdict` into the
    matching first-class lifecycle event (``admit`` / ``hold`` / ``reject``),
    so "the agent learned X" is an entry in the signed chain, never an
    untracked mutation. The decision is computed in :mod:`lens`; this writes it.
  * :func:`taint_walk` / :func:`mark_tainted` — background-grounding §7.2.
    When a ground (a cited norm/precedent pair) fails re-verification, walk the
    log backwards and surface every verdict that cited it: those decisions
    rested on a ground that no longer holds. Detective, never destructive —
    it raises findings (F2/F3), the trigger taxonomy routes them.

Pure stdlib + MutationLog. ``record_admission`` degrades gracefully (a log
failure is attached to the record, never raised — a decision is never lost).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .lens import AdmissionVerdict, Admission, Precedent
from .mutation_log import LogEvent, MutationLog


# Map a Lens admission to the lifecycle verb (all three are VALID_EVENTS).
_ADMISSION_EVENT = {
    Admission.ADMIT: "admit",
    Admission.HOLD: "hold",
    Admission.REJECT: "reject",
}


def record_admission(
    verdict: AdmissionVerdict,
    *,
    folder: str | Path,
    content_hash: str,
    actor: str = "system",
    log_root: Optional[str | Path] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Write the admission verdict as a lifecycle event and return the record.

    ``content_hash`` is the learning object's hash → the ``pair_id`` of the
    learning event, so the same object re-presented is idempotent at the log
    level. ``aggregate_only`` rides in ``extra`` so a downstream reader knows
    an admitted pair must never be stored individually."""
    event_name = _ADMISSION_EVENT[verdict.admission]
    payload = {
        "kind": "learning-admission",
        "class": verdict.cls,
        "admission": verdict.admission.value,
        "reason": verdict.reason,
        "aggregate_only": verdict.aggregate_only,
        "triggers": verdict.triggers,
        **(extra or {}),
    }
    record: dict[str, Any] = {
        "admission": verdict.admission.value,
        "class": verdict.cls,
        "pair_id": f"learn:{content_hash}",
    }
    try:
        log = MutationLog(Path(folder),
                          log_root=Path(log_root) if log_root else None)
        record["audit_id"] = log.append(LogEvent(
            event=event_name, folder_path=str(folder),
            pair_id=f"learn:{content_hash}", channel="system",
            lifecycle_state=("" if verdict.admission is Admission.ADMIT
                             else "rejected" if verdict.admission is Admission.REJECT
                             else ""),
            actor=actor, extra=payload))
    except Exception as exc:                 # noqa: BLE001 — never lose the decision
        record["audit_error"] = f"{type(exc).__name__}: {exc}"
    return record


# ── precedent persistence (stare decisis for agents, §4.3) ──────────────────
# A precedent is a recorded human origination declared learnable. Declaration
# and revocation are STAKE-BEARING (they let the agent act under a human's
# judgment), so they ride the signed chain as `system` events, never hand state.
# The live shelf is reconstructed by replay (most-recent action per id wins) —
# same "replay, never store" discipline as the update budget.

def record_precedent(
    precedent: Precedent,
    *,
    folder: str | Path,
    action: str = "declare",          # "declare" | "revoke"
    actor: str = "system",
    log_root: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Write a precedent declare/revoke as a signed `system` event and return
    the record. Idempotent at the log level by ``pair_id = precedent:<id>``."""
    payload = {"kind": "precedent", "action": action, **precedent.to_dict()}
    record: dict[str, Any] = {"action": action, "precedent_id": precedent.id,
                              "pair_id": f"precedent:{precedent.id}"}
    try:
        log = MutationLog(Path(folder),
                          log_root=Path(log_root) if log_root else None)
        record["audit_id"] = log.append(LogEvent(
            event="system", folder_path=str(folder),
            pair_id=f"precedent:{precedent.id}", channel="system",
            actor=actor, extra=payload))
    except Exception as exc:                 # noqa: BLE001 — never lose the decision
        record["audit_error"] = f"{type(exc).__name__}: {exc}"
    return record


def list_precedents(
    folder: str | Path,
    *,
    log_root: Optional[str | Path] = None,
    now: Optional[float] = None,
    include_inactive: bool = False,
) -> list[Precedent]:
    """Reconstruct the live precedent shelf by replaying the signed chain.

    Most-recent ``precedent`` event per id wins; revoked precedents are dropped
    (unless ``include_inactive``); expired precedents are excluded from the
    *active* shelf but kept when ``include_inactive`` is set."""
    import time as _t
    latest: dict[str, dict[str, Any]] = {}
    log = MutationLog(Path(folder),
                      log_root=Path(log_root) if log_root else None)
    for evt in log.replay():
        ex = evt.extra or {}
        if evt.event != "system" or ex.get("kind") != "precedent":
            continue
        pid = ex.get("id")
        if pid:
            latest[pid] = ex                 # replay order ⇒ last write wins
    out: list[Precedent] = []
    when = now if now is not None else _t.time()
    for ex in latest.values():
        p = Precedent(
            id=str(ex.get("id", "")),
            query_features=ex.get("query_features") or {},
            chosen_option=str(ex.get("chosen_option", "")),
            rationale=str(ex.get("rationale", "")),
            actor=str(ex.get("actor", "")),
            learnable=bool(ex.get("learnable", False)),
            similarity_threshold=float(ex.get("similarity_threshold", 0.9)),
            expires_at=ex.get("expires_at"),
            revoked=bool(ex.get("revoked", False)) or ex.get("action") == "revoke")
        if not include_inactive:
            if p.revoked:
                continue
            if p.expires_at is not None and when >= float(p.expires_at):
                continue
        out.append(p)
    out.sort(key=lambda p: p.id)
    return out


# ── ground-id taint (background grounding §7.2) ─────────────────────────────

@dataclass
class TaintFinding:
    """One verdict that cited a now-failed ground."""
    audit_id: str
    pair_id: str
    actor: str
    ts: float
    cited: str                              # the failed ground id
    event: str
    stake_bearing: bool = False             # footprint present ⇒ incident

    def to_dict(self) -> dict[str, Any]:
        return {"audit_id": self.audit_id, "pair_id": self.pair_id,
                "actor": self.actor, "ts": self.ts, "cited": self.cited,
                "event": self.event, "stake_bearing": self.stake_bearing}


_STAKE_FOOTPRINTS = {"personal-data", "financial", "irreversible",
                     "external-publish", "security-control"}


def _cited_grounds(evt: LogEvent) -> list[str]:
    """Ground ids a log event cites. Verdicts store them under
    ``extra.obligation_pairs`` (audit triples) or ``extra.grounds``."""
    ex = evt.extra or {}
    out: list[str] = []
    for key in ("obligation_pairs", "grounds", "cited"):
        v = ex.get(key)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict) and "id" in item:
                    out.append(item["id"])
        elif isinstance(v, str):
            out.append(v)
    return out


def taint_walk(events: Iterable[LogEvent], failed_ground: str) -> list[TaintFinding]:
    """Surface every event that cited ``failed_ground``. Pure over an event
    iterable so it works on a replay, a subset, or a test list.

    Stake-bearing findings (the event carried a risk footprint) are flagged
    ``stake_bearing`` — those become incidents at the folder's oversight level
    (§7.2); the rest are advisory re-checks."""
    findings: list[TaintFinding] = []
    for evt in events:
        cited = _cited_grounds(evt)
        if failed_ground not in cited:
            continue
        ex = evt.extra or {}
        footprint = ex.get("footprint", []) or []
        stake = any(f in _STAKE_FOOTPRINTS for f in footprint)
        findings.append(TaintFinding(
            audit_id=evt.audit_id, pair_id=evt.pair_id, actor=evt.actor,
            ts=evt.ts, cited=failed_ground, event=evt.event,
            stake_bearing=stake))
    return findings


def mark_tainted(
    findings: Iterable[TaintFinding],
    *,
    folder: str | Path,
    failed_ground: str,
    reason: str = "",
    log_root: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Write a `system` event recording the taint sweep (detective, not
    destructive — nothing is undone). Returns the summary. Stake-bearing
    findings are listed separately so the caller can raise them as incidents."""
    findings = list(findings)
    stake = [f for f in findings if f.stake_bearing]
    summary = {
        "kind": "ground-taint",
        "failed_ground": failed_ground,
        "reason": reason,
        "tainted_count": len(findings),
        "stake_bearing_count": len(stake),
        "tainted_audit_ids": [f.audit_id for f in findings],
        "incidents": [f.to_dict() for f in stake],
    }
    try:
        log = MutationLog(Path(folder),
                          log_root=Path(log_root) if log_root else None)
        summary["audit_id"] = log.append(LogEvent(
            event="system", folder_path=str(folder),
            pair_id=f"taint:{failed_ground}", channel="system",
            actor="system", extra=summary))
    except Exception as exc:                 # noqa: BLE001
        summary["audit_error"] = f"{type(exc).__name__}: {exc}"
    return summary
