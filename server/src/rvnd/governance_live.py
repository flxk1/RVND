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


def _neutral_governance() -> dict[str, Any]:
    """The honest-neutral governance object for an UNATTRIBUTED connection — an
    agent that never appears as an actor on the signed chain. Every dispositive
    field is null/empty: no verdict is ever fabricated here, and ``_agent_
    boundary`` is deliberately NOT consulted (its fail-closed ``refused`` default
    would misread as an earned verdict for an agent that simply has no history)."""
    return {
        "attributed": False,
        "join_key": None,
        "verdict": None,
        "grade": None,
        "escalation": False,
        "event_count": 0,
        "last_event_ts": None,
        "recent": [],
    }


def connected_agents_governance(folder_context: str, *,
                                log_root: Optional[str] = None,
                                chain_limit: int = 10,
                                now: Optional[float] = None) -> dict[str, Any]:
    """Join REAL server-level presence (connected agents) to REAL folder-chain
    governance. The join key is the host SESSION ID when the connection carries
    one (``session_id`` == chain actor, the true per-session identity), and the
    agent NAME only as a fallback.

    Presence records (connid, agent, session_id, transport, pid, connected_at)
    come from ``list_connected``. The folder chain is replayed ONCE and its
    events bucketed by ``evt.actor``. A connection is ``attributed`` iff its
    join actor (session_id, else agent name) appears as an actor on the chain
    (>=1 event); only then is the
    REAL verdict/grade/escalation computed via ``_agent_boundary`` (lane_
    capabilities, strictest-wins) and the actor's own chain tail returned. An
    unattributed connection gets the honest-neutral object (all nulls / empty) —
    never a fabricated or fail-closed verdict. connid/pid never touch the chain
    and never derive governance. Pure projection: no chain append, no lease."""
    from .mutation_log import MutationLog
    from .connected_agents import list_connected

    agents = list_connected()

    # Replay the folder chain ONCE; bucket events by actor (oldest→newest as
    # appended, so a simple reverse gives newest-first).
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    try:
        log = MutationLog(folder_context, log_root=_log_root_path(log_root))
        for idx, evt in enumerate(log.replay()):
            extra = evt.extra or {}
            buckets[evt.actor].append({
                "seq": idx,
                "event": evt.event,
                "action": str(extra.get("action") or ""),
                "kind": str(extra.get("kind") or ""),
                "audit_id": evt.audit_id,
                "ts": _iso(evt.ts),
            })
    except Exception:  # noqa: BLE001 — an unreadable chain ⇒ nobody attributed
        buckets = defaultdict(list)

    out: list[dict[str, Any]] = []
    for rec in agents:
        agent = str(rec.get("agent") or "")
        session_id = str(rec.get("session_id") or "").strip()
        # Prefer the true per-session join key (session_id == chain actor); fall
        # back to the agent name only when the connection carries no session id.
        if session_id and buckets.get(session_id):
            join_actor, join_key = session_id, "session_id"
        else:
            join_actor, join_key = agent, "agent"
        events = buckets.get(join_actor) or []
        attributed = bool(join_actor) and len(events) > 0
        if attributed:
            boundary = _agent_boundary(folder_context, join_actor, log_root)
            recent = list(reversed(events))[:chain_limit]  # newest first, bounded
            gov: dict[str, Any] = {
                "attributed": True,
                "join_key": join_key,
                "verdict": boundary.get("verdict"),
                "grade": boundary.get("grade"),
                "escalation": bool(boundary.get("escalation")),
                "event_count": len(events),
                "last_event_ts": recent[0]["ts"] if recent else None,
                "recent": recent,
            }
        else:
            gov = _neutral_governance()
        out.append({
            "connid": rec.get("connid"),
            "agent": rec.get("agent"),
            "session_id": (session_id or None),
            "transport": rec.get("transport"),
            "pid": rec.get("pid"),
            "connected_at": rec.get("connected_at"),
            "governance": gov,
        })

    return {
        "ok": True,
        "folder_context": folder_context,
        "count": len(out),
        "agents": out,
    }


