# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""decision_dossier — one pending decision's local context, in one read.

Joins the stored escalation surface with the raising actor's standing, run
history and recourse ladder, so a reviewer sees more than the queue one-liner
before claiming. An ungated local read with the same access posture as
decision_pending — including the queue's lazy upkeep (lease expiry, ladder
escalation), which may record events on any queue read. Outbound minimisation
is unchanged: the Lock-gated cards stay content-minimised. Grounding leaves
banded (thin/moderate/firm), never as a raw score. Panel seats stay sealed
until resolution: counts and commitments only, never a choice or rationale.
Run and use-case sections join by ``raised_by`` — attributed, not asserted.
Unknown or closed ids, and a failed replay, refuse whole (no partial dossier).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .._meters import verdict_tally
from .queue import DecisionQueue

RUN_LIMIT = 10          # most recent runs attributed to the raiser
OVERRIDE_LIMIT = 5      # most recent overrides recorded by the raiser

# The pending entry stores no structured link to a run or use case; the join
# key is the raising actor. The label rides the payload so a reader never
# mistakes attribution for a recorded causal link.
ATTRIBUTED_JOIN = ("attributed by raised_by — the raising actor's history,"
                   " not a recorded link to this escalation")


def _band(grounding: Any) -> str:
    """The decision_build banding: thin (< 0.6), moderate (< 0.9), firm."""
    try:
        g = float(grounding)
    except (TypeError, ValueError):
        g = 0.0
    return "thin" if g < 0.6 else "moderate" if g < 0.9 else "firm"


def _panel_public(entry: dict) -> Optional[dict[str, Any]]:
    """Panel state without content: seat counts, rule, who recorded, and the
    record commitments (hashes). Choices and rationales stay sealed."""
    state = DecisionQueue.panel_state(entry)
    if state is None:
        return None
    state["commitments"] = [r["commitment"]
                            for r in entry["panel"]["seat_records"]]
    return state


def _basis(surface: dict) -> dict[str, Any]:
    """The stored surface with every option's grounding stripped to its band
    plus a supporting-norm count — the raw score never leaves the server."""
    options = []
    for opt in surface.get("options") or []:
        o = dict(opt)
        g = o.pop("grounding", None)
        if "grounding_band" not in o:
            o["grounding_band"] = _band(g)
        o["supporting_count"] = len(o.get("supporting") or [])
        options.append(o)
    return {"query": surface.get("query", ""),
            "esc_reason": surface.get("esc_reason", ""),
            "context": surface.get("context", ""),
            "single_reading_warning": bool(surface.get("single_reading_warning")),
            "options_may_be_incomplete": bool(
                surface.get("options_may_be_incomplete", True)),
            "options": options}


def _standing(folder_context: str, raised_by: str, log_root) -> dict[str, Any]:
    """The raiser's roster record, per-actor verdict tally and override
    history. An unregistered raiser is flagged unknown, never invented."""
    from ..parties import list_parties
    from ..review_card import overrides_for
    roster = list_parties(folder_context,
                          log_root=log_root).get("parties") or []
    rec = next((p for p in roster if p.get("party_id") == raised_by), None)
    if rec is None:
        party: dict[str, Any] = {"party_id": raised_by, "registered": False}
    else:
        party = {"party_id": raised_by, "registered": True,
                 "name": rec.get("name") or raised_by,
                 "party_kind": rec.get("party_kind"),
                 "status": rec.get("status", "active"),
                 "grade": rec.get("grade"),
                 "competences": list(rec.get("competences") or [])}
    mine = [o for o in overrides_for(folder_context, log_root=log_root)
            if o.get("actor") == raised_by]
    return {"party": party,
            "meter": verdict_tally(folder_context, log_root, actor=raised_by),
            "overrides": {"count": len(mine),
                          "recent": [{"stage": o.get("stage"),
                                      "field": o.get("field"),
                                      "rationale": o.get("rationale"),
                                      "receipt": o.get("receipt")}
                                     for o in mine[-OVERRIDE_LIMIT:]]}}


def _recourse(entry: dict, use_cases: list[dict]) -> dict[str, Any]:
    """The declared widening ladder plus the obligations, redress routes and
    reserved acts riding the use cases the raiser is permitted on."""
    rows = [{"use_case_id": uc.get("use_case_id"), "name": uc.get("name"),
             "risk": uc.get("risk"),
             "obligations": list(uc.get("obligations") or []),
             "redress": list(uc.get("redress") or []),
             "reserved_acts": list(uc.get("reserved_acts") or [])}
            for uc in use_cases]
    return {"escalate_to": entry.get("escalate_to", ""),
            "escalate_after_s": entry.get("escalate_after_s", 0),
            "escalated_at": entry.get("escalated_at"),
            "write_reconfirm": bool(entry.get("write_reconfirm")),
            "panel_rule": (entry.get("panel") or {}).get("rule"),
            "use_cases": {"join": ATTRIBUTED_JOIN, "rows": rows}}


def decision_dossier(folder_context: str, *, decision_id: str,
                     log_root: Optional[str] = None) -> dict[str, Any]:
    """Assemble the dossier for one open decision-queue item, or refuse in
    words. Approval-request and contract-approval inboxes are separate engines
    and are not hydrated here."""
    q = DecisionQueue(folder_context, log_root=log_root)
    entry = q.get(str(decision_id or ""))
    if entry is None or entry.get("state") != "open":
        return {"ok": False, "error": f"no open decision {decision_id!r}"}
    raised_by = entry.get("raised_by", "")
    try:
        from ..operations import runs_for
        from ..use_case import list_use_cases
        item = {k: entry.get(k) for k in
                ("decision_id", "state", "opened_at", "priority", "decide_by",
                 "competence", "assignment_basis", "raised_by", "claimed_by",
                 "claim_expires_at", "claim_ttl_s")}
        item["overdue"] = bool(entry.get("decide_by")) and \
            entry["decide_by"] <= datetime.now(timezone.utc).isoformat()
        item["panel"] = _panel_public(entry)
        mine = [r for r in runs_for(folder_context, log_root=log_root)
                if r.get("agent_id") == raised_by]
        ucs = [uc for uc in list_use_cases(folder_context, log_root=log_root)
               if raised_by in (uc.get("allowed_agents") or [])]
        dossier = {"decision_id": entry["decision_id"],
                   "item": item,
                   "basis": _basis(entry.get("surface") or {}),
                   "runs": {"join": ATTRIBUTED_JOIN, "total": len(mine),
                            "rows": mine[-RUN_LIMIT:]},
                   "standing": _standing(folder_context, raised_by, log_root),
                   "recourse": _recourse(entry, ucs)}
    except Exception as e:                                       # noqa: BLE001
        # Fail-closed: a replay that raises must not yield a dossier missing
        # sections that then reads as "no history, no obligations".
        return {"ok": False,
                "error": "dossier assembly failed — refusing a partial"
                         f" dossier: {type(e).__name__}: {e}"}
    return {"ok": True, "folder_context": folder_context, "dossier": dossier}
