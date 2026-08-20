# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governed orchestration over the companions in a workspace tree.

The shared backend for the Workspaces chat (the app sidebar and the `/Workspaces` skill in
a host like Cowork both call this). It scopes top-down over a workspace and its
descendants, finds the companion workspaces (those that expose skills), and produces
a **gated dispatch plan** — each companion's dispatch ruled by the action gate
(GO / CONDITIONAL / NO-GO) and the plan recorded on the root workspace's signed
chain.

Scope of this primitive: route + gate + record. It does not execute the
companions' skills (that is `dispatch_skill`, the pluggable next step) and it
does not call a model — the plan says which companions apply and whether each
may run unattended, so the chat can dispatch the GO ones and surface the
CONDITIONAL ones to the human.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from .action_gate import ActionRequest, gate
from .workspace_contract import WorkspaceContract, describe_workspace
from .mutation_log import LogEvent, MutationLog


def turn_governance(*,
                    egress_to_cloud: bool = False,
                    uses_works: bool = False,
                    crosses_workspaces: bool = False) -> dict[str, Any]:
    """Decide which governance tools a single chat turn needs.

    The chat orchestrates this — not every turn needs every tool. Two layers:

    - **floor (always on):** the audit chain records the turn and the oversight
      dial bounds it. Non-negotiable, every turn.
    - **conditional (the chat selects per turn):** the Lock only when the turn
      egresses to a cloud model; the Grounder only when the answer rests on
      works (so creators get credit) — a casual or pure-reasoning turn does not
      need grounding; cross-workspace only when the turn composes other workspaces.

    Returns ``{floor, conditional, grounding, tools}``.
    """
    floor = ["audit-chain", "oversight"]
    conditional: list[str] = []
    if egress_to_cloud:
        conditional.append("lock")
    if uses_works:
        conditional.append("grounder")
    if crosses_workspaces:
        conditional.append("cross-workspace")
    return {
        "floor": floor,
        "conditional": conditional,
        "grounding": uses_works,
        "tools": floor + conditional,
    }


def _flatten(c: WorkspaceContract) -> Iterator[WorkspaceContract]:
    yield c
    for kid in c.children:
        yield from _flatten(kid)


def orchestrate(query: str,
                folder: str | Path,
                *,
                agent: str = "workspaces-chat",
                actor: str = "user",
                autonomy_grade: str = "L2",
                # A plain "ask this companion" is low-risk -> GO, governed and
                # recorded; the oversight dial still applies. Risky skills carry
                # their own footprint (personal-data / external-publish / ...),
                # which flags them to CONDITIONAL for human sign-off.
                footprint: tuple[str, ...] = (),
                max_depth: int = 4,
                log_root: str | Path | None = None) -> dict[str, Any]:
    """Build a governed dispatch plan for ``query`` across the workspace tree at
    ``folder``. Returns ``{query, root, scope_workspaces, companions: [...],
    audit_id, governed}``; each companion entry carries its gate ``verdict``.
    """
    from .workspace_hooks import check_access
    root = describe_workspace(folder, depth=max_depth, log_root=log_root)
    nodes = list(_flatten(root))
    companions = [c for c in nodes
                  if (c.exposes.get("skills", 0) > 0
                      or c.workspace_type in ("companion", "skill"))
                  and check_access(actor, c.folder)]   # access = workspace (overlay enforces)

    plan: list[dict[str, Any]] = []
    for comp in companions:
        req = ActionRequest(agent=agent, action_class="dispatch-skill",
                            autonomy_grade=autonomy_grade, footprint=footprint,
                            folder=comp.folder)
        dec = gate(req)
        plan.append({
            "workspace": comp.workspace_id,
            "name": comp.name,
            "folder": comp.folder,
            "workspace_type": comp.workspace_type,
            "skills": comp.skill_ids,
            "verdict": dec.verdict.value,
        })

    audit_id = None
    audit_dropped = None
    try:
        log = MutationLog(root.folder, log_root=log_root)
        ev = LogEvent(
            event="system", folder_path=root.folder,
            pair_id=f"orchestrate:{(query or '')[:32]}", channel="system",
            actor=agent,
            extra={"kind": "orchestrate", "query": query,
                   "companions": [p["workspace"] for p in plan],
                   "verdicts": {p["workspace"]: p["verdict"] for p in plan}},
        )
        audit_id = log.append(ev)
    except Exception as exc:
        from .audit_drop import record as _record_drop
        audit_dropped = _record_drop("workspace_orchestrate.orchestrate", exc,
                                     folder=root.folder, log_root=log_root)

    return {
        "query": query,
        "root": root.folder,
        "scope_workspaces": len(nodes),
        "companions": plan,
        "audit_id": audit_id,
        # Present only when the append failed. Without it `audit_id: None` is
        # indistinguishable from "no audit configured".
        **({"audit_dropped": audit_dropped["error"]} if audit_dropped else {}),
        "governed": True,
    }


