# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Conformity projections — evidence as API (C1).

Compliance vendors sell documentation; documentation cannot satisfy
"effective oversight *during use*". Only a runtime can. This module is the
read side of that claim: six projections over the signed mutation log, each
keyed to the standard clause it evidences. Nothing here writes state — an
evidence export that mutated the evidence would be worthless — and every
projection is deterministic over the same log (no wall-clock defaults except
where explicitly passed).

Ops and their hooks (clause references version-pinned to the January 2026
Enquiry drafts; all legal readings pending expert review):

    evidence_pack          Art. 12; prEN 18229-1; prEN ISO/IEC 24970
    oversight_attestation  Art. 14; the action-level oversight record
    trigger_map            adjacent-legislation inventory (external actions →
                           which instruments they activate)
    drift_report           Art. 3(23), Art. 72; prEN 18286 cl. 9.4
    risk_register          Art. 9; prEN 18228 (autonomy level as a system
                           characteristic; the automation boundary per
                           footprint class)
    threat_model           Art. 15(4); prEN 18282 (adversarial test suite
                           mapped onto the OWASP agentic threat taxonomy)

The claim this module supports is "produces the evidence the articles
require" — never "compliant". Pure stdlib.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .action_gate import _POSTURE_SHIFT, _RISK_MIN_GRADE  # noqa: F401 — ontology is the register
from .adapters.policy_languages import grade_levels
from .mutation_log import LogEvent, MutationLog
from .pinned_skills import load_pinned_skills
from .policy import load_policy

__all__ = ["evidence_pack", "oversight_attestation", "trigger_map",
           "drift_report", "risk_register", "threat_model", "OPS",
           "load_regime", "REFERENCE_REGIME"]

OPS = ("evidence_pack", "oversight_attestation", "trigger_map",
       "drift_report", "risk_register", "threat_model")

# Jurisdiction neutrality (principal's ruling: concepts are substrate,
# citations are pack data). The conformity ENGINE below — projecting the
# signed log into evidence — is jurisdiction-neutral and cites no statute.
# The legal LABELS (which instrument an action triggers, which article a
# projection evidences, the disclosure marking) live in a REGIME PACK, loaded
# and supplied by a caller; the substrate applies NONE unless one is given.
# The EU AI Act reference regime ships as DATA, the worked example — same rule
# as the contract jurisdiction packs. A regime can never lower the engine's
# guarantees; it only adds labels.
import json as _json
from pathlib import Path as _Path

REGIME_PACKS_DIR = _Path(__file__).resolve().parent / "data" / "packs"
REFERENCE_REGIME = REGIME_PACKS_DIR / "eu-ai-act-conformity.json"

# Neutral basis lines used when NO regime is supplied — engine description,
# no statute. A regime's ``op_basis`` overrides per op when one is loaded.
_NEUTRAL_BASIS = {
    "evidence_pack": "operations projected from the signed mutation log; "
                     "no regime loaded — legal-basis labels omitted.",
    "oversight_attestation": "human determinations + rationale and oversight "
                     "bypasses projected from the log; no regime loaded.",
    "trigger_map": "external-action inventory from gate history + workflow "
                   "definitions; no regime loaded — instruments omitted.",
    "drift_report": "operational-state baselines vs current, projected from "
                    "the log; no regime loaded.",
    "risk_register": "automation boundary (as designed × as exercised) from "
                     "the footprint ontology + verdict history; no regime loaded.",
    "threat_model": "adversarial regression coverage by category; no regime "
                    "loaded — framework mapping omitted.",
}


def load_regime(path=REFERENCE_REGIME) -> dict[str, Any]:
    """Load a conformity regime pack (the legal labels). Returns the parsed
    dict. Callers pass the result as ``regime=`` to the ops; the substrate
    default is no regime (neutral output)."""
    return _json.loads(_Path(path).read_text(encoding="utf-8"))


def _basis(op: str, regime: Optional[dict]) -> str:
    if regime:
        return (regime.get("op_basis", {}).get(op)
                or _NEUTRAL_BASIS.get(op, ""))
    return _NEUTRAL_BASIS.get(op, "")


def _regime_id(regime: Optional[dict]) -> str:
    return regime.get("id", "custom") if regime else "none"


