# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Review cards + override-as-event — the human layer's spine.

Every node in the solver graph — Lock decision, analysis, routing choice,
reserved act — projects into ONE card shape so the whole pipeline is reviewed
through a single contract: what it did, why (explanation + cited sources at
their authority tier), its signals (completeness / grounding / confidence),
the inputs/spans it used, and an override affordance.

The differentiator is the override. A human override is NOT a throwaway edit:
it is a signed chain event (attributable), it is retained as a correction the
case memory can learn from, and a recurring override proposes a rule. That is
what makes every human touch compound instead of evaporating — the engine of
the oversight-scaling thesis, and the thing no fixed-flow tool does.

Human experts are then just nodes a card is delivered to (by competence, via
`parties.route_approvers`) through a connector (send-md is the always-works
floor; email/calendar/ticket/Slack are the upgrade). This module builds the
card and records the override; delivery is the connector layer's job.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog

STAGES = ("lock", "analysis", "routing", "oversight")
_NEEDS_REVIEW_BANDS = ("low", "medium")


def review_card(
    *,
    node_id: str,
    stage: str,
    what: str,
    why: str,
    citations: Optional[list[dict]] = None,
    signals: Optional[dict[str, Any]] = None,
    inputs: Optional[list[dict]] = None,
    reserved_act: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Project a node's output into the uniform review card.

    ``status`` is derived: a reserved act → 'reserved' (a human MUST act,
    whatever the certainty); else low/medium completeness → 'needs-review';
    else 'auto'. Reserved outranks completeness — a reserved act is owed even
    when the analysis is certain."""
    signals = signals or {}
    band = signals.get("completeness")
    if reserved_act is not None:
        status, human_required = "reserved", True
    elif band in _NEEDS_REVIEW_BANDS:
        status, human_required = "needs-review", True
    else:
        status, human_required = "auto", False
    return {
        "node_id": node_id,
        "stage": stage,
        "what": what,
        "why": why,
        "citations": list(citations or []),
        "signals": dict(signals),
        "inputs": list(inputs or []),
        "reserved_act": reserved_act,
        "status": status,
        "override": {"editable": True, "human_required": human_required},
    }


def record_override(
    folder_context: str,
    *,
    card: dict[str, Any],
    actor: str,
    field: str,
    new_value: Any,
    rationale: str,
    old_value: Any = None,
    log_root: Optional[str] = None,
) -> str:
    """Record a human override of a card as a signed chain event. Fail-closed:
    an override is a human correction, so it needs a named actor AND a written
    rationale. The event is the audit record AND the seed of a correction-case
    the memory can learn from."""
    if not (actor or "").strip():
        raise ValueError("an override needs a named human actor")
    if not (rationale or "").strip():
        raise ValueError("an override needs a written rationale (why change it)")
    log = MutationLog(folder_context, log_root=log_root)
    return log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"override:{card.get('node_id', '')}",
        channel="system",
        actor=actor,
        extra={
            "kind": "NodeOverride",
            "node_id": card.get("node_id", ""),
            "stage": card.get("stage", ""),
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
            "rationale": (rationale or "")[:500],
        },
    ))


def overrides_for(
    folder_context: str,
    log_root: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Pure replay projection of every recorded override, in chain order."""
    log = MutationLog(folder_context, log_root=log_root)
    out: list[dict[str, Any]] = []
    for evt in log.replay():
        extra = evt.extra or {}
        if extra.get("kind") != "NodeOverride":
            continue
        out.append({
            "node_id": extra.get("node_id", ""),
            "stage": extra.get("stage", ""),
            "field": extra.get("field", ""),
            "old_value": extra.get("old_value"),
            "new_value": extra.get("new_value"),
            "rationale": extra.get("rationale", ""),
            "actor": evt.actor,
            "receipt": evt.audit_id,
        })
    return out


def recurrence_flags(
    folder_context: str,
    threshold: int = 3,
    log_root: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Where humans keep making the SAME override, the system should change —
    a recurring (stage, field) override at or above ``threshold`` proposes a
    rule. This is how captured overrides become governance feedback, not just
    audit entries."""
    counts: dict[tuple, int] = {}
    for o in overrides_for(folder_context, log_root=log_root):
        k = (o["stage"], o["field"])
        counts[k] = counts.get(k, 0) + 1
    return [{"kind": "propose-rule", "stage": s, "field": f, "count": n}
            for (s, f), n in sorted(counts.items()) if n >= threshold]
