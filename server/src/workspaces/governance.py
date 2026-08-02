# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The one oversight chokepoint every action passes through.

Whatever the actor — a skill, an agent, a workflow step, a privacy egress, a call
arriving from any MCP client — it resolves to ONE decision here, composed from the
four things that bind, and written to the ONE signed audit chain:

  1. the gate's structural verdict        (footprint × autonomy grade × standing
                                            approvals → GO / CONDITIONAL / NO-GO)
  2. the workspace's policy matrix             (grade × oversight → go / ask / block,
                                            resolved global→workspace→sub-workspace)
  3. the workspace's oversight level           (which row of the matrix is live)
  4. the data's privacy-class floor       (regulated → supervised, …)

strictest-wins, via the shared tri-state (`verdict.py`). The result is PERMIT /
HOLD / DENY: PERMIT acts, HOLD pushes to the human (explanation + ask), DENY is
refused. Lock is one action type that flows through here, not a separate path.

This module composes existing substrate; it adds no new authority. Callers that
already gate (the workflow runner) may delegate here later — this is the seam.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import policy_matrix as _pm
from . import verdict as _vd


def _actor_grade_cap(folder: str | Path, actor: str,
                     *, log_root: Optional[Path] = None) -> str:
    """The Breaker/kill-switch cap on a registered AGENT's autonomy: ``L0`` when
    the agent is suspended or killed (quarantined — the kill switch), else ``""``
    (no breaker cap).

    Scope (deliberate): the breaker is an AGENT kill-switch, so it caps actors
    that are registered agents. It does NOT cap actors absent from the register —
    those are the runtime / system / non-agent callers (e.g. the default
    ``mcp:l0`` dispatch actor), which the breaker was never meant to govern;
    capping them to L0 would force every system action to maximum oversight (a
    denial of service) and add no safety, because the action gate + policy matrix
    + oversight already govern EVERY actor regardless of registration. The breaker
    is a supplementary revocation on top of that primary authorization, not the
    authorization itself.

    Fail-closed: if the party register can't be read at all, return ``L0`` — we
    cannot verify that an agent isn't quarantined, so we don't trust the asked
    grade."""
    try:
        from .parties import list_parties
        for r in list_parties(folder, log_root=log_root).get("parties", []):
            if r.get("party_id") == actor and r.get("status") in ("suspended", "killed"):
                return "L0"
    except Exception:
        return "L0"
    return ""


def _is_registered_agent(folder: str | Path, actor: str,
                         *, log_root: Optional[Path] = None) -> bool:
    """Return whether the actor is governed as an agent in this folder."""
    try:
        from .parties import list_parties
        records = list_parties(folder, log_root=log_root)
        parties = records.get("parties", []) if isinstance(records, dict) else records
        return any((row.get("party_id") or row.get("id")) == actor
                   and (row.get("party_kind") or row.get("kind")) == "agent"
                   for row in parties)
    except Exception:
        return True