# Marker for fields a draft standard names but the runtime does not carry —
# stated, never invented (the NT-2 discipline extends to evidence exports).
NOT_SPECIFIED = "not-specified-in-runtime"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _actor_kind(actor: str) -> str:
    if actor == "user":
        return "user"
    if str(actor).startswith("agent:"):
        return "agent"
    return "system"


def _window(log: MutationLog, since: Optional[float],
            until: Optional[float]) -> Iterable[LogEvent]:
    for e in log.replay():
        if since is not None and e.ts < since:
            continue
        if until is not None and e.ts > until:
            continue
        yield e


# ── 1. evidence pack (Art. 12 / 24970) ───────────────────────────────────────

def evidence_pack(folder: str | Path, *, log_root: Optional[Path] = None,
                  since: Optional[float] = None,
                  until: Optional[float] = None,
                  regime: Optional[dict] = None) -> dict[str, Any]:
    """Period export: every recorded operation with actor, initiation kind,
    signature presence, and its place in the verified chain.

    Each record resolves to one signed log event by ``event_id`` (= the
    event's ``audit_id``) — the export is a *projection* of the chain, so a
    reviewer can replay the log and reconcile the pack line by line.

    24970 alignment note: field mapping follows the operational-event-record
    intent of prEN ISO/IEC 24970 as scoped in the Jan 2026 Enquiry draft;
    fields the draft names that this runtime does not carry are exported as
    ``not-specified-in-runtime`` rather than invented. Pending expert review.
    """
    log = MutationLog(folder, log_root=log_root)
    chain = log.verify_chain()
    records: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for e in _window(log, since, until):
        x = e.extra or {}
        kind = x.get("kind") or e.event
        counts[kind] = counts.get(kind, 0) + 1
        rec = {
            "event_id": e.audit_id,
            "timestamp": _iso(e.ts),
            "event_type": e.event,
            "record_kind": kind,
            "channel": e.channel,
            "actor": e.actor,
            "initiation": _actor_kind(e.actor),     # user- vs AI-initiated (Art. 15(4))
            "pair_id": e.pair_id,
            "signature_present": bool(e.signature),
            "host_id": e.host_id or NOT_SPECIFIED,
            # 24970-named fields without a runtime source — stated, not invented:
            "decision_model_identifier": NOT_SPECIFIED,
            "training_data_provenance": NOT_SPECIFIED,
        }
        if kind == "gate-verdict":
            d = x.get("decision") or {}
            rec["detail"] = {"verdict": d.get("verdict"),
                             "reason": d.get("reason"),
                             "telemetry": (d.get("audit_triple") or {}).get("telemetry")}
        elif kind in ("residual-decision",):
            rec["detail"] = {"chosen": x.get("chosen_option_id"),
                             "rationale": x.get("rationale"),
                             "decider": x.get("actor")}
        elif kind in ("workflow-event",):
            rec["detail"] = {"workflow": x.get("workflow"),
                             "step_index": x.get("step_index"),
                             "state": x.get("state"),
                             "skill_id": x.get("skill_id")}
        elif kind in ("incident", "drift-finding", "drift-baseline"):
            rec["detail"] = {k: v for k, v in x.items() if k != "kind"}
        records.append(rec)
    return {
        "op": "evidence_pack",
        "folder": str(folder),
        "period": {"since": _iso(since) if since else "log-start",
                   "until": _iso(until) if until else "log-end"},
        "chain": {"ok": chain.ok, "total_events": chain.total_events,
                  "broken_links": len(chain.broken_links),
                  "signature_failures": len(chain.signature_failures),
                  "unsigned_events": chain.unsigned_events,
                  "purged_with_tombstone": chain.purged_with_tombstone,
                  "key_pin": getattr(chain, "key_pin", None)},
        "counts_by_kind": dict(sorted(counts.items())),
        "records": records,
        "regime": _regime_id(regime),
        "basis": _basis("evidence_pack", regime),
    }


# ── 2. oversight attestation (Art. 14, fn. 18/20) ───────────────────────────

