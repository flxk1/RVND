# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Incident escalation — detection already exists; this wires it to a human.

C0 item 3 of the conformity-runtime design. The substrate detects
tampering (``verify_chain``), records oversight bypasses (``llm_capture``
stamps ``oversight_bypassed=True``), refuses disk-full writes loudly
(``DiskFullError``), and surfaces drift findings (``drift_monitor``) — but
until now nothing routed a detection to the responsible human. Serious-
incident readiness (Art. 73; QMS post-market clause) is precisely that
routing: detect → record → surface → human determination with rationale.

Same discipline as the schedulers: no daemon, an explicit ``scan`` you call
from an MCP op, a cron line, or a test; deterministic over the same log;
recording is idempotent; the machine never closes an incident — closure is a
decision-surface choice with rationale. Pure stdlib.

Incident classes
----------------
chain-verification-failure   broken hash links / signature failures / malformed lines
host-divergence              advisory host_id shift mid-chain (possible re-signed rewrite)
oversight-bypassed           agentic capture ran with oversight disabled
no-go-storm                  ≥ threshold NO-GO gate verdicts in the scan window
drift-unresolved             drift findings with no recorded human determination
runtime-exception            reported at a catch site via :func:`report_exception`
                             (DiskFullError and peers — a full disk cannot be
                             found by scanning the disk)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog

__all__ = ["IncidentReport", "scan", "record_incidents", "incident_surface",
           "report_exception", "log_gate_decision", "NO_GO_STORM_THRESHOLD"]

NO_GO_STORM_THRESHOLD = 5          # NO-GOs in one scan window = a storm
_INCIDENT_KIND = "incident"
_GATE_KIND = "gate-verdict"


# ── making gate verdicts scannable ───────────────────────────────────────────

def log_gate_decision(folder: str | Path, decision: Any, *,
                      log_root: Optional[Path] = None,
                      actor: str = "system",
                      run_id: str = "", step_index: Optional[int] = None) -> str:
    """Write one gate verdict to the folder's log in the scannable shape.

    ``decision`` is an ``action_gate.GateDecision`` (or its ``to_dict()``).
    Callers that gate actions (workflow runner, schedulers, dispatch) use
    this so the NO-GO storm scan reads real verdicts, not reconstructions.

    ``run_id``/``step_index`` bind this verdict to the run + step it authorised,
    so the effect ledger can tie an observed step outcome back to its
    authorisation by identity — BOUND, not inferred (the complete-mediation
    reconciliation reads these). Absent for non-workflow gate calls, which is
    harmless: an unbindable authorisation simply is not reconciled.
    """
    d = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
    extra = {"kind": _GATE_KIND, "decision": d}
    if run_id:
        extra["run_id"] = run_id
    if step_index is not None:
        extra["step_index"] = step_index
    log = MutationLog(folder, log_root=log_root)
    return log.append(LogEvent(
        event="system", folder_path=str(folder), pair_id=_GATE_KIND,
        channel="system", actor=actor, extra=extra))


# ── the report ────────────────────────────────────────────────────────────────

@dataclass
class Incident:
    klass: str
    fingerprint: str            # idempotence key within (klass)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"class": self.klass, "fingerprint": self.fingerprint,
                "detail": self.detail}


@dataclass
class IncidentReport:
    folder: str
    as_of: float
    incidents: list[Incident] = field(default_factory=list)
    window_events: int = 0

    @property
    def ok(self) -> bool:
        return not self.incidents

    def to_dict(self) -> dict[str, Any]:
        return {"folder": self.folder, "as_of": self.as_of, "ok": self.ok,
                "window_events": self.window_events,
                "incidents": [i.to_dict() for i in self.incidents]}


# ── the scan ──────────────────────────────────────────────────────────────────

