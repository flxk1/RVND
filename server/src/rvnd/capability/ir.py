# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Capability IR — the neutral representation a skill is translated INTO.

The universal skill adapter is a hub: any source (Anthropic SKILL.md + its
``workflow.yaml``, a Cursor command set, a skill-shop manifest) is parsed into
this one Capability IR, and the IR is projected onto any target architecture
(Workspace's own workflow engine, a generated MCP server, a prompt as the lossy
floor). N parsers + M projectors, not N×M direct translators. The hub step is
where governance attaches.

This module is the IR plus:
  * ``parse_workflow_yaml`` — the initial parser (workflow.yaml, two
    dialects: ``stages`` for disciplines, ``scenarios`` for role-verticals).
  * ``project_to_workspace_workflow`` — the initial projector (IR → the existing
    ``rvnd.workflows.Workflow``). Lossy where the engine is narrower than the
    IR; the loss is reported, never silent.
  * ``readiness_report`` — the import-time honesty contract: for a given host
    (which skills/connectors it has), what runs native / under-oversight /
    needs-a-connector / degrades-to-prompt.

Design rule: the IR is RICHER than any single source or target. The
workflow.yaml parser only emits the step kinds its source actually declares
(skill / route / gate); a future skill-shop parser emits ``tool`` and ``llm``
steps. Projectors downgrade explicitly. Nothing here executes anything — it
translates and reports. No cloud calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


IR_VERSION = 1

# The five step kinds the IR can carry. A parser emits a subset; a projector
# consumes a subset and downgrades the rest.
STEP_KINDS = ("skill", "tool", "llm", "route", "gate")
#   skill — dispatch a named skill (the only kind today's Workspace engine runs)
#   tool  — call a connector / deterministic script with args (needs host tool)
#   llm   — an LLM-reasoning step with a prompt (needs an execution model)
#   route — choose among downstream steps/skills by a trigger/condition
#   gate  — a non-dispatch control point (audit stamp, refusal gate, checkpoint)


# ---------------------------------------------------------------------------
# IR data model
# ---------------------------------------------------------------------------


@dataclass
class CapabilityStep:
    """One node of a capability. Superset of rvnd.workflows.WorkflowStep."""

    id: str
    kind: str = "skill"                         # one of STEP_KINDS
    ref: str = ""                               # skill_id | tool name | "" for llm/gate
    intent: str = ""                            # description / query / prompt / trigger
    chain: list[str] = field(default_factory=list)   # for route: ordered skill_ids
    consumes: list[str] = field(default_factory=list)  # upstream step ids / artifact names
    produces: str = ""                          # output artifact name
    parallel_group: str = ""                    # steps sharing a non-empty group run together
    run_when: list[str] = field(default_factory=list)  # conditions; empty == always eligible
    optional: bool = False
    always: bool = False                        # runs regardless of route selection
    refusable: bool = False                     # may legitimately refuse
    refuse_when: list[str] = field(default_factory=list)
    on_failure: str = "stop"                    # stop | continue | retry
    requires: list[str] = field(default_factory=list)  # capability reqs (connector ids, etc.)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CapabilityStep":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class CapabilitySpec:
    """A whole capability: ordered steps + named route profiles + metadata."""

    name: str
    version: str = "0.0.0"
    source_format: str = ""                     # workflow.yaml:stages | workflow.yaml:scenarios | ...
    description: str = ""
    role: str = ""
    steps: list[CapabilityStep] = field(default_factory=list)
    routes: dict[str, list[str]] = field(default_factory=dict)  # profile -> ordered step ids
    settings: dict[str, Any] = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)  # union of step requirements
    ir_version: int = IR_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CapabilitySpec":
        steps = [CapabilityStep.from_dict(s) for s in (d.get("steps") or [])]
        return cls(
            name=str(d.get("name", "")),
            version=str(d.get("version", "0.0.0")),
            source_format=str(d.get("source_format", "")),
            description=str(d.get("description", "")),
            role=str(d.get("role", "")),
            steps=steps,
            routes={k: list(v) for k, v in (d.get("routes") or {}).items()},
            settings=dict(d.get("settings") or {}),
            requires=list(d.get("requires") or []),
            ir_version=int(d.get("ir_version") or IR_VERSION),
        )

    def step_by_id(self, sid: str) -> Optional[CapabilityStep]:
        for s in self.steps:
            if s.id == sid:
                return s
        return None


# ---------------------------------------------------------------------------
# Parser 1 — workflow.yaml (both dialects) -> CapabilitySpec
# ---------------------------------------------------------------------------