def oversight_attestation(folder: str | Path, *, log_root: Optional[Path] = None,
                          since: Optional[float] = None,
                          until: Optional[float] = None,
                          regime: Optional[dict] = None) -> dict[str, Any]:
    """Evidence that oversight was *operationalised during use*: every human
    determination with its rationale, every CONDITIONAL that was released by a
    recorded sign-off, and — on the failure side — every agentic operation
    that ran with oversight bypassed and every finding still awaiting a human.

    ``attested`` is True only when the period contains no agentic bypass and
    no undetermined finding. The attestation never asserts more than the log
    shows.
    """
    log = MutationLog(folder, log_root=log_root)
    determinations: list[dict] = []
    conditional_releases: list[dict] = []
    bypassed: list[dict] = []
    gate_counts = {"GO": 0, "CONDITIONAL": 0, "NO-GO": 0}
    finding_ts: list[tuple[float, str]] = []
    last_determination_ts = 0.0
    for e in _window(log, since, until):
        x = e.extra or {}
        kind = x.get("kind")
        if kind == "residual-decision":
            last_determination_ts = max(last_determination_ts, e.ts)
            determinations.append({
                "event_id": e.audit_id, "timestamp": _iso(e.ts),
                "decider": x.get("actor") or e.actor,
                "chosen": x.get("chosen_option_id"),
                "rationale_present": bool((x.get("rationale") or "").strip()),
                "esc_reason": x.get("esc_reason", "")})
        elif kind == "gate-verdict":
            v = (x.get("decision") or {}).get("verdict")
            if v in gate_counts:
                gate_counts[v] += 1
        elif kind == "workflow-event" and (
                (x.get("kind2") == "workflow-thread"
                 and x.get("verdict") == "approved-by-human")
                or x.get("kind2") == "gate-release"):
            conditional_releases.append({
                "event_id": e.audit_id, "timestamp": _iso(e.ts),
                "release_kind": x.get("kind2"),
                "step_index": x.get("step_index", x.get("to_step")),
                "rationale_present": bool((x.get("approval_rationale") or "").strip())})
        elif kind in ("drift-finding", "incident"):
            finding_ts.append((e.ts, e.audit_id))
        if x.get("oversight_bypassed") is True and _actor_kind(e.actor) == "agent":
            bypassed.append({"event_id": e.audit_id, "timestamp": _iso(e.ts),
                             "actor": e.actor})
    undetermined = [aid for ts, aid in finding_ts if ts > last_determination_ts]
    attested = not bypassed and not undetermined
    return {
        "op": "oversight_attestation",
        "folder": str(folder),
        "period": {"since": _iso(since) if since else "log-start",
                   "until": _iso(until) if until else "log-end"},
        "attested": attested,
        "statement": ("Every recorded escalation in the period carries a human "
                      "determination with rationale; no agentic operation ran "
                      "with oversight bypassed." if attested else
                      "Attestation FAILS for the period — see bypassed_events "
                      "and undetermined_findings."),
        "determinations": determinations,
        "conditional_releases": conditional_releases,
        "gate_verdicts": gate_counts,
        "bypassed_events": bypassed,
        "undetermined_findings": undetermined,
        "regime": _regime_id(regime),
        "basis": _basis("oversight_attestation", regime),
    }


# ── 3. regulatory trigger map (the Step-9 inventory) ─────────────────────────
# The footprint → instrument mapping and the operator questions are REGIME
# (legal) data, supplied by a regime pack — not substrate. Without a regime
# the engine still produces the inventory (action classes, footprints, agents,
# verdicts); it just omits the instrument labels.

