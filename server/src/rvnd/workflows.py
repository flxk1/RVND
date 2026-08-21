# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Per-folder workflow definitions + sequential runner.

A workflow is a named, ordered list of skill dispatches against a folder.
Definitions live at::

    <log_root>/<folder_hash>/workflows/<name>.json

Schema:

    {
      "version": 1,
      "name": "contract-intake",
      "description": "Ingest a contract, route to legal, format the result.",
      "steps": [
        {"skill_id": "legal-first-aid:contract-orchestrator",
         "query": "Classify and route this contract",
         "on_failure": "continue" | "stop"},
        ...
      ]
    }

Each *run* is identified by an opaque ``run_id`` (sha256 prefix). The
runner walks the steps sequentially, calling ``dispatch_skill`` for each
and emitting ``workflow-event`` mutation-log events at every state change:

    workflow-event { run_id, workflow, step_index, state, skill_id,
                     started_at|ended_at, error? }

States: ``pending → running → done | failed | cancelled``.

The runner does not auto-invoke skills — it RECORDS that the dispatch was
requested, and the host (dashboard / orchestrator skill) is expected to
honour each dispatch as it does today. This keeps the workflow engine
side-effect-free at the Workspace MCP layer; the audit trail is the source
of truth.

Asymmetric inheritance: a folder's workflow list is the union of its own
defined workflows and those of its ancestors. Sibling and descendant
workflows do NOT contribute.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

from .mutation_log import LOG_ROOT_DEFAULT, LogEvent, MutationLog, folder_hash


WORKFLOWS_SUBDIR = "workflows"
WORKFLOW_VERSION = 1


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _workflows_dir(folder_path: str | Path,
                   log_root: Optional[Path] = None) -> Path:
    root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
    fh = folder_hash(folder_path)
    return root / fh / WORKFLOWS_SUBDIR


def _workflow_path(folder_path: str | Path, name: str,
                   log_root: Optional[Path] = None) -> Path:
    return _workflows_dir(folder_path, log_root) / f"{name}.json"


def _validate_workflow_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("workflow name must be a non-empty string")
    n = name.strip()
    if not n:
        raise ValueError("workflow name must be a non-empty string after strip")
    # Conservative: alnum + dash + underscore, prevents path traversal
    if not all(c.isalnum() or c in "-_." for c in n) or n.startswith("."):
        raise ValueError(f"workflow name {n!r} must be [A-Za-z0-9._-], no leading dot")
    return n


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WorkflowStep:
    skill_id: str
    query: str = ""
    on_failure: str = "stop"    # "stop" | "continue" | "retry"
    retries: int = 0            # honoured only when on_failure="retry"
    backoff_ms: int = 0         # initial delay; doubles each attempt
    # C0.4: risk footprint of the step's dispatch, declared
    # STATICALLY in the workflow definition. The gate reads this, never the
    # step output — an upstream payload cannot re-tag a downstream action.
    footprint: tuple = ()
    # C2 (Art. 50): natural persons an external-publish step's output affects.
    # Static, like footprint; required by the gate when external-publish is set.
    affected_parties: tuple = ()
    # Policy-matrix binding (2026-06-07): this step's reach grade and oversight
    # override. Static, like footprint. Both ``None`` = inherit (grade from the
    # run's dispatch context, oversight from the folder matrix default); a
    # per-step value lets one plan mix an ``L0``/``manual`` step with an
    # ``L3``/``notify`` step.
    autonomy_grade: Optional[str] = None
    oversight: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["footprint"] = list(self.footprint)
        d["affected_parties"] = list(self.affected_parties)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkflowStep":
        return cls(
            skill_id=str(d.get("skill_id") or "").strip(),
            query=str(d.get("query") or ""),
            on_failure=str(d.get("on_failure") or "stop"),
            retries=int(d.get("retries") or 0),
            backoff_ms=int(d.get("backoff_ms") or 0),
            footprint=tuple(d.get("footprint") or ()),
            affected_parties=tuple(d.get("affected_parties") or ()),
            autonomy_grade=(str(d["autonomy_grade"]) if d.get("autonomy_grade") else None),
            oversight=(str(d["oversight"]) if d.get("oversight") else None),
        )


