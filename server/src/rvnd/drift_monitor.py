# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Drift monitor — versioned operational-state baselines + a deterministic tick.

Runtime state is versioned architecture: if tool selection, memory updates and
policy bindings are not scoped and replayable, drift and variance become
indistinguishable and "substantial modification" (Art. 3(23)) is unmeasurable
by design. The mutation log already makes every state replayable; this module
adds the missing monitoring half (the conformity-runtime design, C0
item 2):

  1. ``baseline(folder)`` — snapshot the folder's operational state (policy
     bindings, pinned-skill set, optional catalogue fingerprint, behavioural
     mix projected from the log) as a signed ``drift-baseline`` event.
  2. ``drift_tick(folder)`` — deterministic diff of the current state against
     the latest baseline. Same log + same ``as_of`` ⇒ same report. Pure read;
     recording is explicit and idempotent (``record_findings``).
  3. Threshold crossings become FINDINGS that route to the decision surface
     with the options {within-envelope → re-baseline | reassess | halt}.
     **Terminal-for-machine**: the monitor never declares substantial
     modification — same discipline as ``breached_candidate`` in the
     obligation runtime. The surfaced options + the human's recorded
     rationale ARE the documented Art. 3(23) determination procedure.

No daemon. Call the tick from an MCP op, a cron line, or a test — the
``obligation_scheduler.tick`` pattern. Pure stdlib.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .mutation_log import LogEvent, MutationLog
from .pinned_skills import load_pinned_skills
from .policy import load_policy

__all__ = ["DriftThresholds", "DriftReport", "baseline", "drift_tick",
           "record_findings", "finding_surface", "DEFAULT_THRESHOLDS"]


# ── thresholds ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DriftThresholds:
    """Conservative defaults; per-folder overrides travel through the MCP layer.

    ``share_shift`` — a behavioural category (event type, channel, actor kind,
    dispatched skill) whose share of traffic moves by more than this absolute
    amount against the baseline mix is a finding.
    ``min_events`` — behavioural comparison needs at least this many events
    since the baseline; below it the window is reported as ``too_thin``
    (surfaced, never guessed — no finding fires on noise).
    """
    share_shift: float = 0.15
    min_events: int = 20


DEFAULT_THRESHOLDS = DriftThresholds()

_BASELINE_KIND = "drift-baseline"
_FINDING_KIND = "drift-finding"


# ── operational state projection ─────────────────────────────────────────────

def _policy_bindings(folder: str | Path) -> dict[str, Any]:
    p = load_policy(folder)
    return {
        "privacy_lock_enabled": p.privacy_lock_enabled,
        "lock_is_active": p.lock_is_active,
        "lock_mode": p.lock_mode,
        "oversight_enabled": p.oversight_enabled,
        "oversight_is_active": p.oversight_is_active,
        "oversight_default_level": p.oversight_default_level,
        "acknowledgements": sorted(p.acknowledgements.keys()),
    }


def _pinned(folder: str | Path, log_root: Optional[Path]) -> list[str]:
    try:
        store = load_pinned_skills(folder, log_root=log_root)
    except Exception:                                   # noqa: BLE001
        return ["<pinned-skills-store-unreadable>"]
    return sorted(s.id for s in store.skills)


def _behaviour_mix(events: Iterable[LogEvent]) -> dict[str, Any]:
    """Histogram projection of a span of events. Deterministic."""
    n = 0
    by_event: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    by_actor_kind: dict[str, int] = {}
    by_skill: dict[str, int] = {}
    escalations = 0
    for e in events:
        n += 1
        by_event[e.event] = by_event.get(e.event, 0) + 1
        by_channel[e.channel] = by_channel.get(e.channel, 0) + 1
        kind = "agent" if str(e.actor).startswith("agent:") else (
            "user" if e.actor == "user" else "system")
        by_actor_kind[kind] = by_actor_kind.get(kind, 0) + 1
        x = e.extra or {}
        if x.get("kind") in ("workflow-event", "skill-dispatch") and x.get("skill_id"):
            by_skill[str(x["skill_id"])] = by_skill.get(str(x["skill_id"]), 0) + 1
        if x.get("kind") == "residual-decision":
            escalations += 1
    return {"n": n, "by_event": by_event, "by_channel": by_channel,
            "by_actor_kind": by_actor_kind, "by_skill": by_skill,
            "escalations": escalations}