def trigger_map(folder: str | Path, *,
                log_root: Optional[Path] = None,
                regime: Optional[dict] = None) -> dict[str, Any]:
    """The external-action inventory: every action class the folder has gated
    or defined, its observed footprints, and (if a regime is supplied) the
    instruments those footprints activate. The engine generates the inventory
    jurisdiction-neutrally; instrument labels and operator questions come from
    the regime pack, never from substrate.

    This is the foundational inventory — external actions, data flows,
    connected systems, affected persons — generated rather than hand-written.
    """
    footprint_instruments = (regime or {}).get("footprint_instruments", {})
    operator_questions = (regime or {}).get("operator_questions", [])
    log = MutationLog(folder, log_root=log_root)
    actions: dict[str, dict[str, Any]] = {}
    for e in log.replay():
        x = e.extra or {}
        if x.get("kind") != "gate-verdict":
            continue
        d = x.get("decision") or {}
        t = d.get("audit_triple") or {}
        ac = t.get("object") or ""
        if not ac:
            continue
        a = actions.setdefault(ac, {"action_class": ac, "footprints": set(),
                                    "agents": set(), "verdicts": {},
                                    "last_seen": ""})
        a["footprints"].update(t.get("footprint") or [])
        a["agents"].add(t.get("subject") or "")
        v = d.get("verdict") or "?"
        a["verdicts"][v] = a["verdicts"].get(v, 0) + 1
        a["last_seen"] = max(a["last_seen"], _iso(e.ts))
    # Workflow definitions contribute declared (static) footprints too.
    try:
        from .workflows import list_workflows, load_workflow
        for meta in list_workflows(folder, include_ancestors=True,
                                   log_root=log_root)["workflows"]:
            wf = load_workflow(meta["defined_in"], meta["name"], log_root=log_root)
            if wf is None:
                continue
            for s in wf.steps:
                ac = f"dispatch:{s.skill_id}"
                a = actions.setdefault(ac, {"action_class": ac, "footprints": set(),
                                            "agents": set(), "verdicts": {},
                                            "last_seen": ""})
                a["footprints"].update(s.footprint)
                a["agents"].add(f"workflow:{wf.name}")
    except Exception:                                   # noqa: BLE001
        pass
    rows = []
    union_instruments: set[str] = set()
    for ac in sorted(actions):
        a = actions[ac]
        instruments = sorted({i for f in a["footprints"]
                              for i in footprint_instruments.get(f, [])})
        union_instruments.update(instruments)
        no_label = ("no regime loaded — instruments omitted" if not regime
                    else "none derivable from footprints")
        rows.append({"action_class": ac,
                     "footprints": sorted(a["footprints"]),
                     "agents": sorted(x for x in a["agents"] if x),
                     "verdicts": a["verdicts"],
                     "last_seen": a["last_seen"],
                     "instruments": instruments or [no_label]})
    try:
        pinned = sorted(s.id for s in load_pinned_skills(folder, log_root=log_root).skills)
    except Exception:                                   # noqa: BLE001
        pinned = []
    return {
        "op": "trigger_map",
        "folder": str(folder),
        "actions": rows,
        "pinned_skills": pinned,
        "instruments_union": sorted(union_instruments),
        "operator_questions": operator_questions,
        "regime": _regime_id(regime),
        "basis": _basis("trigger_map", regime),
    }


# ── 4. drift report ──────────────────────────────────────────────────────────

def drift_report(folder: str | Path, *, log_root: Optional[Path] = None,
                 catalogue_fingerprint: str = "",
                 as_of: Optional[float] = None,
                 regime: Optional[dict] = None) -> dict[str, Any]:
    """The drift monitor's current posture for the folder: latest baseline,
    tick result, and any finding still awaiting a determination."""
    from .drift_monitor import drift_tick
    log = MutationLog(folder, log_root=log_root)
    baselines = []
    last_det = 0.0
    open_findings = []
    for e in log.replay():
        x = e.extra or {}
        if x.get("kind") == "drift-baseline":
            baselines.append({"event_id": e.audit_id, "timestamp": _iso(e.ts),
                              "actor": e.actor, "note": x.get("note", "")})
        elif x.get("kind") == "residual-decision":
            last_det = max(last_det, e.ts)
    for e in log.replay():
        x = e.extra or {}
        if x.get("kind") == "drift-finding" and e.ts > last_det:
            open_findings.append({"event_id": e.audit_id,
                                  "timestamp": _iso(e.ts),
                                  "metric": x.get("metric")})
    tick = drift_tick(folder, log_root=log_root,
                      catalogue_fingerprint=catalogue_fingerprint, as_of=as_of)
    return {
        "op": "drift_report",
        "folder": str(folder),
        "baselines": baselines,
        "tick": tick.to_dict(),
        "open_findings": open_findings,
        "regime": _regime_id(regime),
        "basis": _basis("drift_report", regime),
    }


# ── 5. risk register ─────────────────────────────────────────────────────────

def _grade_token(rank: int) -> str:
    """Render a grade RANK as the governance lattice's token — consuming
    ``grade_levels()`` rather than hardcoding the ``L<int>`` format or an ``L4``
    ceiling. Clamps into range so it never over/under-flows the lattice."""
    lv = grade_levels()
    return lv[max(0, min(len(lv) - 1, int(rank)))]


