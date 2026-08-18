# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""``governance_live`` — read-only projection of live governance state.

The honest-subset board (spec v2, 2026-08-08): every governance session the
signed log has admitted, the run leases that serialize workflow execution, and
the one chain they all append to — each field wired to a REAL folder-readable
source. It is a **pure projection** in the ``workspace_conformity``/``console_
snapshot`` family: it appends no chain event, acquires no lease, mutates
nothing.

House rule (omit-don't-fake): fields no module surfaces folder-readably are
absent, never invented. Deferred to later module work (never faked here):
session ``kind``, autonomy ``decay_pct``, iteration ``budget``, per-agent
``breaker`` arm-state, and a per-session action verdict — none has a read-only
folder source today (breaker/oversight state is in-memory; decay is binary).
The per-agent live verdict/grade/escalation come from ``lane_capabilities``
instead, the one fingerprint-stamped, fail-closed boundary projection that IS
folder-readable.

Sources:
  * sessions   ← ``mutation_log.replay`` for ``GovernanceSessionOpened`` events
                 (there is no session registry; sessions are derived by replay).
  * admitted   ← claims ``exp`` vs now AND the nonce-keyed revocation store.
  * verdict/grade/escalation ← ``lane_capabilities`` (per-agent, strictest-wins).
  * leases     ← ``queue.list_queue`` (per-(folder,workflow) run lease).
  * chain      ← ``mutation_log.replay`` (seq = replay index; prev_hash public).
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

# Strictest-wins ranking for collapsing one agent's per-(kind,risk) cells into a
# single honest board verdict — the tightest constraint the agent faces, never
# the loosest (a governance board must not under-report the protection).
_VERDICT_RANK = {
    "prohibited": 5,
    "refused": 4,
    "reserved": 3,
    "human": 2,
    "auto": 1,
    "unfired": 0,
}


def _log_root_path(log_root: Optional[str]) -> Optional[Path]:
    return Path(log_root) if log_root else None


def _iso(epoch: Any) -> str:
    """Epoch seconds → ISO-8601 Z. Empty string on anything unparseable."""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))
    except (TypeError, ValueError, OSError):
        return ""


def _agent_boundary(folder_context: str, actor: str,
                    log_root: Optional[str]) -> dict[str, Any]:
    """One agent's strictest verdict + grade + escalation, from lane_capabilities.

    Fail-closed: no active lane, an unreadable chain, or no capabilities →
    ``refused`` / no grade / no escalation — the same default the module itself
    projects, never a blank that could read as permissive.
    """
    from .lane_capabilities import lane_capabilities
    try:
        cap = lane_capabilities(folder_context, actor, log_root=log_root)
    except Exception:  # noqa: BLE001 — an unreadable boundary must fail closed
        return {"verdict": "refused", "grade": None, "escalation": False}
    caps = cap.get("capabilities") or []
    if not cap.get("ok") or not caps:
        return {"verdict": "refused", "grade": None, "escalation": False}
    verdict = "unfired"
    escalation = False
    for entry in caps:
        cells = list(entry["by_risk"].values()) if "by_risk" in entry else [entry]
        for cell in cells:
            v = cell.get("verdict") or "unfired"
            if _VERDICT_RANK.get(v, 0) > _VERDICT_RANK.get(verdict, 0):
                verdict = v
            if cell.get("escalation"):
                escalation = True
    grade = (cap.get("provenance") or {}).get("max_grade")
    return {"verdict": verdict, "grade": grade, "escalation": escalation}