def _operational_state(folder: str | Path, *, log_root: Optional[Path],
                       catalogue_fingerprint: str,
                       events: Iterable[LogEvent]) -> dict[str, Any]:
    return {"policy": _policy_bindings(folder),
            "pinned_skills": _pinned(folder, log_root),
            "catalogue_fingerprint": catalogue_fingerprint,
            "behaviour": _behaviour_mix(events)}


# ── baseline ──────────────────────────────────────────────────────────────────

def baseline(folder: str | Path, *, log_root: Optional[Path] = None,
             catalogue_fingerprint: str = "", actor: str = "user",
             note: str = "") -> dict[str, Any]:
    """Snapshot the folder's operational state as a signed baseline event.

    Setting (and re-setting) a baseline is a deliberate act — default actor is
    ``user`` because the conformity envelope is a human assertion, not a
    machine guess. Re-baselining after a finding goes through the decision
    surface (option ``re-baseline`` + rationale), then calls this.
    """
    log = MutationLog(folder, log_root=log_root)
    state = _operational_state(folder, log_root=log_root,
                               catalogue_fingerprint=catalogue_fingerprint,
                               events=log.replay())
    audit_id = log.append(LogEvent(
        event="system", folder_path=str(folder), pair_id=_BASELINE_KIND,
        channel="system", actor=actor,
        extra={"kind": _BASELINE_KIND, "state": state, "note": note}))
    return {"audit_id": audit_id, "state": state}


def _latest_baseline(log: MutationLog) -> Optional[tuple[LogEvent, dict]]:
    found = None
    for e in log.replay():
        if e.pair_id == _BASELINE_KIND and (e.extra or {}).get("kind") == _BASELINE_KIND:
            found = (e, (e.extra or {}).get("state") or {})
    return found


# ── the tick ──────────────────────────────────────────────────────────────────

@dataclass
class DriftReport:
    folder: str
    as_of: float
    baseline_audit_id: str = ""
    baseline_ts: float = 0.0
    structural: list[dict] = field(default_factory=list)   # config drift — no threshold
    behavioural: list[dict] = field(default_factory=list)  # share shifts past threshold
    too_thin: bool = False        # not enough events since baseline to compare
    window_n: int = 0
    no_baseline: bool = False

    @property
    def findings(self) -> list[dict]:
        return self.structural + self.behavioural

    @property
    def ok(self) -> bool:
        return not self.no_baseline and not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {"folder": self.folder, "as_of": self.as_of,
                "baseline_audit_id": self.baseline_audit_id,
                "baseline_ts": self.baseline_ts,
                "structural": self.structural, "behavioural": self.behavioural,
                "too_thin": self.too_thin, "window_n": self.window_n,
                "no_baseline": self.no_baseline, "ok": self.ok}


def _shares(hist: dict[str, int]) -> dict[str, float]:
    total = sum(hist.values()) or 1
    return {k: v / total for k, v in hist.items()}


def _share_drift(name: str, base_hist: dict[str, int], now_hist: dict[str, int],
                 limit: float) -> list[dict]:
    base_s, now_s = _shares(base_hist or {}), _shares(now_hist or {})
    out = []
    for key in sorted(set(base_s) | set(now_s)):
        delta = abs(now_s.get(key, 0.0) - base_s.get(key, 0.0))
        if delta > limit:
            out.append({"metric": f"{name}:{key}",
                        "baseline_share": round(base_s.get(key, 0.0), 4),
                        "current_share": round(now_s.get(key, 0.0), 4),
                        "delta": round(delta, 4), "threshold": limit})
    return out