def decide_action(
    folder: str | Path,
    *,
    action_class: str,
    grade: str = "L1",
    footprint: tuple[str, ...] = (),
    affected_parties: tuple[str, ...] = (),
    actor: str = "system",
    privacy_class: Optional[str] = None,
    standing_approvals: tuple = (),
    posture: str = "balanced",
    oversight_level: Optional[str] = None,
    grade_ceiling: str = "",
    use_case_id: str = "",
    connector_id: str = "",
    policy_fingerprint: str = "",
    log_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Resolve one action to PERMIT / HOLD / DENY and record it on the signed
    chain. Returns ``{verdict, light, oversight_level, grade, requested_grade,
    grade_ceiling, breaker_grade, gate_verdict, reason, audit_id, action_class}``.

    ``verdict`` is the canonical tri-state value: ``permit`` (act), ``hold``
    (push to the human and wait), ``deny`` (refuse).

    D9 — the requested ``grade`` is CAPPED before it reaches the gate, strictest
    wins: by the actor's Breaker/kill-switch state (a suspended/killed agent
    drops to L0) and by the composed oversight ``grade_ceiling`` (the regulatory
    cap). The chokepoint never trusts the requested grade above what the lease and
    the law allow; the effective and requested grades are both surfaced (for the
    UI ceiling fader and for audit)."""
    from .action_gate import ActionRequest, gate as _gate
    from .incidents import log_gate_decision
    from .policy import load_policy
    from .breaker import cap_grade

    requested_grade = grade if grade in _pm.GRADES else "L1"
    ov = (oversight_level or load_policy(folder).oversight_default_level or "approve")
    if ov not in _pm.OVERSIGHT:
        ov = "approve"

    # Cap the requested grade (D9): kill-switch/quarantine first, then the
    # composed regulatory ceiling. cap_grade is the lattice meet — "" = no cap,
    # an unrecognised ceiling token caps to L0 (fail-safe, M1).
    breaker_grade = _actor_grade_cap(folder, actor, log_root=log_root)
    g = cap_grade(requested_grade, breaker_grade) if breaker_grade else requested_grade
    if grade_ceiling:
        g = cap_grade(g, grade_ceiling)

    # P2: a use case the policy PROHIBITS is severed — the gate returns NO-GO
    # regardless of grade. Look it up by action_class (the applied twin keys the
    # prohibition to the use_case/gate id); a non-use-case action_class is unaffected.
    prohibited_actions: tuple[str, ...] = ()
    try:
        from .use_case import get_use_case
        _uc = get_use_case(folder, action_class, log_root=log_root)
        if _uc and _uc.get("prohibited"):
            prohibited_actions = (action_class,)
    except Exception:                            # noqa: BLE001
        # Fail-closed (cf. _actor_grade_cap): if we cannot read the use-case store we
        # cannot verify this action is NOT prohibited, so we treat it as prohibited
        # rather than waving it through (a would-be NO-GO must not fail OPEN).
        prohibited_actions = (action_class,)

    request = ActionRequest(agent=actor, action_class=action_class, autonomy_grade=g,
                            footprint=tuple(footprint), folder=str(folder),
                            affected_parties=tuple(affected_parties))
    lane_result = None
    if _is_registered_agent(folder, actor, log_root=log_root):
        from .governance_lane import evaluate_lane, get_lane
        lane_request = ActionRequest(
            agent=actor, action_class=action_class, autonomy_grade=requested_grade,
            footprint=tuple(footprint), folder=str(folder),
            affected_parties=tuple(affected_parties))
        lane_result = evaluate_lane(
            get_lane(folder, actor, log_root=log_root), lane_request,
            use_case_id=use_case_id or action_class,
            connector_id=connector_id,
            policy_fingerprint=policy_fingerprint,
        )
        if not lane_result.allowed:
            prohibited_actions = tuple(set(prohibited_actions) | {action_class})

    decision = _gate(
        request,
        standing_approvals=standing_approvals, posture=posture,
        prohibited_actions=prohibited_actions)

    # Compose the structural verdict with the workspace's resolved matrix + oversight
    # row + privacy floor. resolve_matrix returns the global default when the workspace
    # has no override, so every workspace is governed even with no painting.
    eff = _pm.effective_light(
        _pm.resolve_matrix(folder, log_root=log_root),
        grade=g, oversight=ov, privacy_class=privacy_class,
        gate_verdict=decision.verdict.value)

    canon = _vd.from_light(eff["light"])
    audit_id = log_gate_decision(folder, decision, log_root=log_root, actor=actor)
    return {
        "verdict": canon.value,             # permit | hold | deny
        "light": eff["light"],              # go | ask | block
        "oversight_level": ov,
        "grade": g,                         # the EFFECTIVE (capped) grade the gate used
        "requested_grade": requested_grade,  # what was asked, before capping (D9)
        "grade_ceiling": grade_ceiling,     # composed regulatory ceiling ("" = none)
        "breaker_grade": breaker_grade,     # kill-switch cap ("L0" if quarantined, else "")
        "gate_verdict": decision.verdict.value,
        "privacy_class": privacy_class,
        "reason": eff["reason"] or decision.reason,
        "audit_id": audit_id,
        "action_class": action_class,
        "actor": actor,
        "governance_lane": lane_result.to_dict() if lane_result else None,
    }


def permits(decision: dict[str, Any]) -> bool:
    return decision.get("verdict") == "permit"


def _band(ov: str) -> str:
    i = _pm.OVERSIGHT.index(ov)
    return "HITL" if i >= 3 else ("HIC" if i == 0 else "HOTL")


def decide_output(
    folder: str | Path,
    *,
    grounded: bool,
    oversight_level: Optional[str] = None,
    action_class: str = "result",
    actor: str = "system",
    detail: str = "",
    log_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Route an OUTPUT through the normal oversight modes by whether it is
    grounded by a cited source. Ungrounded output is always flagged; the workspace's
    oversight level then decides whether the agent **stops** or **keeps running**:

      * HITL band (approve / supervised / manual) → ``hold`` — the agent stops
        and pushes it to you before the output is used.
      * HOTL / HIC band (review / notify / autonomous) → ``permit`` but
        ``flagged`` — it keeps running; you catch it via the audit / undo.

    Grounded output → ``permit``. Recorded on the signed chain either way
    (including the flag), so an ungrounded-but-permitted output is never silent.
    """
    ov = (oversight_level or _load_oversight(folder))
    if ov not in _pm.OVERSIGHT:
        ov = "approve"
    if grounded:
        verdict, flagged = "permit", False
        reason = "grounded by a cited source"
    else:
        stops = _pm.OVERSIGHT.index(ov) >= _pm.OVERSIGHT.index("approve")
        verdict, flagged = ("hold" if stops else "permit"), True
        reason = (f"ungrounded output — agent stops (oversight={ov})" if stops
                  else f"ungrounded output — flagged, keeps running (oversight={ov})")
    audit_id = _log_grounding(folder, action_class, grounded, verdict, ov,
                              reason, actor, log_root)
    return {"verdict": verdict, "grounded": grounded, "flagged": flagged,
            "oversight_level": ov, "band": _band(ov), "reason": reason,
            "detail": detail, "audit_id": audit_id, "action_class": action_class}