def _sessions(folder_context: str, log_root: Optional[str], now: float,
              revoked: Any) -> list[dict[str, Any]]:
    """Derive sessions from the signed log (no registry exists to list them)."""
    from .mutation_log import MutationLog
    log = MutationLog(folder_context, log_root=_log_root_path(log_root))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evt in log.replay():
        extra = evt.extra or {}
        if extra.get("kind") != "GovernanceSessionOpened":
            continue
        claims = extra.get("claims") or {}
        nonce = str(claims.get("nonce") or "")
        sid = evt.pair_id or (f"session:{nonce}" if nonce else "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        exp = claims.get("exp")
        try:
            live = exp is not None and float(exp) > now
        except (TypeError, ValueError):
            live = False
        admitted = bool(live and nonce and nonce not in revoked)
        session: dict[str, Any] = {"sid": sid, "admitted": admitted}
        if admitted:
            # Signed-ness is implied by a valid admission; no explicit field.
            session["capability"] = {
                "folder_context": str(claims.get("folder") or ""),
                "expires": _iso(exp),
            }
        session.update(_agent_boundary(folder_context, evt.actor, log_root))
        out.append(session)
    return out


def _leases(folder_context: str, log_root: Optional[str],
            now: float) -> list[dict[str, Any]]:
    """Run leases for this folder — the serialization view. One holder
    (position 0) per (folder, workflow); the rest queue by enqueue order."""
    from . import queue as _q
    lr = _log_root_path(log_root)
    entries = _q.list_queue(folder_path=folder_context, log_root=lr)
    active = [e for e in entries if e.state in ("pending", "leased")]
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for e in active:
        groups[(e.folder_path, e.workflow_name)].append(e)
    out: list[dict[str, Any]] = []
    for grp in groups.values():
        leased = [e for e in grp if e.state == "leased"]
        pending = sorted((e for e in grp if e.state == "pending"),
                         key=lambda e: e.enqueued_at)
        for pos, e in enumerate(leased + pending):  # ≤1 leased ⇒ it is position 0
            holder: Optional[str] = None
            ttl: Optional[int] = None
            if e.state == "leased":
                holder = e.leased_to or None
                lease = _q._read_lease(e.run_id, lr)
                if lease is not None:
                    ttl = max(0, int(lease.expires_at - now))
            out.append({
                "run_id": e.run_id,
                "folder": e.folder_path,
                "workflow": e.workflow_name,
                "holder": holder,
                "position": pos,
                "ttl_s": ttl,
            })
    return out


def _chain(folder_context: str, log_root: Optional[str],
           limit: int) -> list[dict[str, Any]]:
    """The last ``limit`` chain entries as a CONTIGUOUS replay tail, newest
    first — no filtering or sampling between adjacent entries. Contiguity is
    load-bearing: it is what lets a consumer verify linearity, since each
    entry's ``prev_hash`` is the ``hash`` of the entry immediately older than
    it (so ``chain[i]["prev_hash"] == chain[i+1]["hash"]``). ``seq`` is the
    replay index (the log has no seq field). ``hash`` is the canonical content
    hash — a digest of an already-public audit event, identical to what the
    next entry stored as its ``prev_hash`` — so exposing it leaks nothing and
    makes the link checkable. Any filtered/per-actor view must be a SEPARATE
    field, never this linkage-checked ``chain``.
    """
    from .mutation_log import MutationLog, _canonical_event_hash
    log = MutationLog(folder_context, log_root=_log_root_path(log_root))
    events = list(log.replay())
    start = max(0, len(events) - limit)
    out: list[dict[str, Any]] = []
    for idx in range(len(events) - 1, start - 1, -1):
        evt = events[idx]
        out.append({
            "seq": idx,
            "actor": evt.actor,
            "event": evt.event,
            "extra": str((evt.extra or {}).get("kind") or ""),
            "hash": _canonical_event_hash(json.loads(evt.to_jsonl())),
            "prev_hash": evt.prev_hash or "",
        })
    return out


def _certificates(folder_context: str, log_root: Optional[str],
                  limit: int) -> list[dict[str, Any]]:
    """Portable oversight certificates persisted BESIDE the chain (the
    ``oversight_certs.jsonl`` sidecar), newest first and bounded — a SEPARATE
    field from the linkage-checked ``chain`` (never mixed into it). Each row is
    ``{audit_id, certificate}`` linking a certificate to the decision's own audit
    event, so a returned human decision carries its proof on the same board that
    already tracks it — no new surface, no second courier. Read-only; empty when
    the sidecar (or the [oversight-cert] extra) is absent. Never raises."""
    try:
        from .mutation_log import MutationLog
        log = MutationLog(folder_context, log_root=_log_root_path(log_root))
        path = log.log_dir / "oversight_certs.jsonl"
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                continue
        return rows[-limit:][::-1]
    except Exception:
        return []


def _reconciliation(folder_context: str,
                    log_root: Optional[str]) -> dict[str, Any]:
    """Complete-mediation summary for the board: the authorisation ledger
    (per-step gate verdicts) reconciled against the effect ledger (observed step
    outcomes) over the whole chain — see ``reconciliation_binding``. Compact by
    design: the count of unauthorised effects, not their list (that stays in the
    ``evidence_pack``). Fail-closed to an ``unavailable`` summary — a board panel
    must never break the board."""
    try:
        from . import reconciliation_binding as rb
        from .mutation_log import MutationLog
        log = MutationLog(folder_context, log_root=_log_root_path(log_root))
        events = list(log.replay())
        if not events:
            return {"status": "reconciled", "unauthorised_rate": 0.0,
                    "matched": 0, "authorised_not_observed": 0,
                    "observed_not_authorised": 0}
        first = min(e.ts for e in events)
        last = max(e.ts for e in events)
        r = rb.reconcile_projection(events, since_ts=first, until_ts=last + 1.0)
        return {"status": r.get("status"),
                "unauthorised_rate": r.get("unauthorised_rate", 0.0),
                "matched": r.get("matched", 0),
                "authorised_not_observed": r.get("authorised_not_observed", 0),
                "observed_not_authorised": len(r.get("observed_not_authorised", []))}
    except Exception:                                   # noqa: BLE001
        return {"status": "unavailable"}


def governance_live(folder_context: str, *, log_root: Optional[str] = None,
                    chain_limit: int = 20,
                    now: Optional[float] = None) -> dict[str, Any]:
    """Read-only governance-live board for one folder. Pure projection."""
    now = time.time() if now is None else float(now)
    try:
        from .session_capability import FileRevocationStore
        revoked: Any = FileRevocationStore.default()
    except Exception:  # noqa: BLE001 — no revocation store ⇒ nothing revoked
        revoked = frozenset()
    sessions = _sessions(folder_context, log_root, now, revoked)
    leases = _leases(folder_context, log_root, now)
    chain = _chain(folder_context, log_root, chain_limit)
    certificates = _certificates(folder_context, log_root, chain_limit)
    reconciliation = _reconciliation(folder_context, log_root)
    summary = {
        "sessions_open": len(sessions),
        "admitted": sum(1 for s in sessions if s.get("admitted")),
        "run_leases_held": sum(
            1 for lease in leases
            if lease.get("position") == 0 and lease.get("holder")
        ),
        "escalations": sum(1 for s in sessions if s.get("escalation")),
        # Complete-mediation at a glance: effects observed with no authorisation.
        "unauthorised_effects": reconciliation.get("observed_not_authorised", 0),
    }
    return {
        "ok": True,
        "summary": summary,
        "sessions": sessions,
        "leases": leases,
        "chain": chain,
        "certificates": certificates,
        "reconciliation": reconciliation,
    }