def drift_tick(folder: str | Path, *, log_root: Optional[Path] = None,
               catalogue_fingerprint: str = "",
               thresholds: DriftThresholds = DEFAULT_THRESHOLDS,
               as_of: Optional[float] = None) -> DriftReport:
    """Deterministic diff of current operational state against the latest
    baseline. Pure read — writes nothing (see :func:`record_findings`)."""
    log = MutationLog(folder, log_root=log_root)
    as_of = as_of if as_of is not None else time.time()
    report = DriftReport(folder=str(folder), as_of=as_of)

    found = _latest_baseline(log)
    if found is None:
        report.no_baseline = True
        return report
    base_event, base_state = found
    report.baseline_audit_id = base_event.audit_id
    report.baseline_ts = base_event.ts

    # Structural drift: policy bindings, pinned skills, catalogue fingerprint.
    # Config changes have no threshold — every one is a finding (it may be a
    # legitimate, logged change; the human says so on the surface, with reasons).
    now_policy = _policy_bindings(folder)
    for k in sorted(set(base_state.get("policy", {})) | set(now_policy)):
        b, n = base_state.get("policy", {}).get(k), now_policy.get(k)
        if b != n:
            report.structural.append(
                {"metric": f"policy:{k}", "baseline": b, "current": n})
    b_pin = base_state.get("pinned_skills", [])
    n_pin = _pinned(folder, log_root)
    if b_pin != n_pin:
        report.structural.append(
            {"metric": "pinned_skills",
             "added": sorted(set(n_pin) - set(b_pin)),
             "removed": sorted(set(b_pin) - set(n_pin))})
    b_cat = base_state.get("catalogue_fingerprint", "")
    if catalogue_fingerprint != b_cat:
        report.structural.append(
            {"metric": "catalogue_fingerprint",
             "baseline": b_cat, "current": catalogue_fingerprint})

    # Behavioural drift: the mix of events SINCE the baseline vs. the mix the
    # baseline recorded. Thin windows are surfaced, never compared.
    window = [e for e in log.replay()
              if e.ts > base_event.ts and e.ts <= as_of
              and e.pair_id not in (_BASELINE_KIND, _FINDING_KIND)]
    mix_now = _behaviour_mix(window)
    report.window_n = mix_now["n"]
    if mix_now["n"] < thresholds.min_events:
        report.too_thin = True
        return report
    mix_base = base_state.get("behaviour", {})
    for name in ("by_event", "by_channel", "by_actor_kind", "by_skill"):
        report.behavioural.extend(_share_drift(
            name, mix_base.get(name, {}), mix_now.get(name, {}),
            thresholds.share_shift))
    return report


# ── recording + the decision surface ─────────────────────────────────────────

def record_findings(report: DriftReport, *, log_root: Optional[Path] = None,
                    actor: str = "system") -> list[str]:
    """Write one ``drift-finding`` event per finding, idempotently: a finding
    is keyed by (baseline_audit_id, metric) and never written twice."""
    if not report.findings:
        return []
    log = MutationLog(report.folder, log_root=log_root)
    seen = set()
    for e in log.replay():
        x = e.extra or {}
        if x.get("kind") == _FINDING_KIND:
            seen.add((x.get("baseline_audit_id"), x.get("metric")))
    out = []
    for f in report.findings:
        key = (report.baseline_audit_id, f["metric"])
        if key in seen:
            continue
        out.append(log.append(LogEvent(
            event="system", folder_path=report.folder, pair_id=_FINDING_KIND,
            channel="system", actor=actor,
            extra={"kind": _FINDING_KIND,
                   "baseline_audit_id": report.baseline_audit_id,
                   "metric": f["metric"], "finding": f})))
    return out


def finding_surface(report: DriftReport):
    """Route the report's findings to the human as a residual choice.

    Three options, none recommended, rationale required (the existing
    ``decision_surface.record_choice`` discipline). The machine never decides
    whether a drift is a substantial modification — it builds the surface.
    """
    from .decisions.surface import build_surface
    if not report.findings:
        raise ValueError("no findings — nothing to decide")
    detail = "; ".join(f["metric"] for f in report.findings)
    return build_surface(
        query=(f"Operational drift detected against baseline "
               f"{report.baseline_audit_id} in folder {report.folder}"),
        candidates=[
            {"id": "within-envelope",
             "label": "Within the assessed envelope — re-baseline",
             "conclusion": "The change is anticipated adaptive behaviour; "
                           "record why and set a new baseline.",
             "consequences": ["a new drift-baseline event is written",
                              "the rationale becomes part of the audit chain"]},
            {"id": "reassess",
             "label": "Outside the envelope — reassess before continuing",
             "conclusion": "The change may affect the assessed risk profile; "
                           "rerun the risk assessment for this folder.",
             "consequences": ["folder stays operational",
                              "reassessment is tracked as an open obligation"]},
            {"id": "halt",
             "label": "Halt the folder pending review",
             "conclusion": "Suspend autonomous operation in this folder until "
                           "a human review completes.",
             "consequences": ["agentic dispatch should be refused by the host",
                              "interactive use may continue"]},
        ],
        esc_reason=f"drift findings: {detail}",
        context="Art. 3(23) determination — the choice and its rationale are "
                "the documented procedure.")