def decide_confidence(
    folder: str | Path,
    *,
    confidence: float,
    floor: float = 0.85,
    oversight_level: Optional[str] = None,
    action_class: str = "extraction",
    actor: str = "system",
    detail: str = "",
    log_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Confidence + escalation, routed by the SAME oversight policy as everything
    else. An extraction (or any output) below ``floor`` is never silently emitted:
    it's flagged, and the workspace's oversight level decides — HITL (approve/supervised/
    manual) → ``hold`` (agent stops, you decide); HOTL/HIC (review/notify/
    autonomous) → ``permit`` but ``flagged`` (keeps running, caught via audit).
    Recorded on the signed chain. This is the legal-ND escalation path."""
    ov = (oversight_level or _load_oversight(folder))
    if ov not in _pm.OVERSIGHT:
        ov = "approve"
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    if conf >= float(floor):
        verdict, flagged = "permit", False
        reason = f"confidence {conf} ≥ floor {floor}"
    else:
        stops = _pm.OVERSIGHT.index(ov) >= _pm.OVERSIGHT.index("approve")
        verdict, flagged = ("hold" if stops else "permit"), True
        reason = (f"confidence {conf} < floor {floor} — agent stops (oversight={ov})"
                  if stops else
                  f"confidence {conf} < floor {floor} — flagged, keeps running (oversight={ov})")
    audit_id = _log_decision(folder, "confidence-gate",
                             {"confidence": conf, "floor": float(floor),
                              "verdict": verdict, "oversight": ov, "reason": reason},
                             action_class, actor, log_root)
    return {"verdict": verdict, "confidence": conf, "floor": float(floor),
            "flagged": flagged, "oversight_level": ov, "band": _band(ov),
            "reason": reason, "detail": detail, "audit_id": audit_id,
            "action_class": action_class}


def _log_decision(folder, kind, extra, action_class, actor, log_root) -> str:
    from .mutation_log import MutationLog, LogEvent
    try:
        log = MutationLog(Path(folder),
                          log_root=Path(log_root) if log_root else None)
        return log.append(LogEvent(
            event="system", folder_path=str(folder),
            pair_id=f"{kind}:{action_class}", channel="system", actor=actor,
            extra={"kind": kind, **extra}))
    except Exception:                            # noqa: BLE001 — never lose it
        return ""


def grounding_feed(folder: str | Path, *, log_root: Optional[Path] = None,
                   limit: int = 50) -> dict[str, Any]:
    """Read the grounding-gate decisions off the signed chain — the feed behind
    the dashboard's output-review surface. Newest first."""
    from .mutation_log import MutationLog
    log = MutationLog(Path(folder),
                      log_root=Path(log_root) if log_root else None)
    rows: list[dict[str, Any]] = []
    for evt in log.replay():
        ex = evt.extra or {}
        if evt.event != "system" or ex.get("kind") != "grounding-gate":
            continue
        rows.append({"grounded": bool(ex.get("grounded")),
                     "verdict": ex.get("verdict", ""),
                     "oversight": ex.get("oversight", ""),
                     "reason": ex.get("reason", ""),
                     "actor": evt.actor, "ts": evt.ts, "audit_id": evt.audit_id})
    rows.sort(key=lambda r: r["ts"], reverse=True)
    flagged = sum(1 for r in rows if r["verdict"] != "permit" or not r["grounded"])
    return {"folder": str(folder), "count": len(rows), "flagged": flagged,
            "events": rows[:max(1, limit)]}


def _load_oversight(folder: str | Path) -> str:
    from .policy import load_policy
    return load_policy(folder).oversight_default_level or "approve"


def _log_grounding(folder, action_class, grounded, verdict, ov, reason, actor,
                   log_root) -> str:
    from .mutation_log import MutationLog, LogEvent
    try:
        log = MutationLog(Path(folder),
                          log_root=Path(log_root) if log_root else None)
        return log.append(LogEvent(
            event="system", folder_path=str(folder),
            pair_id=f"grounding:{action_class}", channel="system", actor=actor,
            extra={"kind": "grounding-gate", "grounded": grounded,
                   "verdict": verdict, "oversight": ov, "reason": reason}))
    except Exception as exc:                     # noqa: BLE001 — never lose it
        return ""