def _gather_sources(folder: str, log_root, max_depth: int) -> dict[str, str]:
    """Sources from the granted workspace + its descendants only.

    access = workspace: the chat sees exactly the workspace the user was given and the
    sub-workspaces beneath it; siblings and parents are out of scope by the
    asymmetric rule. Returns ``{pair_id: source_folder}``.
    """
    from .workspace_lock import read_pairs
    from .memory import discover_descendants
    try:
        folders = discover_descendants(folder, log_root=log_root) or [folder]
    except Exception:
        folders = [folder]
    seen: dict[str, str] = {}
    for f in folders:
        try:
            for pid in read_pairs(f, log_root=log_root):
                if pid:
                    seen.setdefault(pid, f)
        except Exception:
            continue
    return seen


def _handoff_dispatcher(*, folder: str, query: str, skills: list) -> dict:
    """Default dispatcher: the workspace routes + gates; the host/app agent executes
    the skill. Returns a handoff descriptor, not a fabricated result."""
    return {"ok": False, "handoff": True,
            "reason": "execution handed to the host/app agent",
            "folder": folder, "skills": skills}


def ask_workspace(query: str,
             folder: str | Path,
             *,
             actor: str = "user",
             max_depth: int = 4,
             dispatcher=None,
             completer=None,
             uses_works: bool | None = None,
             max_tokens: int = 512,
             log_root: str | Path | None = None) -> dict[str, Any]:
    """One governed chat turn over the workspace a user has access to — the shared
    loop for the app sidebar and ``/Workspaces``. **access = workspace:** scope is this
    workspace + its descendants only; siblings and parents are out of scope.

    Fused chat core:
      route (``orchestrate``) → dispatch the GO companions (a companion is a
      workspace, so any companion is reachable the same way) → fold their
      outputs + the workspace's works into a local-first ``cascade`` → ground only
      when the turn rests on works (so creators get credit) → record.

    ``dispatcher`` executes a companion (the host/app supplies it; the default
    hands off rather than fabricating a result). ``completer`` is the model call
    (injectable for tests).
    """
    from .workspace_cascade import cascade_for_workspace
    from .workspace_hooks import check_access
    resolved = str(Path(folder).expanduser().resolve())

    # access = workspace: refuse if the actor was not granted this workspace (overlay
    # enforces; core default allows all)
    if not check_access(actor, resolved):
        return {"query": query, "folder": resolved, "ok": False,
                "error": "access denied: actor not granted this workspace",
                "governance": {}, "companions": [],
                "grounding": {"applied": False}, "audit_id": None}

    # 1. sources: this workspace + descendants (access = workspace)
    src_map = _gather_sources(resolved, log_root, max_depth)
    sources = list(src_map.keys())
    if uses_works is None:
        uses_works = bool(sources)

    # 2. route to companions within this workspace's tree, gated
    plan = orchestrate(query, resolved, actor=actor, max_depth=max_depth,
                       log_root=log_root)
    go = [c for c in plan["companions"] if c["verdict"] == "GO"]

    # 3. dispatch the GO companions (governed); default hands off to the host
    disp = dispatcher or _handoff_dispatcher
    dispatched: list[dict[str, Any]] = []
    for comp in go:
        try:
            out = disp(folder=comp["folder"], query=query, skills=comp["skills"])
        except Exception as e:  # a companion failing must not crash the turn
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        dispatched.append({"workspace": comp["workspace"], "name": comp["name"], "output": out})

    # 4. fold companion outputs + the workspace's works into the prompt
    parts = [query]
    if sources:
        parts.append(f"[ground in {len(sources)} source(s) from this workspace; cite them]")
    for d in dispatched:
        o = d["output"]
        if isinstance(o, dict) and o.get("ok") and o.get("response"):
            parts.append(f"[from companion {d['name']}]: {o['response']}")
    prompt = "\n\n".join(parts)

    # 5. generate local-first (Lock gates any cloud rung)
    gen = cascade_for_workspace(resolved, prompt, max_tokens=max_tokens,
                           completer=completer, log_root=log_root)

    # 6. per-turn governance (chat decides which tools the turn needs)
    tg = turn_governance(egress_to_cloud=bool(gen.get("served_is_cloud")),
                         uses_works=uses_works, crosses_workspaces=bool(dispatched))

    # 7. grounding only when the turn rests on works (creators get credit)
    grounding: dict[str, Any] = {"applied": False}
    if uses_works:
        grounding = {"applied": True, "sources": sources,
                     "note": "answer must cite these works; uncredited works are "
                             "flagged so creators get credit (Grounder)"}

    # 8. record the turn
    audit_id = None
    audit_dropped = None
    try:
        log = MutationLog(resolved, log_root=log_root)
        ev = LogEvent(event="system", folder_path=resolved,
                      pair_id=f"ask:{(query or '')[:32]}", channel="system",
                      actor="workspaces-chat",
                      extra={"kind": "ask", "query": query, "tools": tg["tools"],
                             "companions": [d["workspace"] for d in dispatched],
                             "served_by": gen.get("served_by"),
                             "grounding": grounding["applied"]})
        audit_id = log.append(ev)
    except Exception as exc:
        from .audit_drop import record as _record_drop
        audit_dropped = _record_drop("workspace_orchestrate.ask", exc,
                                     folder=resolved, log_root=log_root)

    return {
        "query": query,
        "folder": resolved,
        "ok": bool(gen.get("ok")),
        "answer": gen.get("response", ""),
        "served_by": gen.get("served_by", ""),
        "governance": tg,
        "companions": dispatched,
        "grounding": grounding,
        "audit_id": audit_id,
        "cascade": gen,
    }