def scan(folder: str | Path, *, log_root: Optional[Path] = None,
         as_of: Optional[float] = None, window_s: float = 7 * 24 * 3600.0,
         no_go_threshold: int = NO_GO_STORM_THRESHOLD) -> IncidentReport:
    """Deterministic sweep of one folder for open incident conditions.

    Pure read. ``window_s`` bounds the behavioural checks (storms); integrity
    checks (chain) always run over the whole log — tampering does not expire.
    """
    as_of = as_of if as_of is not None else time.time()
    report = IncidentReport(folder=str(folder), as_of=as_of)
    log = MutationLog(folder, log_root=log_root)

    # 1. Chain integrity — the whole log, always.
    chain = log.verify_chain()
    if not chain.ok:
        report.incidents.append(Incident(
            "chain-verification-failure",
            fingerprint=f"links:{len(chain.broken_links)}/"
                        f"sigs:{len(chain.signature_failures)}/"
                        f"malformed:{chain.malformed_lines}",
            detail={"broken_links": chain.broken_links,
                    "signature_failures": chain.signature_failures,
                    "malformed_lines": chain.malformed_lines}))
    if chain.host_divergence_warning:
        report.incidents.append(Incident(
            "host-divergence",
            fingerprint=str(chain.host_divergence_warning[0].get("audit_id", "")),
            detail={"warnings": chain.host_divergence_warning}))

    # 2. Log-derived conditions inside the window.
    lo = as_of - window_s
    no_gos: list[dict] = []
    bypassed: list[str] = []
    drift_findings: list[tuple[float, str]] = []
    determinations_after: float = 0.0
    for e in log.replay():
        if e.ts > as_of:
            continue
        x = e.extra or {}
        if x.get("kind") == "residual-decision":
            determinations_after = max(determinations_after, e.ts)
        if e.ts < lo:
            continue
        report.window_events += 1
        if x.get("kind") == _GATE_KIND:
            verdict = (x.get("decision") or {}).get("verdict")
            if verdict == "NO-GO":
                no_gos.append({"audit_id": e.audit_id, "ts": e.ts,
                               "triple": (x.get("decision") or {}).get("audit_triple", {})})
        if x.get("kind") == "drift-finding":
            drift_findings.append((e.ts, e.audit_id))
        if x.get("oversight_bypassed") is True and str(e.actor).startswith("agent:"):
            bypassed.append(e.audit_id)

    if len(no_gos) >= no_go_threshold:
        report.incidents.append(Incident(
            "no-go-storm",
            fingerprint=no_gos[-1]["audit_id"],
            detail={"count": len(no_gos), "threshold": no_go_threshold,
                    "events": no_gos}))
    for aid in bypassed:
        report.incidents.append(Incident(
            "oversight-bypassed", fingerprint=aid, detail={"audit_id": aid}))
    open_findings = [aid for ts, aid in drift_findings if ts > determinations_after]
    if open_findings:
        report.incidents.append(Incident(
            "drift-unresolved", fingerprint=open_findings[-1],
            detail={"finding_audit_ids": open_findings}))
    return report


# ── recording (idempotent) + the surface ─────────────────────────────────────

def record_incidents(report: IncidentReport, *, log_root: Optional[Path] = None,
                     actor: str = "system") -> list[str]:
    """One ``incident`` event per (class, fingerprint), never twice."""
    if report.ok:
        return []
    log = MutationLog(report.folder, log_root=log_root)
    seen = set()
    for e in log.replay():
        x = e.extra or {}
        if x.get("kind") == _INCIDENT_KIND:
            seen.add((x.get("class"), x.get("fingerprint")))
    out = []
    for inc in report.incidents:
        if (inc.klass, inc.fingerprint) in seen:
            continue
        out.append(log.append(LogEvent(
            event="system", folder_path=report.folder, pair_id=_INCIDENT_KIND,
            channel="system", actor=actor,
            extra={"kind": _INCIDENT_KIND, "class": inc.klass,
                   "fingerprint": inc.fingerprint, "detail": inc.detail})))
    return out


def report_exception(folder: str | Path, exc: BaseException, *,
                     log_root: Optional[Path] = None,
                     context: str = "") -> dict[str, Any]:
    """Catch-site reporter for runtime exceptions (DiskFullError and peers).

    Best-effort by necessity: a full disk may also refuse the incident write.
    Never raises; returns ``{"logged": bool, ...}`` so the caller can fall back
    to stderr/host notification when the log itself is unavailable.
    """
    inc = Incident("runtime-exception",
                   fingerprint=f"{type(exc).__name__}:{context}",
                   detail={"type": type(exc).__name__, "message": str(exc),
                           "context": context})
    try:
        log = MutationLog(folder, log_root=log_root)
        audit_id = log.append(LogEvent(
            event="system", folder_path=str(folder), pair_id=_INCIDENT_KIND,
            channel="system", actor="system",
            extra={"kind": _INCIDENT_KIND, **inc.to_dict()}))
        return {"logged": True, "audit_id": audit_id, **inc.to_dict()}
    except Exception as write_exc:                      # noqa: BLE001
        return {"logged": False, "write_error": str(write_exc), **inc.to_dict()}


def incident_surface(report: IncidentReport):
    """Route open incidents to the human. The machine never closes an
    incident; ``acknowledge`` / ``investigate`` / ``halt`` are originated
    choices with rationale (``decision_surface.record_choice``)."""
    from .decisions.surface import build_surface
    if report.ok:
        raise ValueError("no incidents — nothing to decide")
    classes = sorted({i.klass for i in report.incidents})
    return build_surface(
        query=(f"{len(report.incidents)} open incident(s) in folder "
               f"{report.folder}: {', '.join(classes)}"),
        candidates=[
            {"id": "acknowledge",
             "label": "Acknowledge — known cause, no further action",
             "conclusion": "The condition is explained (e.g. an authorised "
                           "erasure, a planned policy change). Record why.",
             "consequences": ["incident closed by this determination",
                              "rationale joins the audit chain"]},
            {"id": "investigate",
             "label": "Investigate — open a review before further autonomy",
             "conclusion": "Cause unclear; review the cited events before "
                           "the folder runs unattended again.",
             "consequences": ["incident stays open",
                              "review outcome is recorded as a follow-up decision"]},
            {"id": "halt",
             "label": "Halt the folder pending review",
             "conclusion": "Treat as potentially serious; suspend autonomous "
                           "operation in this folder.",
             "consequences": ["agentic dispatch should be refused by the host",
                              "interactive use may continue"]},
        ],
        esc_reason=f"incident classes: {', '.join(classes)}",
        context="Serious-incident readiness (Art. 73): detection, record, and "
                "human determination form one evidentiary chain.")