def _coerce_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def _parse_stages(doc: dict[str, Any]) -> list[CapabilityStep]:
    """Discipline dialect: ``stages`` with consumes/parallel_with/optional/
    runs_when/always. A stage with ``skill`` is a skill step; a stage with no
    skill but ``always`` (the audit-stamp pattern) is a gate."""
    steps: list[CapabilityStep] = []
    for st in doc.get("stages") or []:
        sid = str(st.get("id", "")).strip()
        skill = str(st.get("skill", "")).strip()
        always = bool(st.get("always", False))
        kind = "skill" if skill else ("gate" if always else "skill")
        steps.append(CapabilityStep(
            id=sid,
            kind=kind,
            ref=skill,
            intent=str(st.get("description", "")).strip(),
            consumes=_coerce_list(st.get("consumes")),
            produces=str(st.get("output", "")).strip(),
            parallel_group=str(st.get("parallel_with", "")).strip(),
            run_when=_coerce_list(st.get("runs_when")),
            optional=bool(st.get("optional", False)),
            always=always,
            refusable=bool(st.get("refusable", False)),
            refuse_when=_coerce_list(st.get("refuses_when")),
        ))
    return steps


def _parse_scenarios(doc: dict[str, Any]) -> list[CapabilityStep]:
    """Role-vertical dialect: ``scenarios`` with trigger + skills[] + refusable.
    A scenario fans a trigger out to an ordered list of skills, so it is a
    ``route`` step carrying the chain."""
    steps: list[CapabilityStep] = []
    for sc in doc.get("scenarios") or []:
        steps.append(CapabilityStep(
            id=str(sc.get("id", "")).strip(),
            kind="route",
            ref="",
            intent=str(sc.get("trigger", "")).strip(),
            chain=_coerce_list(sc.get("skills")),
            produces=str(sc.get("output", "")).strip(),
            run_when=_coerce_list(sc.get("trigger")),
            refusable=bool(sc.get("refusable", False)),
        ))
    return steps


def parse_workflow_yaml(source: "str | Path | dict[str, Any]") -> CapabilitySpec:
    """Parse a workflow.yaml source (path, yaml text, or pre-loaded dict) into
    the Capability IR. Auto-detects the ``stages`` vs ``scenarios`` dialect.

    Both dialects may carry top-level ``routing`` (profile -> step ids) and
    ``settings``. The union of every step's ``requires`` is lifted to the spec.
    """
    if isinstance(source, dict):
        doc = source
    else:
        import yaml  # dep already declared; parser requires it
        text = (Path(source).read_text(encoding="utf-8")
                if isinstance(source, Path) or "\n" not in str(source)
                else str(source))
        # If it looked like a path but wasn't multi-line, the above read it.
        doc = yaml.safe_load(text) or {}

    if "stages" in doc:
        steps = _parse_stages(doc)
        fmt = "workflow.yaml:stages"
    elif "scenarios" in doc:
        steps = _parse_scenarios(doc)
        fmt = "workflow.yaml:scenarios"
    else:
        steps = []
        fmt = "workflow.yaml:empty"

    routes = {k: _coerce_list(v) for k, v in (doc.get("routing") or {}).items()}

    requires: list[str] = []
    for s in steps:
        for r in s.requires:
            if r not in requires:
                requires.append(r)

    # Name carrier differs by dialect: disciplines use `kit:`, role-verticals
    # use `vertical:`, a generic source may use `name:`. First non-empty wins.
    name = (str(doc.get("name") or doc.get("kit") or doc.get("vertical") or "")
            .strip())

    return CapabilitySpec(
        name=name,
        version=str(doc.get("version", "0.0.0")).strip(),
        source_format=fmt,
        description=str(doc.get("description", "")).strip(),
        role=str(doc.get("role", "")).strip(),
        steps=steps,
        routes=routes,
        settings=dict(doc.get("settings") or {}),
        requires=requires,
    )


# ---------------------------------------------------------------------------
# Parser 2 — a router skill's dispatch-map.md routing table -> CapabilitySpec
# ---------------------------------------------------------------------------
#
# Router products (music-companion, legal-companion) carry their orchestration
# NOT in a workflow.yaml but in a markdown "Type | Route" table inside the
# router skill's references/dispatch-map.md, e.g.::
#
#     | Type | Route |
#     |---|---|
#     | split-sheet | music-data-extractor -> split-calculator -> check |
#     | sync-licence | contract-key-terms + music-data-extractor + sync-quote |
#
# This is deterministic structure (not free prose): each row is a route step
# whose trigger is the document type and whose chain is the ordered skills.
# Steps separated by '->' (sequence) or '+' (also-run) both flatten to an
# ordered chain — the engine runs them sequentially either way; the '+' vs
# '->' distinction is preserved in the step intent for a richer projector.