def risk_register(folder: str | Path, *, log_root: Optional[Path] = None,
                  posture: str = "balanced",
                  regime: Optional[dict] = None) -> dict[str, Any]:
    """Autonomy level as a system characteristic, rendered per footprint
    class: the static automation boundary (which grade a footprint requires
    under the folder's posture) plus the observed verdict history per action
    class — the boundary as designed AND as exercised."""
    p = load_policy(folder)
    shift = _POSTURE_SHIFT.get(posture, 0)
    boundary = []
    for tag in sorted(_RISK_MIN_GRADE):
        base = _RISK_MIN_GRADE[tag]
        boundary.append({
            "footprint": tag,
            "min_grade_base": _grade_token(base),
            "min_grade_under_posture": _grade_token(base + shift),
            "below_minimum": "NO-GO",
            "at_or_above_without_approval": "CONDITIONAL (human sign-off)",
            "telemetry": "observables may raise to CONDITIONAL, never lower (NT-13)"})
    tm = trigger_map(folder, log_root=log_root, regime=regime)
    return {
        "op": "risk_register",
        "folder": str(folder),
        "posture": posture,
        "oversight": {"enabled": p.oversight_enabled,
                      "active": p.oversight_is_active,
                      "default_level": p.oversight_default_level},
        "automation_boundary": boundary,
        "observed_actions": tm["actions"],
        "regime": _regime_id(regime),
        "basis": _basis("risk_register", regime),
    }


# ── 6. threat model ──────────────────────────────────────────────────────────

# Adversarial test suite mapped onto a generic agentic-threat taxonomy
# (category labels are descriptive engineering terms, jurisdiction-neutral;
# a regime pack may add framework cross-references). "covered" means a
# red-team regression exists and runs in CI — evidence of testing, not a
# security guarantee.
_THREAT_MAP = [
    {"category": "memory / context poisoning",
     "tests": ["security/test_attack_prompt_injection_via_ingest.py"]},
    {"category": "cascading injection across agents (inter-agent channel)",
     "tests": ["security/test_attack_prompt_injection_via_thread.py"]},
    {"category": "tool misuse / confused deputy",
     "tests": ["test_workflow_boundary.py", "test_action_gate.py"]},
    {"category": "privilege compromise / authority escalation through scopes",
     "tests": ["test_gate_telemetry.py",
               "security/test_attack_folder_context_traversal.py"]},
    {"category": "repudiation & untraceability",
     "tests": ["test_audit_chain_hash.py",
               "security/test_attack_chain_rewrite_with_key.py",
               "security/test_attack_chain_rewrite_no_key.py"]},
    {"category": "identity spoofing (host/key)",
     "tests": ["test_per_host_keys_068.py", "test_ed25519_signing.py"]},
    {"category": "erasure forgery (purge tombstones)",
     "tests": ["security/test_attack_purge_tombstone_forged.py"]},
    {"category": "detector evasion via input obfuscation",
     "tests": ["security/test_attack_lock_confusable_unicode.py"]},
    {"category": "arbitrary code execution via agent tools",
     "tests": [], "status": "not-applicable",
     "note": "no code-execution tool is exposed by the runtime's catalogue"},
]


def threat_model(*, tests_dir: Optional[str | Path] = None,
                 regime: Optional[dict] = None) -> dict[str, Any]:
    """The adversarial test suite as a threat-model artifact: category →
    red-team regressions present on disk. Presence is checked, not assumed."""
    base = Path(tests_dir) if tests_dir else (
        Path(__file__).resolve().parents[2] / "tests")
    rows = []
    for entry in _THREAT_MAP:
        if entry.get("status") == "not-applicable":
            rows.append({**entry, "present": [], "missing": []})
            continue
        present = [t for t in entry["tests"] if (base / t).exists()]
        missing = [t for t in entry["tests"] if not (base / t).exists()]
        rows.append({"category": entry["category"], "tests": entry["tests"],
                     "present": present, "missing": missing,
                     "status": "covered" if present and not missing
                     else ("partial" if present else "gap")})
    return {
        "op": "threat_model",
        "tests_dir": str(base),
        "categories": rows,
        "regime": _regime_id(regime),
        "basis": _basis("threat_model", regime),
    }
