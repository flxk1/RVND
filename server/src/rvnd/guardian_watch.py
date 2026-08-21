# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Guardian watchdog — the guardian's EYES (concept § 1.1/§ 4).

Declarative rules over a replay snapshot of the folder's signed chain:
budget (total events by an agent), rate (events inside a sliding window),
loop (consecutive identical operations), drift (delegated to
``drift_monitor.drift_tick`` — pure read, never re-implemented here).

Everything the watchdog DOES goes through ``guardian.guardian_act`` —
pause + escalate, nothing else. A rule configured with any other action is
an expansion attempt: refused, and the refusal appended to the chain
(test_guardian_watch.py pins this before the logic existed). Drift findings
always escalate, never pause — the within-envelope / reassess / halt choice
is the human's documented 3-option surface (drift_monitor), and the
watchdog must not preempt it.

Determinism: findings are a pure function of (chain snapshot, rules, now).
Absence of evidence is never a finding: no baseline → no drift finding;
empty chain → quiet. Humans are out of scope by construction — the rules
evaluate agent actors only, and ``guardian_act`` refuses human targets
anyway (defense in depth on the un-gateable root path).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .guardian import GUARDIAN_ACTIONS, GuardianRefused, _append, guardian_act
from .mutation_log import MutationLog
from .parties import list_parties

WATCH_RULE_KINDS = ("budget", "rate", "loop", "drift",
                    "queue_stuck", "queue_flood")

# rule kind -> action when the rule does not name one
_DEFAULT_ACTION = {"budget": "pause", "rate": "pause", "loop": "pause",
                   "drift": "escalate",
                   "queue_stuck": "escalate", "queue_flood": "pause"}

# kinds whose findings route to an existing human decision surface and
# therefore may ONLY escalate (the watchdog never preempts those choices:
# drift -> within-envelope/reassess/halt; stuck runs -> resume/mark-failed)
_ESCALATE_ONLY = ("drift", "queue_stuck")


@dataclass(frozen=True)
class WatchRule:
    """One declarative rule. ``party_id`` empty = every registered agent."""
    kind: str
    limit: int = 0
    window_seconds: float = 0.0
    party_id: str = ""
    action: str = ""            # "" = the kind's default

    def resolved_action(self) -> str:
        return self.action or _DEFAULT_ACTION.get(self.kind, "pause")


def _refuse_rule(folder_context: str, guardian_id: str, log_root,
                 rule: WatchRule, why: str) -> None:
    _append(folder_context, guardian_id, log_root, {
        "kind": "GuardianRefused",
        "party_id": rule.party_id,
        "attempted": f"watch-rule {rule.kind}:{rule.action or 'default'}",
        "why": why,
    })
    raise GuardianRefused(why)


def _validate(folder_context: str, guardian_id: str, log_root,
              rules: list[WatchRule]) -> None:
    for r in rules:
        if r.kind not in WATCH_RULE_KINDS:
            _refuse_rule(folder_context, guardian_id, log_root, r,
                         f"unknown watch rule kind {r.kind!r}; known: "
                         f"{WATCH_RULE_KINDS}")
        act = r.resolved_action()
        if act not in GUARDIAN_ACTIONS:
            _refuse_rule(folder_context, guardian_id, log_root, r,
                         f"watch rule action {act!r} outside the monotone "
                         f"vocabulary {GUARDIAN_ACTIONS}; the watchdog "
                         f"restricts, never expands")
        if r.kind in _ESCALATE_ONLY and act != "escalate":
            _refuse_rule(folder_context, guardian_id, log_root, r,
                         f"{r.kind} findings always escalate: they route to "
                         f"an existing human decision surface (drift -> "
                         f"within-envelope/reassess/halt; stuck runs -> "
                         f"resume/mark-failed), not the watchdog's to preempt")


def _agent_events(events, agents: set[str]) -> dict[str, list]:
    by: dict[str, list] = {a: [] for a in agents}
    for e in events:
        if e.actor in by:
            by[e.actor].append(e)
    return by


def _longest_run(evts) -> tuple[int, str]:
    best, run, prev = 0, 0, None
    best_sig = ""
    for e in evts:
        sig = (e.event, e.pair_id)
        run = run + 1 if sig == prev else 1
        prev = sig
        if run > best:
            best, best_sig = run, e.pair_id
    return best, best_sig