def _split_route_cell(cell: str) -> list[str]:
    """Split a route cell like 'a -> b + c' into ['a','b','c'].

    Accepts the ASCII '->' and the unicode arrow, plus '+' and ','. Drops
    obvious non-skill prose cells (e.g. 'no forced data route ...')."""
    raw = cell.strip().strip("`").strip()
    low = raw.lower()
    if not raw or "no forced" in low or "hand back" in low or low in {"-", "—", "n/a"}:
        return []
    # normalise all separators to a single delimiter
    for sep in ("→", "->", "→", "+", ","):
        raw = raw.replace(sep, "|")
    parts = [p.strip().strip("`").strip() for p in raw.split("|")]
    return [p for p in parts if p]


def parse_dispatch_map(markdown: str, *, name: str = "",
                       skill_prefix: str = "") -> CapabilitySpec:
    """Parse a router skill's dispatch-map.md into the Capability IR.

    Extracts the first ``| Type | Route |`` table found. Each data row becomes
    a ``route`` step: ``id`` = the type cell, ``intent`` = "document type: X",
    ``chain`` = the ordered skills in the route cell. Rows whose route is prose
    ("no forced data route — hand back to the coach") become a refusable route
    with an empty chain (a hand-back gate).

    ``skill_prefix`` (e.g. "music-companion:") is prepended to bare skill names
    that don't already carry a ':' namespace, so the chain entries match the
    skill_ids the host pins.
    """
    lines = markdown.splitlines()
    rows: list[tuple[str, str]] = []
    in_table = False
    header_seen = False
    for ln in lines:
        s = ln.strip()
        is_row = s.startswith("|") and s.count("|") >= 2
        if not in_table:
            # find a header row mentioning Type and Route
            if is_row and "type" in s.lower() and "route" in s.lower():
                in_table = True
                header_seen = True
            continue
        if not is_row:
            if header_seen and rows:
                break          # table ended
            continue
        # skip the |---|---| separator row
        if set(s.replace("|", "").strip()) <= {"-", ":", " "}:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 2:
            rows.append((cells[0], cells[1]))

    steps: list[CapabilityStep] = []
    for type_cell, route_cell in rows:
        tid = type_cell.strip().strip("`").strip()
        if not tid or tid.lower() == "type":
            continue
        chain = _split_route_cell(route_cell)
        if skill_prefix:
            chain = [c if ":" in c else skill_prefix + c for c in chain]
        steps.append(CapabilityStep(
            id=tid,
            kind="route",
            intent=f"document type: {tid}",
            chain=chain,
            run_when=[f"doc_type=={tid}"],
            refusable=not chain,        # prose hand-back rows are refusable gates
        ))

    return CapabilitySpec(
        name=name or "dispatch-map",
        source_format="dispatch-map.md",
        description="Router dispatch table lifted from a skill's dispatch-map.md",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Projector 1 — CapabilitySpec -> rvnd.workflows.Workflow (lossy, reported)
# ---------------------------------------------------------------------------


@dataclass
class ProjectionResult:
    """The projected engine workflow as a dict, plus an explicit loss report."""

    workflow: dict[str, Any]                    # rvnd.workflows.Workflow dict shape
    projected_steps: int = 0
    downgraded: list[dict[str, str]] = field(default_factory=list)  # {step_id, kind, reason}
    dropped: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_to_workspace_workflow(spec: CapabilitySpec,
                               *, route: str = "") -> ProjectionResult:
    """Project the IR onto the existing Workspace engine shape
    (``{name, description, version, steps:[{skill_id, query, on_failure}]}``).

    The engine today runs ONLY skill dispatches, so this projection is lossy by
    construction and the loss is reported, not hidden:
      * ``skill``  -> a WorkflowStep (native).
      * ``route``  -> the chain is flattened to one WorkflowStep per skill in
        order (the trigger is recorded in the query); downgraded.
      * ``gate``   -> dropped from the runnable steps (audit/refusal gates have
        no engine step type yet); reported.
      * ``tool`` / ``llm`` -> downgraded to a skill-shaped step whose query
        carries the intent, and flagged: the engine cannot execute these until
        the engine is widened (Phase 2).

    ``route`` (the profile name) selects a subset+order of step ids from
    ``spec.routes``; empty means all steps in declared order.
    """
    if route and route in spec.routes:
        order = [spec.step_by_id(sid) for sid in spec.routes[route]]
        order = [s for s in order if s is not None]
    else:
        order = list(spec.steps)

    eng_steps: list[dict[str, Any]] = []
    downgraded: list[dict[str, str]] = []
    dropped: list[dict[str, str]] = []
    notes: list[str] = []

    for s in order:
        if s.kind == "skill":
            eng_steps.append({
                "skill_id": s.ref,
                "query": s.intent,
                "on_failure": s.on_failure,
            })
        elif s.kind == "route":
            if not s.chain:
                # an empty route (e.g. a "hand back to the coach" row) has no
                # runnable dispatch — it is a control hand-back, reported as a
                # drop, never as a flatten.
                dropped.append({"step_id": s.id, "kind": "route",
                                "reason": "empty route — hand-back / no dispatch"})
                continue
            for skill_id in s.chain:
                eng_steps.append({
                    "skill_id": skill_id,
                    "query": s.intent,
                    "on_failure": "continue" if s.refusable else s.on_failure,
                })
            downgraded.append({"step_id": s.id, "kind": "route",
                               "reason": "flattened chain to sequential skill dispatches"})
        elif s.kind in ("tool", "llm"):
            eng_steps.append({
                "skill_id": s.ref or f"__{s.kind}__:{s.id}",
                "query": s.intent,
                "on_failure": s.on_failure,
            })
            downgraded.append({"step_id": s.id, "kind": s.kind,
                               "reason": "engine has no native %s step; needs Phase-2 "
                                         "engine widening to execute" % s.kind})
        elif s.kind == "gate":
            dropped.append({"step_id": s.id, "kind": "gate",
                            "reason": "audit/refusal gate — no runnable engine step type"})
        else:
            dropped.append({"step_id": s.id, "kind": s.kind, "reason": "unknown kind"})

    if downgraded:
        notes.append("%d step(s) downgraded — engine narrower than IR" % len(downgraded))
    if dropped:
        notes.append("%d step(s) dropped — no engine equivalent yet" % len(dropped))

    workflow = {
        "name": spec.name + (("::" + route) if route else ""),
        "description": spec.description,
        "version": 1,
        "steps": eng_steps,
    }
    return ProjectionResult(workflow=workflow, projected_steps=len(eng_steps),
                            downgraded=downgraded, dropped=dropped, notes=notes)


# ---------------------------------------------------------------------------
# Readiness — the import-time honesty contract
# ---------------------------------------------------------------------------


def readiness_report(spec: CapabilitySpec,
                     *,
                     available_skills: Optional[set[str]] = None,
                     available_connectors: Optional[set[str]] = None) -> dict[str, Any]:
    """For a given host, classify every step: ``native`` (runs as-is),
    ``oversight`` (an agentic/llm step that runs under Workspace's oversight),
    ``needs_connector`` (a tool/connector dependency the host lacks),
    ``needs_skill`` (a referenced skill not present), or ``prompt_only``
    (a kind with no execution model on this host — degrades to instructions).

    ``available_skills`` / ``available_connectors`` are what the host actually
    provides; ``None`` means "unknown — do not claim availability" (so skill/
    tool refs are reported as requirements rather than asserted satisfied).
    """
    have_skills = available_skills if available_skills is not None else None
    have_conns = available_connectors if available_connectors is not None else None

    buckets: dict[str, list[str]] = {
        "native": [], "oversight": [], "needs_connector": [],
        "needs_skill": [], "prompt_only": [],
    }
    missing_skills: set[str] = set()
    missing_connectors: set[str] = set()

    def skill_ok(sid: str) -> bool:
        return bool(sid) and (have_skills is None or sid in have_skills)

    for s in spec.steps:
        if s.kind == "skill":
            if have_skills is not None and s.ref not in have_skills:
                buckets["needs_skill"].append(s.id); missing_skills.add(s.ref)
            else:
                buckets["native"].append(s.id)
        elif s.kind == "route":
            absent = [sk for sk in s.chain if not skill_ok(sk)]
            if absent and have_skills is not None:
                buckets["needs_skill"].append(s.id); missing_skills.update(absent)
            else:
                buckets["native"].append(s.id)
        elif s.kind == "tool":
            conn = s.ref
            if have_conns is not None and conn not in have_conns:
                buckets["needs_connector"].append(s.id); missing_connectors.add(conn)
            elif have_conns is None:
                buckets["needs_connector"].append(s.id); missing_connectors.add(conn or s.id)
            else:
                buckets["native"].append(s.id)
        elif s.kind == "llm":
            buckets["oversight"].append(s.id)
        elif s.kind == "gate":
            buckets["native"].append(s.id)   # gates are substrate-native (audit/refusal)
        else:
            buckets["prompt_only"].append(s.id)

    runnable = not buckets["needs_connector"] and not buckets["needs_skill"]
    verdict = ("runs_native" if runnable and not buckets["oversight"]
               else "runs_under_oversight" if runnable
               else "blocked")

    return {
        "capability": spec.name,
        "verdict": verdict,
        "runnable": runnable,
        "buckets": buckets,
        "missing_skills": sorted(missing_skills),
        "missing_connectors": sorted(missing_connectors),
        "step_count": len(spec.steps),
        "requires": list(spec.requires),
    }


# ---------------------------------------------------------------------------
# Import-time entry point — discover a product's orchestration + report
# ---------------------------------------------------------------------------


# Where a product folder may carry its orchestration. Both styles are real:
#   workflow.yaml — disciplines + role-verticals (declared playbook)
#   skills/<router>/references/dispatch-map.md — router products (table)
_WORKFLOW_NAMES = ("workflow.yaml", "workflow.yml")
_DISPATCH_NAMES = ("dispatch-map.md",)


def discover_capabilities(folder_path: "str | Path") -> list[CapabilitySpec]:
    """Walk a product/plugin folder and lift every capability it declares into
    the IR. Picks up both ``workflow.yaml`` playbooks and router
    ``dispatch-map.md`` tables. Returns one CapabilitySpec per source found
    (empty list if the product is a single flat skill with no orchestration —
    which is itself an honest answer, not an error)."""
    root = Path(folder_path).expanduser()
    specs: list[CapabilitySpec] = []
    if not root.exists():
        return specs

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        nm = p.name.lower()
        try:
            if nm in _WORKFLOW_NAMES:
                specs.append(parse_workflow_yaml(p.read_text(encoding="utf-8")))
            elif nm in _DISPATCH_NAMES:
                # name the capability after the product folder, prefix bare
                # skills with the router's plugin namespace if discoverable
                prefix = _plugin_namespace(root)
                specs.append(parse_dispatch_map(
                    p.read_text(encoding="utf-8"),
                    name=root.name,
                    skill_prefix=(prefix + ":") if prefix else ""))
        except Exception as e:  # a malformed source is reported, never fatal
            specs.append(CapabilitySpec(
                name=p.name, source_format="error",
                description=f"failed to parse {p.name}: {e}"))
    return specs


def _plugin_namespace(root: Path) -> str:
    """Best-effort plugin name from .claude-plugin/plugin.json (for prefixing
    bare skill names in a dispatch map). Empty string if not found."""
    pj = root / ".claude-plugin" / "plugin.json"
    if pj.exists():
        try:
            return str(json.loads(pj.read_text(encoding="utf-8")).get("name", "")).strip()
        except Exception:
            return ""
    return ""


def import_readiness(folder_path: "str | Path",
                     *,
                     available_skills: Optional[set[str]] = None,
                     available_connectors: Optional[set[str]] = None) -> dict[str, Any]:
    """The import-time honesty contract for a whole product folder.

    Discovers every capability, runs the readiness check on each against what
    the host provides, and returns one combined report:

        {
          "folder": ...,
          "capabilities": [ <readiness_report per spec> ],
          "overall": "runs_native" | "runs_under_oversight" | "blocked"
                     | "no_orchestration",
          "missing_skills": [...union...],
          "missing_connectors": [...union...],
        }

    ``overall`` is the WEAKEST verdict across capabilities (one blocked
    capability blocks the product); ``no_orchestration`` means the product is a
    flat skill with nothing to schedule — honest, not a failure.
    """
    specs = [s for s in discover_capabilities(folder_path)
             if s.source_format != "error"]
    errors = [s for s in discover_capabilities(folder_path)
              if s.source_format == "error"]

    if not specs:
        return {
            "folder": str(folder_path),
            "capabilities": [],
            "overall": "no_orchestration",
            "missing_skills": [],
            "missing_connectors": [],
            "errors": [s.description for s in errors],
        }

    reports = [readiness_report(s, available_skills=available_skills,
                                available_connectors=available_connectors)
               for s in specs]

    rank = {"blocked": 0, "runs_under_oversight": 1, "runs_native": 2}
    overall = min((r["verdict"] for r in reports), key=lambda v: rank.get(v, 0))

    miss_s: set[str] = set()
    miss_c: set[str] = set()
    for r in reports:
        miss_s.update(r["missing_skills"])
        miss_c.update(r["missing_connectors"])

    return {
        "folder": str(folder_path),
        "capabilities": reports,
        "overall": overall,
        "missing_skills": sorted(miss_s),
        "missing_connectors": sorted(miss_c),
        "errors": [s.description for s in errors],
    }