def _client_of(conn: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The DESCRIPTIVE MCP clientInfo carried by a live connection, tier-flagged.

    ``tier`` is the literal constant ``"observed"``: RVND saw this at the MCP
    transport handshake (clientInfo in ``initialize``), NOT proven on the signed
    chain. The tier travels WITH the value, so a gate/authority can always read
    the provenance off the client object and never mistake it for the witnessed
    (chain) identity. ``name``/``version`` are None when there is no connection or
    the connection carried no clientInfo — never fabricated, never a human name."""
    name = str((conn or {}).get("client_name") or "").strip() or None
    version = str((conn or {}).get("client_version") or "").strip() or None
    return {"name": name, "version": version, "tier": "observed"}


def session_governance(folder_context: str, *,
                       log_root: Optional[str] = None,
                       chain_limit: int = 10,
                       now: Optional[float] = None) -> dict[str, Any]:
    """Sessions/agents that have ACTED, sourced from the SIGNED CHAIN — the real
    per-session identity (the actor the PreToolUse hook records IS the session id).

    The chain is keyed by the true per-session actor, and a live connection now
    carries that same id as ``session_id`` (``CLAUDE_CODE_SESSION_ID``, captured
    on connect and backfilled from the running process). So we make the CHAIN
    ACTORS the primary list: each acting actor gets its REAL lane verdict/grade/
    escalation via ``_agent_boundary`` (lane_capabilities, strictest-wins — the
    verdict the gate WOULD dispose for this actor; fail-closed 'refused' when the
    actor has no approved lane is a real, honest disposition, not a fabrication)
    plus its own recent chain tail. A live connection is cross-referenced to flag
    connected/connid/pid/session_id, joined by ``session_id == actor`` (agent
    name only as a fallback) — never fused by a guess. Connections that have not
    acted are returned separately as idle presence. Pure projection: no chain
    append, no lease."""
    from .connected_agents import list_connected
    from .mutation_log import MutationLog

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    try:
        log = MutationLog(folder_context, log_root=_log_root_path(log_root))
        for idx, evt in enumerate(log.replay()):
            extra = evt.extra or {}
            buckets[evt.actor].append({
                "seq": idx, "event": evt.event,
                "action": str(extra.get("action") or ""),
                "kind": str(extra.get("kind") or ""),
                "audit_id": evt.audit_id, "ts": _iso(evt.ts),
            })
    except Exception:  # noqa: BLE001 — unreadable chain ⇒ no acting sessions
        buckets = defaultdict(list)

    conns = list_connected()
    # The honest join key is the HOST SESSION ID: a live connection carries the
    # same ``session_id`` (``CLAUDE_CODE_SESSION_ID``) that the PreToolUse hook
    # records as the chain ACTOR. So a connection whose session_id equals an
    # acting actor IS that session's live presence — its REAL verdict is the
    # actor's lane disposition, not a fabrication. The agent NAME is kept only as
    # a secondary fallback (for non-host agents whose name equals their actor).
    by_session: dict[str, dict[str, Any]] = {}
    # How many LIVE connections claim each session id. The join key is an
    # unauthenticated host env var (CLAUDE_CODE_SESSION_ID), so >1 connection can
    # claim one actor's id. We resolve to the newest (conns are newest-first) but
    # must not resolve it SILENTLY: the count drives a presence_ambiguous flag so
    # an overseer sees the collision instead of trusting one arbitrary process.
    session_conn_count: dict[str, int] = defaultdict(int)
    by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in conns:
        sid = str(c.get("session_id") or "").strip()
        if sid:
            session_conn_count[sid] += 1
            if sid not in by_session:
                by_session[sid] = c            # newest wins (conns are newest-first)
        by_agent[str(c.get("agent") or "")].append(c)

    sessions: list[dict[str, Any]] = []
    for actor, events in buckets.items():
        if not actor:
            continue
        boundary = _agent_boundary(folder_context, actor, log_root)
        recent = list(reversed(events))[:chain_limit]  # newest first, bounded
        # Join by session_id first (the true per-session key), then by agent name.
        conn = by_session.get(actor) or (by_agent.get(actor) or [None])[0]
        sessions.append({
            "actor": actor,
            "session_id": (str(conn.get("session_id") or "") or None) if conn else None,
            # The chain actor / session id IS the WITNESSED identity: it is proven
            # on the signed chain (the actor the PreToolUse hook recorded). The
            # tier travels with the value so a gate can read provenance off it and
            # never confuse it with the observed (clientInfo) lane.
            "identity_tier": "witnessed",
            "verdict": boundary.get("verdict"),      # REAL lane disposition
            "grade": boundary.get("grade"),
            "escalation": bool(boundary.get("escalation")),
            "event_count": len(events),
            "last_event_ts": recent[0]["ts"] if recent else None,
            "recent": recent,                        # REAL chain tail for this actor
            "connected": conn is not None,           # LIVE iff a connection matched
            # True when >1 live connection claims this actor's (unauthenticated)
            # session id — the presence match is then ambiguous and the connid/pid
            # below are only ONE of several. Honest surfacing of a silent collision.
            "presence_ambiguous": session_conn_count.get(actor, 0) > 1,
            "connid": conn.get("connid") if conn else None,
            "pid": conn.get("pid") if conn else None,
            # DESCRIPTIVE clientInfo from the matched live connection, tier
            # 'observed' (never witnessed). None where no connection / no clientInfo.
            "client": _client_of(conn),
        })
    sessions.sort(key=lambda s: s.get("last_event_ts") or "", reverse=True)

    def _acted(c: dict[str, Any]) -> bool:
        sid = str(c.get("session_id") or "").strip()
        ag = str(c.get("agent") or "")
        return bool((sid and buckets.get(sid)) or (ag and buckets.get(ag)))

    idle = [
        {"connid": c.get("connid"), "agent": c.get("agent"),
         "transport": c.get("transport"), "pid": c.get("pid"),
         "session_id": (str(c.get("session_id") or "") or None),
         "connected_at": c.get("connected_at"),
         # DESCRIPTIVE clientInfo, tier 'observed'. None where no clientInfo.
         "client": _client_of(c)}
        for c in conns if not _acted(c)
    ]

    return {
        "ok": True,
        "folder_context": folder_context,
        "session_count": len(sessions),
        "sessions": sessions,
        "connected_only": idle,
    }


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
        # A real count ONLY when reconciliation actually ran (an int is present);
        # a failed/unavailable check yields None — rendered "—", never a false "0"
        # that would read as a verified-clean board. Honest-omit, not a fail-OPEN
        # pass: a broken check must never look identical to a passed one.
        "unauthorised_effects": (
            reconciliation.get("observed_not_authorised")
            if isinstance(reconciliation.get("observed_not_authorised"), int)
            else None),
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