def watch_tick(
    folder_context: str,
    rules: list[WatchRule],
    *,
    guardian_id: str = "guardian",
    log_root: Optional[str] = None,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """One deterministic pass: validate rules, compute findings from a chain
    snapshot, then act on each finding through ``guardian_act``."""
    import time as _time
    now = _time.time() if now is None else now
    _validate(folder_context, guardian_id, log_root, list(rules))

    snapshot = list(MutationLog(folder_context, log_root=log_root).replay())
    parties = list_parties(folder_context, log_root=log_root)["parties"]
    agents = {p["party_id"] for p in parties if p.get("party_kind") == "agent"}

    findings: list[dict] = []
    for rule in rules:
        scope = sorted(agents & {rule.party_id} if rule.party_id else agents)
        if rule.kind == "drift":
            from .drift_monitor import drift_tick
            report = drift_tick(folder_context, log_root=log_root, as_of=now)
            if report.no_baseline or report.too_thin or not report.findings:
                continue
            findings.append({"rule": "drift", "party_id": rule.party_id,
                             "action": "escalate",
                             "evidence": report.findings})
            continue
        if rule.kind == "queue_stuck":
            # Detection stays in the queue primitive; the watchdog only
            # carries the evidence to a human. Runs are never mutated here.
            from pathlib import Path as _P

            from .queue import inspect_stuck_runs
            stale = int(rule.window_seconds) if rule.window_seconds else 300
            stuck = inspect_stuck_runs(
                stale_pending_seconds=stale,
                log_root=_P(log_root) if log_root else None)
            if stuck:
                findings.append({"rule": "queue_stuck",
                                 "party_id": rule.party_id,
                                 "action": "escalate",
                                 "count": len(stuck),
                                 "evidence": stuck})
            continue
        if rule.kind == "queue_flood":
            # Budget at the queue: non-terminal entries per enqueuing AGENT
            # (humans out of scope; enqueued_by is the attribution).
            from pathlib import Path as _P

            from .queue import list_queue as _lq
            entries = _lq(log_root=_P(log_root) if log_root else None)
            per: dict[str, int] = {}
            for e in entries:
                if e.state in ("pending", "leased"):
                    per[e.enqueued_by] = per.get(e.enqueued_by, 0) + 1
            scope_f = sorted(agents & {rule.party_id}
                             if rule.party_id else agents)
            for pid in scope_f:
                n = per.get(pid, 0)
                if n > rule.limit:
                    findings.append({"rule": "queue_flood", "party_id": pid,
                                     "action": rule.resolved_action(),
                                     "count": n, "limit": rule.limit})
            continue
        by = _agent_events(snapshot, set(scope))
        for pid in scope:
            evts = [e for e in by[pid] if e.ts <= now]
            if rule.kind == "budget":
                n = len(evts)
                if n > rule.limit:
                    findings.append({"rule": "budget", "party_id": pid,
                                     "action": rule.resolved_action(),
                                     "count": n, "limit": rule.limit})
            elif rule.kind == "rate":
                n = len([e for e in evts
                         if e.ts >= now - rule.window_seconds])
                if n > rule.limit:
                    findings.append({"rule": "rate", "party_id": pid,
                                     "action": rule.resolved_action(),
                                     "count": n, "limit": rule.limit,
                                     "window_seconds": rule.window_seconds})
            elif rule.kind == "loop":
                run, pair = _longest_run(evts)
                if run > rule.limit:
                    findings.append({"rule": "loop", "party_id": pid,
                                     "action": rule.resolved_action(),
                                     "run": run, "limit": rule.limit,
                                     "pair_id": pair})

    actions: list[dict] = []
    for f in findings:
        reason = (f"watchdog {f['rule']}: " +
                  ", ".join(f"{k}={v}" for k, v in f.items()
                            if k not in ("rule", "party_id", "action")))
        if f["party_id"] in agents:
            res = guardian_act(folder_context, f["action"], f["party_id"],
                               reason=reason, guardian_id=guardian_id,
                               log_root=log_root)
        else:   # folder-scope evidence (drift with no named agent)
            audit_id = _append(folder_context, guardian_id, log_root, {
                "kind": "GuardianEscalation", "party_id": "",
                "reason": reason})
            res = {"ok": True, "action": "escalate", "audit_id": audit_id}
        actions.append({"action": f["action"], "party_id": f["party_id"],
                        "rule": f["rule"], "result": res})

    return {"ok": not findings, "now": now,
            "findings": findings, "actions": actions}