@dataclass
class Workflow:
    name: str
    description: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)
    version: int = WORKFLOW_VERSION
    created_at: str = field(default_factory=_now_iso)
    created_by: str = "system"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version":     self.version,
            "name":        self.name,
            "description": self.description,
            "created_at":  self.created_at,
            "created_by":  self.created_by,
            "steps":       [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Workflow":
        return cls(
            name=str(d.get("name") or "").strip(),
            description=str(d.get("description") or ""),
            steps=[WorkflowStep.from_dict(s) for s in (d.get("steps") or [])],
            version=int(d.get("version") or WORKFLOW_VERSION),
            created_at=str(d.get("created_at") or _now_iso()),
            created_by=str(d.get("created_by") or "system"),
        )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


# Per-step policy-binding vocabularies (2026-06-07). Grades = action reach;
# oversight names = the six-level dial (forbidden/NO-GO is a gate verdict, not a
# settable oversight). Kept local to avoid importing the lock package here.
from .adapters.policy_languages import grade_levels as _grade_levels

_GRADES = set(_grade_levels())  # grade lattice consumed from governance's grammar
_OVERSIGHT_NAMES = {"autonomous", "notify", "review", "approve", "supervised", "manual"}


def define_workflow(folder_path: str | Path, wf: Workflow,
                    *, created_by: str = "system",
                    log_root: Optional[Path] = None) -> Path:
    """Persist a workflow definition. Overwrites if a workflow of that name
    already exists in this folder. Atomic (write to .tmp then rename)."""
    name = _validate_workflow_name(wf.name)
    if not wf.steps:
        raise ValueError("workflow must have at least one step")
    for i, s in enumerate(wf.steps):
        if not s.skill_id:
            raise ValueError(f"step {i}: skill_id must be non-empty")
        if s.on_failure not in ("stop", "continue", "retry"):
            raise ValueError(
                f"step {i}: on_failure must be 'stop', 'continue', or 'retry'"
            )
        if s.on_failure == "retry" and s.retries <= 0:
            raise ValueError(
                f"step {i}: on_failure='retry' requires retries > 0"
            )
        if s.backoff_ms < 0:
            raise ValueError(f"step {i}: backoff_ms must be >= 0")
        if s.autonomy_grade is not None and s.autonomy_grade not in _GRADES:
            raise ValueError(
                f"step {i}: autonomy_grade must be one of {sorted(_GRADES)} or null"
            )
        if s.oversight is not None and s.oversight not in _OVERSIGHT_NAMES:
            raise ValueError(
                f"step {i}: oversight must be one of {sorted(_OVERSIGHT_NAMES)} or null"
            )
    wf.name = name
    wf.created_by = created_by or wf.created_by
    path = _workflow_path(folder_path, name, log_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(wf.to_dict(), f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def load_workflow(folder_path: str | Path, name: str,
                  log_root: Optional[Path] = None) -> Optional[Workflow]:
    name = _validate_workflow_name(name)
    path = _workflow_path(folder_path, name, log_root)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return Workflow.from_dict(json.load(f))


def list_workflows_for_folder(folder_path: str | Path,
                              log_root: Optional[Path] = None) -> list[Workflow]:
    """List workflows defined on THIS folder only (no ancestor walk)."""
    d = _workflows_dir(folder_path, log_root)
    if not d.exists():
        return []
    out: list[Workflow] = []
    for p in sorted(d.glob("*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                out.append(Workflow.from_dict(json.load(f)))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def delete_workflow(folder_path: str | Path, name: str,
                    log_root: Optional[Path] = None) -> bool:
    """Delete a workflow definition. Returns True iff it existed."""
    name = _validate_workflow_name(name)
    path = _workflow_path(folder_path, name, log_root)
    if not path.exists():
        return False
    path.unlink()
    return True


# ---------------------------------------------------------------------------
# Asymmetric resolver (mirrors pinned_skills)
# ---------------------------------------------------------------------------


def _ancestor_chain(folder_path: str | Path) -> list[Path]:
    p = Path(folder_path).expanduser().resolve()
    chain = [p]
    while True:
        parent = p.parent
        if parent == p:
            break
        chain.append(parent)
        p = parent
    return chain


def list_workflows(folder_path: str | Path,
                   *, include_ancestors: bool = True,
                   log_root: Optional[Path] = None) -> dict[str, Any]:
    """Return the effective workflow set for ``folder_path`` (self +
    ancestors). Children inherit ancestor workflows; siblings/descendants
    do not contribute."""
    chain = _ancestor_chain(folder_path)
    if not include_ancestors:
        chain = chain[:1]
    seen: dict[str, dict[str, Any]] = {}
    for ancestor in chain:
        for wf in list_workflows_for_folder(ancestor, log_root=log_root):
            if wf.name in seen:
                seen[wf.name].setdefault("inherited_from", str(ancestor))
                continue
            seen[wf.name] = {
                "name":           wf.name,
                "description":    wf.description,
                "step_count":     len(wf.steps),
                "steps":          [s.to_dict() for s in wf.steps],
                "created_at":     wf.created_at,
                "created_by":     wf.created_by,
                "defined_in":     str(ancestor),
                "inherited_from": str(ancestor)
                                  if str(ancestor) != str(chain[0])
                                  else "",
            }
    return {
        "folder_context": str(chain[0]),
        "workflows":      sorted(seen.values(), key=lambda x: x["name"]),
        "chain":          [str(p) for p in chain],
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Template substitution for step output threading (#2)
# ---------------------------------------------------------------------------
# Step queries may reference earlier-step outputs via:
#   ${steps[N].output}    — the dispatcher's full response dict, JSON-encoded
#   ${steps[N].body}      — the dispatched skill's body (if available)
#   ${steps[N].skill_id}  — the skill that was dispatched at step N
#   ${steps[N].error}     — error text if that step failed (empty if it succeeded)
#
# Negative indices are not supported in v1 — keeps the parser dumb and the
# resolution deterministic. Unknown placeholders are left in place verbatim
# so a typo is visible in the audit trail rather than silently dropped.

_TEMPLATE_RE = re.compile(r"\$\{steps\[(\d+)\]\.(output|body|skill_id|error)\}")


def _substitute_step_refs(query: str,
                          prior_results: list[dict[str, Any]]) -> str:
    """Replace ``${steps[N].field}`` references in ``query`` with values
    from ``prior_results``. Out-of-range references are replaced with the
    literal string ``"[unresolved: step N out of range]"`` so the failure
    is loud in the audit trail."""
    if not query or "${" not in query:
        return query
    def _sub(m: re.Match) -> str:
        idx = int(m.group(1))
        field = m.group(2)
        if idx < 0 or idx >= len(prior_results):
            return f"[unresolved: step {idx} out of range]"
        result = prior_results[idx]
        if field == "output":
            try:
                return json.dumps(result.get("output") or {}, sort_keys=True)
            except Exception:
                return str(result.get("output") or "")
        if field == "body":
            return str(result.get("body") or "")
        if field == "skill_id":
            return str(result.get("skill_id") or "")
        if field == "error":
            return str(result.get("error") or "")
        return m.group(0)
    return _TEMPLATE_RE.sub(_sub, query)


# ---------------------------------------------------------------------------
# Inter-step boundary (C0.4, the conformity-runtime design )
# ---------------------------------------------------------------------------
# Step-output threading is an inter-agent communication channel: one skill's
# output becomes another skill's input. Cross-agent propagation of unsafe
# content travels through exactly such ordinary channels, so the mediation
# point we own is where the check lives. Before a prior step's output is
# substituted into the next step's query, the referenced values are scanned
# (PII tier-B + prompt-injection tier-D). A hit HOLDS the run — CONDITIONAL,
# never silent pass-through — and proceeds only with a recorded human
# approval (rationale + actor), which is the sign-off the verdict demands.

def _scan_threaded_refs(query: str,
                        prior_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan every prior-step value referenced by ``query`` BEFORE substitution.

    Returns a list of findings, each ``{from_step, field, type, detail}``.
    Empty list = clean. Raises ImportError loudly if the scanners are absent —
    a missing scanner must never look like a clean scan.
    """
    if not query or "${" not in query:
        return []
    from rvnd.lock import tier_b_scan_text
    from rvnd.lock import scan_text
    findings: list[dict[str, Any]] = []
    for idx_s, fieldname in set(_TEMPLATE_RE.findall(query)):
        idx = int(idx_s)
        if idx < 0 or idx >= len(prior_results) or fieldname == "skill_id":
            continue
        result = prior_results[idx]
        if fieldname == "output":
            try:
                value = json.dumps(result.get("output") or {}, sort_keys=True)
            except Exception:                            # noqa: BLE001
                value = str(result.get("output") or "")
        else:
            value = str(result.get(fieldname) or "")
        if not value:
            continue
        for f in scan_text(value):
            findings.append({"from_step": idx, "field": fieldname,
                             "type": "prompt_injection",
                             "detail": getattr(f, "detail", "") or getattr(f, "match", "") or str(f)})
        for f in tier_b_scan_text(value):
            findings.append({"from_step": idx, "field": fieldname,
                             "type": getattr(f, "type", "pii"),
                             "detail": getattr(f, "detail", "") or getattr(f, "match", "") or str(f)})
    return findings


def _new_run_id(folder_path: str | Path, name: str) -> str:
    seed = f"{folder_path}|{name}|{time.time_ns()}"
    return "wfrun:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _log_workflow_event(folder_path: str | Path,
                        *,
                        run_id: str,
                        workflow: str,
                        step_index: int,
                        state: str,
                        skill_id: str = "",
                        extra: Optional[dict[str, Any]] = None,
                        actor: str = "system",
                        log_root: Optional[Path] = None) -> None:
    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="workflow-event",
        lifecycle_state="",
        channel="system",
        actor=actor or "system",
        extra={
            "kind":       "workflow-event",
            "run_id":     run_id,
            "workflow":   workflow,
            "step_index": step_index,
            "state":      state,
            "skill_id":   skill_id,
            **(extra or {}),
        },
    ))


def run_workflow(folder_path: str | Path,
                 name: str,
                 *,
                 dispatcher: Optional[Callable[..., dict[str, Any]]] = None,
                 actor: str = "system",
                 log_root: Optional[Path] = None,
                 autonomy_grade: str = "L2",
                 posture: str = "balanced",
                 standing_approvals: tuple = (),
                 step_approvals: Optional[dict[int, str]] = None,
                 run_id: Optional[str] = None) -> dict[str, Any]:
    """Execute a workflow sequentially.

    ``dispatcher`` is a callable ``(folder, skill_id, query) -> dict`` —
    typically the existing ``dispatch_skill`` MCP function. Injection lets
    tests use a fake dispatcher. If omitted, falls back to the in-process
    pinned_skills.record_dispatch (records the dispatch but doesn't invoke).

    Inter-step boundary (C0.4): every step's dispatch is gated with the
    DISPATCHING context's ``autonomy_grade`` and the step's STATIC footprint
    (confused-deputy discipline — no step output can raise its own
    privileges), and every prior-step value referenced by a step's query is
    scanned before substitution. A gate CONDITIONAL or a dirty thread HOLDS
    the run unless ``step_approvals[step_index]`` carries a non-empty human
    rationale — the recorded sign-off that CONDITIONAL demands.

    Returns:
        ``{ok, run_id, workflow, steps: [{state, skill_id, error?}, ...],
            final_state, held?}`` — ``final_state`` is ``"held"`` when a
        boundary stopped the run; ``held`` then carries what and why.

    Errors during a step honour ``step.on_failure``: "stop" aborts the run,
    "continue" records the failure and proceeds.
    """
    name = _validate_workflow_name(name)
    # Resolve the workflow from self + ancestors so inherited workflows run
    all_wf = list_workflows(folder_path, include_ancestors=True,
                             log_root=log_root)
    wf_meta = next((w for w in all_wf["workflows"] if w["name"] == name), None)
    if wf_meta is None:
        raise FileNotFoundError(
            f"workflow {name!r} not defined in folder {folder_path} or its ancestors"
        )
    # Load full definition from defined_in
    wf = load_workflow(wf_meta["defined_in"], name, log_root=log_root)
    if wf is None:
        # Race: file moved out from under us. Treat as not found.
        raise FileNotFoundError(
            f"workflow {name!r} disappeared between resolve and load"
        )

    # Accept an externally-supplied run_id (the queue passes its own
    # ``entry.run_id``) so ONE id threads the whole lifecycle — enqueue → gated
    # steps → finalise — instead of the runner minting a second, disconnected
    # id. A direct (non-queued) caller passes nothing and gets a fresh id.
    run_id = run_id or _new_run_id(folder_path, name)
    _log_workflow_event(folder_path, run_id=run_id, workflow=name,
                         step_index=-1, state="running",
                         actor=actor, log_root=log_root,
                         extra={"started_at": _now_iso(),
                                "step_count": len(wf.steps)})

    step_results: list[dict[str, Any]] = []
    final_state = "done"
    held: Optional[dict[str, Any]] = None
    approvals = step_approvals or {}
    for i, step in enumerate(wf.steps):
        approval = (approvals.get(i) or "").strip()

        # ── C0.4a: gate the dispatch with this STEP's grade (per-step reach;
        # falls back to the run's dispatch grade when the step doesn't set one) ──
        from .action_gate import ActionRequest, Verdict, gate as _gate
        from .incidents import log_gate_decision
        step_grade = step.autonomy_grade or autonomy_grade
        decision = _gate(
            ActionRequest(agent=f"workflow:{name}",
                          action_class=f"dispatch:{step.skill_id}",
                          autonomy_grade=step_grade,
                          footprint=tuple(step.footprint),
                          folder=str(folder_path),
                          affected_parties=tuple(step.affected_parties)),
            standing_approvals=standing_approvals, posture=posture)
        log_gate_decision(folder_path, decision, log_root=log_root, actor=actor,
                          run_id=run_id, step_index=i)

        # ── policy-matrix composition (opt-in: only when this folder has a
        # painted matrix). The painted cell can only TIGHTEN the gate's
        # structural verdict, never loosen it — requisite variety made into a
        # default [Ashby 1956; Beer 1972], "meaningful" not nominal control
        # [Santoni de Sio & van den Hoven 2018], raise-only [gate NT-13]. The
        # matrix is the plan layer; here the doing layer reads it. ──
        eff_verdict = decision.verdict
        eff_reason = decision.reason
        from . import policy_matrix as _pm
        from . import verdict as _vd
        # Resolve root→workspace→sub-workspace: a sub-workspace inherits ancestors' policy and
        # may only be stricter. Opt-in if this workspace OR an ancestor has a grid.
        if _pm.has_matrix_in_chain(folder_path, log_root=log_root):
            _light_verdict = {"go": Verdict.GO, "ask": Verdict.CONDITIONAL,
                              "block": Verdict.NO_GO}
            _grade = step_grade if step_grade in _pm.GRADES else "L1"
            _oversight = step.oversight or "approve"
            _eff = _pm.effective_light(
                _pm.resolve_matrix(folder_path, log_root=log_root),
                grade=_grade, oversight=_oversight,
                gate_verdict=decision.verdict.value)
            # strictest-wins via the one shared rule; never looser than the gate.
            if (_vd.severity(_vd.from_light(_eff["light"]))
                    > _vd.severity(_vd.from_gate(eff_verdict.value))):
                eff_verdict = _light_verdict[_eff["light"]]
                eff_reason = f"policy matrix {_grade}×{_oversight}: {_eff['reason']}"
                _log_workflow_event(folder_path, run_id=run_id, workflow=name,
                                    step_index=i, state="matrix-tighten",
                                    skill_id=step.skill_id, actor=actor,
                                    log_root=log_root,
                                    extra={"grade": _grade, "oversight": _oversight,
                                           "light": _eff["light"],
                                           "reason": _eff["reason"]})

        if eff_verdict is Verdict.NO_GO:
            step_results.append({"step_index": i, "skill_id": step.skill_id,
                                 "state": "step-blocked", "ended_at": _now_iso(),
                                 "attempts": 0, "output": {}, "body": "",
                                 "error": f"NO-GO: {eff_reason}"})
            _log_workflow_event(folder_path, run_id=run_id, workflow=name,
                                 step_index=i, state="step-blocked",
                                 skill_id=step.skill_id, actor=actor,
                                 log_root=log_root,
                                 extra={"reason": eff_reason})
            if step.on_failure == "continue":
                continue
            final_state = "failed"
            break
        # ── form gate (2026-06-12): the folder's PACK STACK may demand a
        # control form for this step's action class (footprint). The demand
        # binds independent of the gate verdict — packs only ever tighten.
        # A form requiring human hands resolves through the approvals
        # projection: granted proceeds, denied blocks (timeout IS deny),
        # pending holds. Deterministic request id <workflow>:step<i> — the
        # release model is re-run-after-decision, so re-runs resume it.
        if step.footprint:
            from . import controlforms as _cf
            from .juris_packs import folder_required_forms
            _forms = folder_required_forms(folder_path, step.footprint)
            _composed = _cf.compose_all(_forms) if _forms else frozenset()
            _hands = {_cf.G_PRE_APPROVAL, _cf.G_TWO_APPROVERS, _cf.G_COMPETENCE}
            if _composed & _hands:
                import time as _time
                from .approvals import request_approval, resolve_approval
                _rid = f"{name}:step{i}"
                try:
                    _res = resolve_approval(folder_path, _rid,
                                            now=_time.time(), log_root=log_root)
                except ValueError:
                    request_approval(
                        folder_path, _rid, form=sorted(_composed),
                        competence=(step.footprint[0]
                                    if _cf.G_COMPETENCE in _composed else ""),
                        requester=actor, timeout_seconds=86400.0,
                        now=_time.time(), actor=actor, log_root=log_root)
                    _res = resolve_approval(folder_path, _rid,
                                            now=_time.time(), log_root=log_root)
                _log_workflow_event(
                    folder_path, run_id=run_id, workflow=name, step_index=i,
                    state="form-gate", skill_id=step.skill_id, actor=actor,
                    log_root=log_root,
                    extra={"kind2": "form-gate", "request_id": _rid,
                           "form": _cf.name_of(_composed),
                           "approval_state": _res["state"]})
                if _res["state"] == "denied":
                    err = f"form approval {_res['reason']}: {_cf.name_of(_composed)}"
                    step_results.append({"step_index": i,
                                         "skill_id": step.skill_id,
                                         "state": "step-blocked",
                                         "ended_at": _now_iso(),
                                         "attempts": 0, "output": {},
                                         "body": "", "error": err})
                    _log_workflow_event(folder_path, run_id=run_id,
                                        workflow=name, step_index=i,
                                        state="step-blocked",
                                        skill_id=step.skill_id, actor=actor,
                                        log_root=log_root,
                                        extra={"reason": err})
                    if step.on_failure == "continue":
                        continue
                    final_state = "failed"
                    break
                if _res["state"] != "granted":
                    held = {"step_index": i, "kind": "form-approval-pending",
                            "request_id": _rid,
                            "form": _cf.name_of(_composed),
                            "needed": _res.get("needed"),
                            "approvers_so_far": _res.get("approvers", [])}
                    _log_workflow_event(folder_path, run_id=run_id,
                                        workflow=name, step_index=i,
                                        state="step-held",
                                        skill_id=step.skill_id, actor=actor,
                                        log_root=log_root, extra=held)
                    final_state = "held"
                    break
                # granted: positive evidence, like a gate-release
                _log_workflow_event(
                    folder_path, run_id=run_id, workflow=name, step_index=i,
                    state="step-approved", skill_id=step.skill_id,
                    actor=actor, log_root=log_root,
                    extra={"kind2": "form-release", "request_id": _rid,
                           "form": _cf.name_of(_composed),
                           "approvers": _res.get("approvers", [])})
                # A granted form is strictly stronger evidence than a
                # rationale string (named human decisions on the chain) —
                # it satisfies the legacy CONDITIONAL sign-off too.
                if not approval:
                    approval = (f"form {_cf.name_of(_composed)} granted by "
                                + ", ".join(_res.get("approvers", [])))

        if eff_verdict is Verdict.CONDITIONAL and not approval:
            held = {"step_index": i, "kind": "gate-conditional",
                    "reason": eff_reason}
            _log_workflow_event(folder_path, run_id=run_id, workflow=name,
                                 step_index=i, state="step-held",
                                 skill_id=step.skill_id, actor=actor,
                                 log_root=log_root, extra=held)
            final_state = "held"
            break
        if eff_verdict is Verdict.CONDITIONAL and approval:
            # The sign-off that CONDITIONAL demands, recorded as positive
            # evidence — Art. 14 oversight operationalised during use, not
            # merely designed. The attestation reads this.
            _log_workflow_event(folder_path, run_id=run_id, workflow=name,
                                 step_index=i, state="step-approved",
                                 skill_id=step.skill_id, actor=actor,
                                 log_root=log_root,
                                 extra={"kind2": "gate-release",
                                        "reason": eff_reason,
                                        "approval_rationale": approval})

        # ── C0.4b: scan threaded prior-step values BEFORE substitution ──
        thread_findings = _scan_threaded_refs(step.query, step_results)
        if step.query and "${" in step.query:
            verdict = ("approved-by-human" if (thread_findings and approval)
                       else ("hold" if thread_findings else "allow"))
            _log_workflow_event(
                folder_path, run_id=run_id, workflow=name, step_index=i,
                state="thread-scan", skill_id=step.skill_id, actor=actor,
                log_root=log_root,
                extra={"kind2": "workflow-thread",
                       "from_steps": sorted({f["from_step"] for f in thread_findings}),
                       "to_step": i, "verdict": verdict,
                       "finding_types": sorted({f["type"] for f in thread_findings}),
                       "findings_n": len(thread_findings),
                       "approval_rationale": approval if verdict == "approved-by-human" else ""})
        if thread_findings and not approval:
            held = {"step_index": i, "kind": "thread-hold",
                    "findings": thread_findings}
            _log_workflow_event(folder_path, run_id=run_id, workflow=name,
                                 step_index=i, state="step-held",
                                 skill_id=step.skill_id, actor=actor,
                                 log_root=log_root,
                                 extra={"kind2": "thread-hold",
                                        "findings_n": len(thread_findings)})
            final_state = "held"
            break

        # Substitute ${steps[N].field} references using prior step results.
        resolved_query = _substitute_step_refs(step.query, step_results)
        max_attempts = 1 + (step.retries if step.on_failure == "retry" else 0)
        attempt = 0
        d: dict[str, Any] = {}
        ok = False
        while attempt < max_attempts:
            attempt += 1
            _log_workflow_event(folder_path, run_id=run_id, workflow=name,
                                 step_index=i, state="step-running",
                                 skill_id=step.skill_id, actor=actor,
                                 log_root=log_root,
                                 extra={"started_at": _now_iso(),
                                        "query": resolved_query[:200],
                                        "query_was_templated":
                                            resolved_query != step.query,
                                        "attempt": attempt,
                                        "max_attempts": max_attempts})
            # Dispatch
            try:
                if dispatcher is not None:
                    d = dispatcher(folder_context=str(folder_path),
                                    skill_id=step.skill_id,
                                    query=resolved_query)
                else:
                    from .pinned_skills import record_dispatch
                    d = record_dispatch(folder_path, step.skill_id,
                                         query=resolved_query,
                                         chosen_via=f"workflow:{name}",
                                         actor=actor,
                                         log_root=log_root)
                    d["ok"] = True
            except Exception as e:
                d = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            ok = bool(d.get("ok"))
            if ok:
                break
            # Failed — should we retry?
            if step.on_failure == "retry" and attempt < max_attempts:
                # Exponential backoff (doubles each attempt; start at backoff_ms)
                wait_ms = step.backoff_ms * (2 ** (attempt - 1))
                _log_workflow_event(folder_path, run_id=run_id, workflow=name,
                                     step_index=i, state="step-retry",
                                     skill_id=step.skill_id, actor=actor,
                                     log_root=log_root,
                                     extra={"attempt": attempt,
                                            "next_wait_ms": wait_ms,
                                            "error": d.get("error", "")})
                if wait_ms > 0:
                    time.sleep(wait_ms / 1000.0)
                continue
            break

        result: dict[str, Any] = {
            "step_index":  i,
            "skill_id":    step.skill_id,
            "state":       "done" if ok else "failed",
            "ended_at":    _now_iso(),
            "attempts":    attempt,
            # Output threading: later steps reference these via ${steps[i].*}
            "output":      d,
            "body":        d.get("body") or "",
        }
        if not ok:
            result["error"] = d.get("error", "unknown")

        # ── C2: an external-publish step that produced output gets a signed
        # Art. 50 disclosure envelope attached to its result. The gate already
        # guaranteed affected_parties is non-empty for this footprint.
        if ok and "external-publish" in step.footprint:
            try:
                from .disclosure import make_envelope
                env = make_envelope(result["body"] or "",
                                    affected_parties=list(step.affected_parties),
                                    action_class=f"dispatch:{step.skill_id}",
                                    meta={"workflow": name, "run_id": run_id,
                                          "step_index": i})
                result["disclosure"] = env.to_dict()
            except Exception as exc:                     # noqa: BLE001
                result["disclosure_error"] = str(exc)

        step_results.append(result)
        _log_workflow_event(folder_path, run_id=run_id, workflow=name,
                             step_index=i, state=result["state"],
                             skill_id=step.skill_id, actor=actor,
                             log_root=log_root,
                             extra={"ended_at":   result["ended_at"],
                                    "attempts":   attempt,
                                    "error":      result.get("error", ""),
                                    "has_output": bool(d),
                                    "has_body":   bool(result["body"]),
                                    "disclosure": "disclosure" in result})

        if not ok and step.on_failure == "stop":
            final_state = "failed"
            break
        # on_failure="retry" that exhausted retries → behaves like "stop"
        if not ok and step.on_failure == "retry":
            final_state = "failed"
            break

    _log_workflow_event(folder_path, run_id=run_id, workflow=name,
                         step_index=-1, state=final_state,
                         actor=actor, log_root=log_root,
                         extra={"ended_at": _now_iso()})
    out = {
        "ok":          final_state == "done",
        "run_id":      run_id,
        "workflow":    name,
        "steps":       step_results,
        "final_state": final_state,
    }
    if held is not None:
        out["held"] = held
    return out


# ---------------------------------------------------------------------------
# Read side — recent activity (for HOTL live view)
# ---------------------------------------------------------------------------


def _event_ts_iso(e: Any) -> str:
    """Render a LogEvent's ``ts`` float as an ISO 8601 UTC string.

    Returns ``""`` if the event has no usable timestamp. Used by the
    audit-trail readers (``recent_dispatches`` + ``active_workflows``) so the
    wire shape always carries a chronologically-sortable string.
    """
    from datetime import datetime, timezone
    ts = getattr(e, "ts", None)
    if ts is None or not isinstance(ts, (int, float)) or ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError, OverflowError):
        return ""


def _events_for_folder(folder_path: str | Path,
                       include_workflows: bool,
                       log_root: Optional[Path]) -> list[dict[str, Any]]:
    """Read dispatch + workflow events for a single folder."""
    log = MutationLog(folder_path, log_root=log_root)
    out: list[dict[str, Any]] = []
    fp = str(Path(folder_path).expanduser().resolve())
    for e in log.replay():
        if e.pair_id == "skill-dispatch":
            extra = e.extra or {}
            out.append({
                "kind":          "skill-dispatch",
                "timestamp":     _event_ts_iso(e),
                "actor":         e.actor or "",
                "audit_id":      e.audit_id or "",
                "skill_id":      extra.get("skill_id", ""),
                "query":         extra.get("query", ""),
                "chosen_via":    extra.get("chosen_via", ""),
                "folder_origin": fp,
            })
        elif include_workflows and e.pair_id == "workflow-event":
            extra = e.extra or {}
            out.append({
                "kind":          "workflow-event",
                "timestamp":     _event_ts_iso(e),
                "actor":         e.actor or "",
                "audit_id":      e.audit_id or "",
                "run_id":        extra.get("run_id", ""),
                "workflow":      extra.get("workflow", ""),
                "step_index":    extra.get("step_index", -1),
                "state":         extra.get("state", ""),
                "skill_id":      extra.get("skill_id", ""),
                "error":         extra.get("error", ""),
                "folder_origin": fp,
            })
    return out


def recent_dispatches(folder_path: str | Path,
                       *,
                       limit: int = 50,
                       include_workflows: bool = True,
                       scope: str = "self",
                       log_root: Optional[Path] = None) -> list[dict[str, Any]]:
    """Return the most recent skill-dispatch and (optionally) workflow-event
    entries in chronological-DESC order (newest first), capped at ``limit``.

    ``scope`` controls how far to look:
      - ``"self"``      (default) — only this folder's events.
      - ``"recursive"`` — this folder plus every descendant folder that has
        a mutation log (one per workspace registered in the log_root).

    The asymmetric rule (children flow UP) is preserved for memory pairs;
    the activity-view recursion is a separate read-only convenience for
    the HOTL surface, not an inheritance change.
    """
    if scope not in ("self", "recursive"):
        raise ValueError("scope must be 'self' or 'recursive'")
    events = _events_for_folder(folder_path, include_workflows, log_root)
    if scope == "recursive":
        # Walk every folder that has a log and is a descendant of folder_path.
        try:
            from .memory import discover_descendants
            root = str(Path(folder_path).expanduser().resolve())
            for desc in discover_descendants(root, log_root=log_root):
                if desc == root:
                    continue
                events.extend(_events_for_folder(desc, include_workflows,
                                                  log_root))
        except Exception:
            # Best-effort: if discover_descendants is unavailable, return
            # the self-scope result rather than failing the call.
            pass
    events.sort(key=lambda r: r["timestamp"], reverse=True)
    return events[:max(1, limit)]


def active_workflows(folder_path: str | Path,
                     *,
                     log_root: Optional[Path] = None) -> list[dict[str, Any]]:
    """Return run_ids that started (state=running) but never reached a
    terminal state (done|failed|cancelled). Useful for the HOTL panel to
    surface in-flight work after a crash."""
    log = MutationLog(folder_path, log_root=log_root)
    seen: dict[str, dict[str, Any]] = {}
    terminal = {"done", "failed", "cancelled"}
    for e in log.replay():
        if e.pair_id != "workflow-event":
            continue
        extra = e.extra or {}
        rid = extra.get("run_id")
        if not rid:
            continue
        # Track most recent state per run_id, but only for run-level events
        # (step_index = -1).
        if extra.get("step_index", 0) != -1:
            continue
        seen[rid] = {
            "run_id":    rid,
            "workflow":  extra.get("workflow", ""),
            "state":     extra.get("state", ""),
            "timestamp": _event_ts_iso(e),
            "actor":     e.actor or "",
            "audit_id":  e.audit_id or "",
        }
    return [r for r in seen.values() if r["state"] not in terminal]
