# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Project a workspace onto the workspace contract — governance-first.

The workspace contract is Workspaces' own form of the universal-unit idea (mapped from
Brain Protocol 31, where the unit is called a Cell; in Workspaces the unit is the
**workspace**, and that is the term used here). A workspace already carries most of the
contract: identity (the folder hash), knowledge (its pairs), context (its
policy), structure (descendant workspaces are children, cross-workspace links are edges,
ancestor workspaces are parent perspectives), execution (its pinned skills).

What makes this Workspaces and not a generic knowledge graph: **governance is the
DNA of the contract, not optional metadata.** Every workspace carries its five
governance tools — the gate, the Lock, the oversight dial, the Grounder (no
citation, no claim), and the signed audit chain — at every level of nesting, by
construction. A workspace without them is not
a workspace. So:

- the policy posture (oversight + lock) is always present, defaulted to full
  protection;
- every edge carries its gate verdict — ``ungoverned_edges > 0`` is a contract
  violation, not a tolerated state;
- the workspace's identity is its signed history (``specs.audit_trail`` + chain
  status), not merely a content hash;
- the posture is inherited fractally — same governance settings inside and out;
- this projection is detective and read-only; it honours the asymmetric flow
  (children up, parents down only by explicit publish) and never bypasses the
  gate. Any workspace *action* (execution) routes through ``action_gate``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .mutation_log import MutationLog, folder_hash


@dataclass
class WorkspaceContract:
    workspace_id: str
    name: str
    workspace_type: str                         # space | agent | composite | ...
    telos: str = ""
    status: str = "active"
    domains: list[str] = field(default_factory=list)
    # Governance is the DNA: the four tools, the policy posture, whether the
    # chain verifies and is signed, and whether every edge is gated.
    governance: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    skill_ids: list[str] = field(default_factory=list)
    exec_mode: str = "NONE"                 # NONE | SOFTWARE | LLM_ASSISTED | HYBRID
    knowledge: dict[str, Any] = field(default_factory=dict)   # pair_count, ...
    exposes: dict[str, Any] = field(default_factory=dict)      # skills, child_workspaces, ...
    children: list["WorkspaceContract"] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    parent_perspectives: list[dict[str, Any]] = field(default_factory=list)
    specs: dict[str, Any] = field(default_factory=dict)        # audit_trail, chain_ok
    signed: bool = False
    folder: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def describe_workspace(folder: str | Path,
                  *,
                  depth: int = 1,
                  log_root: str | Path | None = None) -> WorkspaceContract:
    """Project ``folder`` (a workspace) onto the workspace contract. Recurses into direct
    child workspaces while ``depth > 0`` — each child is the same contract shape, so
    the interior is self-similar with the exterior, governance and all."""
    resolved = str(Path(folder).expanduser().resolve())
    cid = folder_hash(resolved)
    name = Path(resolved).name or resolved

    # execution: pinned skills
    from .pinned_skills import list_pinned
    pins = _safe(lambda: [getattr(p, "id", None) or getattr(p, "skill_id", str(p))
                          for p in list_pinned(resolved, log_root=log_root)], [])
    exec_mode = "HYBRID" if pins else "NONE"

    # context: policy posture (defaults assume full protection). Read through
    # the resolver seam so an overlay (tenant ceiling) can cap it; core default
    # returns the folder's own policy unchanged.
    from .workspace_hooks import resolve_policy
    pol = _safe(lambda: resolve_policy(resolved), None)
    context: dict[str, Any] = {
        "oversight": getattr(pol, "oversight_default_level", "approve") if pol else "approve",
        "lock_mode": _safe(lambda: pol.lock_mode, "") if pol else "",
        "lock_enabled": getattr(pol, "privacy_lock_enabled", True) if pol else True,
    }

    # knowledge: pair count
    from .workspace_lock import read_pairs, replay
    pairs = _safe(lambda: read_pairs(resolved, log_root=log_root), {})
    knowledge = {"pair_count": len(pairs)}

    # edges from governed cross-workspace links (each carries its gate verdict)
    events = _safe(lambda: list(replay(resolved, log_root=log_root)), [])
    edges: list[dict[str, Any]] = []
    for e in events:
        extra = getattr(e, "extra", None) or {}
        if extra.get("kind") == "cross-workspace-read":
            edges.append({
                "from": cid,
                "to": folder_hash(extra["source"]) if extra.get("source") else "",
                "type": "applies-to" if extra.get("role") == "companion" else "reads",
                "role": extra.get("role"),
                "verdict": extra.get("verdict"),
                "label": f"{extra.get('role','source')} link -> {Path(extra.get('source','')).name}",
            })

    # specs: the signed audit trail (the workspace's distinctive contract field)
    log = _safe(lambda: MutationLog(resolved, log_root=log_root), None)
    chain_ok = None
    if log is not None:
        chain = _safe(lambda: log.verify_chain(), None)
        if chain is not None:
            chain_ok = getattr(chain, "ok", None)
            if chain_ok is None and isinstance(chain, dict):
                chain_ok = chain.get("ok")
    trail = [{"event": getattr(e, "event", ""), "actor": getattr(e, "actor", ""),
              "audit_id": getattr(e, "audit_id", ""), "ts": getattr(e, "ts", 0)}
             for e in events[-10:]]
    signed = any(getattr(e, "signature", "") for e in events)
    specs = {"audit_trail": trail, "chain_ok": chain_ok, "event_count": len(events)}

    # parent perspectives (ancestors) — invertible hierarchy
    from .memory import discover_ancestors, discover_descendants
    ancestors = _safe(lambda: discover_ancestors(resolved, log_root=log_root), [])
    parents = [{"parent_id": folder_hash(a), "folder": a,
                "telos_from_parent": "nesting (asymmetric: child flows up)"}
               for a in ancestors]

    # direct child workspaces — the fractal interior, governed at each level
    children: list[WorkspaceContract] = []
    if depth > 0:
        desc = _safe(lambda: discover_descendants(resolved, log_root=log_root), [])
        direct = [d for d in desc
                  if d != resolved and str(Path(d).parent) == resolved]
        for d in sorted(direct):
            children.append(describe_workspace(d, depth=depth - 1, log_root=log_root))

    # workspace-native typing: everything is a workspace; what it exposes names its kind.
    # companion = a workspace with skills AND nested workspaces; skill = skills, leaf;
    # context = knowledge, no skills; space = a holding/empty workspace.
    exposes = {"skills": len(pins), "child_workspaces": len(children),
               "knowledge_pairs": knowledge["pair_count"]}
    if pins and children:
        workspace_type = "companion"
    elif pins:
        workspace_type = "skill"
    elif knowledge["pair_count"]:
        workspace_type = "context"
    else:
        workspace_type = "space"

    # governance: the DNA. Present at every level; ungoverned_edges must be 0.
    governance = {
        "tools": ["gate", "lock", "oversight", "grounder", "audit-chain"],
        "oversight": context["oversight"],
        "lock_enabled": context["lock_enabled"],
        "lock_mode": context["lock_mode"],
        "chain_ok": chain_ok,
        "signed": signed,
        "edges_total": len(edges),
        "ungoverned_edges": sum(1 for e in edges if not e.get("verdict")),
    }

    return WorkspaceContract(
        workspace_id=cid, name=name, workspace_type=workspace_type,
        status="active", domains=[],
        governance=governance, context=context,
        skill_ids=pins, exec_mode=exec_mode, knowledge=knowledge, exposes=exposes,
        children=children, edges=edges, parent_perspectives=parents,
        specs=specs, signed=signed, folder=resolved,
    )
