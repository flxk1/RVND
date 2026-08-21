# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governed cross-workspace links and reads.

A workspace can reference another workspace laterally — not only through ancestor/
descendant nesting, but as a *source* it reads from or a *companion* capability
applied to it. Both directions are one primitive: a link with a role. Crossing a
workspace boundary is a governed act — every cross-workspace read is ruled by the action
gate (GO / CONDITIONAL / NO-GO) and, when allowed, recorded on the *target*
workspace's signed audit chain with provenance back to the source pairs.

Scope:
- Does:     gate the boundary crossing, record it on the target's signed chain,
            and assemble references to the source pairs.
- Does not: run the companion's skill — the classification / "shadow workflow"
            is the pluggable layer that consumes the assembled references — and
            does not copy raw source bodies. The Lock governs what content may
            cross; this records references, not contents.

Roles map onto the two drag directions:
- ROLE_SOURCE    (workspace → companion): the workspace feeds the companion as input.
- ROLE_COMPANION (companion → workspace): the companion is applied to the workspace.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .action_gate import ActionRequest, StandingApproval, Verdict, gate
from .seal_binding import read_pairs
from .mutation_log import LogEvent, MutationLog
from .principal import get_request_principal, principal_workspace_member

ROLE_SOURCE = "source"
ROLE_COMPANION = "companion"


def cross_workspace_read(
    target_folder: str | Path,
    source_folders: Iterable[str | Path],
    *,
    agent: str = "companion",
    role: str = ROLE_SOURCE,
    autonomy_grade: str = "L2",
    posture: str = "balanced",
    footprint: tuple[str, ...] = ("personal-data",),
    standing_approvals: Iterable[StandingApproval] = (),
    log_root: str | Path | None = None,
) -> dict:
    """Link ``target_folder`` to each source workspace and, where the gate allows,
    read the source's pair references onto the target's signed chain.

    Returns ``{target, role, links: [{source, role, verdict, reason, pair_ids,
    audit_id, error?}]}``. A NO-GO link carries no pair_ids and no audit_id —
    the read did not happen. A CONDITIONAL link is recorded but flags that the
    crossing needs human sign-off before its content is used downstream.
    """
    target_folder = str(Path(target_folder).expanduser())
    target_log = MutationLog(target_folder, log_root=log_root)
    sas = tuple(standing_approvals)
    links: list[dict] = []

    for raw in source_folders:
        src = str(Path(raw).expanduser())
        request_principal = get_request_principal()
        if request_principal is not None:
            principal = str(request_principal.get("principal") or "")
            if not principal_workspace_member(principal, src, log_root=log_root):
                links.append({
                    "source": src,
                    "role": role,
                    "verdict": Verdict.NO_GO.value,
                    "reason": "the request principal is not an active member "
                              "of the source workspace",
                    "pair_ids": [],
                    "audit_id": None,
                    "error": "refused: cross-workspace reads require active "
                             "membership in every source workspace",
                })
                continue
        req = ActionRequest(
            agent=agent,
            action_class="cross-workspace-read",
            autonomy_grade=autonomy_grade,
            footprint=footprint,
            folder=src,
        )
        dec = gate(req, standing_approvals=sas, posture=posture)
        link = {
            "source": src,
            "role": role,
            "verdict": dec.verdict.value,
            "reason": dec.reason,
            "pair_ids": [],
            "audit_id": None,
        }
        if dec.verdict == Verdict.NO_GO:
            links.append(link)
            continue
        try:
            pairs = read_pairs(src, log_root=log_root)
        except Exception as exc:  # locked or unreadable source
            link["error"] = f"{type(exc).__name__}: {exc}"
            links.append(link)
            continue
        link["pair_ids"] = [pid for pid in pairs if pid]
        ev = LogEvent(
            event="system",
            folder_path=target_folder,
            pair_id=f"xworkspace:{src}",
            channel="system",
            actor=agent,
            extra={
                "kind": "cross-workspace-read",
                "source": src,
                "role": role,
                "verdict": dec.verdict.value,
                "source_pair_ids": link["pair_ids"],
            },
        )
        link["audit_id"] = target_log.append(ev)
        links.append(link)

    return {"target": target_folder, "role": role, "links": links}
