# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Case index — the problem-solution graph's memory.

A solved case is worth more than its answer: it is evidence that a
*configuration* (a solver: walker profile, ND, skill) resolves a *problem
shape* (a fingerprint). This module records walker cases as chain events and
projects **solves-edges** out of the replay — the index the dispatcher can
consult before falling back to description matching.

No new store: a case is recorded by an event on the folder's signed chain
(``CaseRecorded``), the solves-edges are a pure replay projection, exactly
the party-registry pattern. Erasure, seal, and tamper evidence come free
from the substrate.

Doctrine carried over from :mod:`rvnd.applicability`: fingerprints are
deterministic facet sets; a facet that cannot be read confidently stays
UNSET, and an unset facet never excludes at match time — conservative in
the safe direction. No embeddings, no model in the loop.

Outcome vocabulary is closed and human-anchored: ``ratified`` and
``decided`` are the walker's two human closures and the only outcomes that
count as evidence; ``open`` is recorded (the attempt is history) but yields
no edge. The index therefore cannot accumulate self-asserted competence.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog

#: The walker's two human closures, plus the honest non-closure.
CASE_OUTCOMES = ("ratified", "decided", "open")

#: Outcomes that count as evidence on a solves-edge (human-closed only).
EVIDENCE_OUTCOMES = ("ratified", "decided")


# ── fingerprint ───────────────────────────────────────────────────────────────

def case_fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    """Deterministic problem fingerprint from a walker-shaped result.

    Reads ``inputs`` first (the walk's own record of what it consumed), the
    case dict second. Facets it cannot read stay unset — never guessed.
    """
    case = payload.get("case") or {}
    if hasattr(case, "to_dict"):
        case = case.to_dict()
    inputs = payload.get("inputs") or {}

    fp: dict[str, Any] = {}
    profile = inputs.get("profile") or case.get("profile") or ""
    if profile:
        fp["profile"] = profile

    rooms = inputs.get("rooms")
    if rooms is None:
        rooms = [g.get("pinpoint", "") for g in (case.get("grounds") or [])]
        rooms += list(case.get("gaps") or [])
    fp["rooms"] = sorted({r for r in rooms if r})

    for flag in ("stake", "personal"):
        if flag in inputs:
            fp[flag] = bool(inputs[flag])
    return fp


def _facets_compatible(stored: dict, query: dict) -> bool:
    """Conservative match: a facet UNSET on the stored edge never excludes;
    set-valued facets must overlap, scalar facets must agree."""
    for key, sval in stored.items():
        if key not in query:
            continue                       # query doesn't constrain this facet
        qval = query[key]
        if isinstance(sval, (list, tuple, set)):
            if not sval:
                continue                   # unset on the edge → applies broadly
            if not set(sval) & set(qval or ()):
                return False
        elif sval != qval:
            return False
    return True


# ── record (append) ───────────────────────────────────────────────────────────

def record_case(
    folder_context: str,
    result: dict[str, Any],
    *,
    actor: str,
    outcome: str,
    solver: str = "walker",
    log_root: Optional[str] = None,
) -> str:
    """Append a ``CaseRecorded`` event for one walker result.

    Fail-closed: an outcome outside :data:`CASE_OUTCOMES` raises BEFORE
    anything is appended. The input ``result`` is never mutated."""
    if outcome not in CASE_OUTCOMES:
        raise ValueError(
            f"unknown outcome '{outcome}'. Valid: {list(CASE_OUTCOMES)} — "
            "'ratified'/'decided' are the human closures; 'open' is honest.")

    fp = case_fingerprint(result)
    case = result.get("case") or {}
    if hasattr(case, "to_dict"):
        case = case.to_dict()
    question = ((result.get("inputs") or {}).get("question")
                or (case.get("problem") or {}).get("text") or "")

    log = MutationLog(folder_context, log_root=log_root)
    return log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"case:{solver}",
        channel="system",
        actor=actor,
        extra={
            "kind": "CaseRecorded",
            "fingerprint": fp,
            "outcome": outcome,
            "solver": solver,
            "question": question[:300],
            "resolution_type": (case.get("resolution") or {}).get("type", ""),
            "coverage": case.get("coverage", None),
        },
    ))


def record_dispatch_case(
    folder_context: str,
    *,
    solver: str,
    outcome: str,
    actor: str,
    rationale: str = "",
    question: str = "",
    fingerprint: Optional[dict[str, Any]] = None,
    log_root: Optional[str] = None,
) -> str:
    """Record a DISPATCHED solver's case — the bridge for skills, including
    adapter-ingested Cowork/MCP-client skills (``solver`` is an opaque id).

    A non-walker solver has no built-in human boundary, so the boundary is
    enforced here: an evidence outcome (``ratified``/``decided``) requires a
    named actor AND a written rationale, and the actor must not be known to
    the party registry as an agent or as suspended/killed — agent approvals
    never count, and the kill switch reaches case closures. ``open`` records
    honestly from anyone and yields no edge. Fail-closed: a refusal raises
    BEFORE anything is appended."""
    if outcome not in CASE_OUTCOMES:
        raise ValueError(
            f"unknown outcome '{outcome}'. Valid: {list(CASE_OUTCOMES)}")
    if not (solver or "").strip():
        raise ValueError("solver must be a non-empty id")

    if outcome in EVIDENCE_OUTCOMES:
        if not (actor or "").strip():
            raise ValueError(
                "an evidence outcome needs a named human actor — a "
                "dispatched skill cannot close its own case")
        if not (rationale or "").strip():
            raise ValueError(
                "an evidence outcome needs a written rationale — the "
                "closure is the human's, and it must say why")
        from .parties import list_parties
        known = {p["party_id"]: p for p in
                 list_parties(folder_context, log_root=log_root)["parties"]}
        party = known.get(actor)
        if party is not None:
            if party.get("party_kind") == "agent":
                raise ValueError(
                    f"actor '{actor}' is registered as an AGENT — agent "
                    "closures never count as evidence (approvals doctrine)")
            if party.get("status") != "active":
                raise ValueError(
                    f"actor '{actor}' is {party.get('status')} — a "
                    "non-active party cannot close a case into evidence")

    log = MutationLog(folder_context, log_root=log_root)
    return log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_context).expanduser().resolve()),
        pair_id=f"case:{solver}",
        channel="system",
        actor=actor or "system",
        extra={
            "kind": "CaseRecorded",
            "fingerprint": dict(fingerprint or {}),
            "outcome": outcome,
            "solver": solver,
            "question": (question or "")[:300],
            "rationale": (rationale or "")[:500],
            "closure": "dispatch-bridge",
        },
    ))


def _minimise(text: str) -> str:
    """Minimise a free-text excerpt at the Privacy-Lock boundary BEFORE it is
    persisted to local memory (alignment requirement: the fingerprint is
    derived and safe, the excerpt is not). Allow → original; minimise →
    redacted; refuse/unavailable → a placeholder. Conservative: a failure to
    classify never lets raw text through."""
    if not (text or "").strip():
        return ""
    try:
        from rvnd.lock import lock_text, Mode
        d = lock_text(text, context="", mode=Mode.STANDARD, source="triple")
        if d.action == "allow":
            return text
        if d.action == "minimise":
            return d.redacted_text or "[minimised]"
        return "[lock-withheld]"
    except Exception:
        return "[lock-unavailable]"


def record_token_case(
    folder_context: str,
    token,
    *,
    outcome: str,
    actor: str,
    rationale: str = "",
    solver: str = "skill",
    log_root: Optional[str] = None,
) -> str:
    """Persist the solution of an ISSUE TOKEN — the retain step of the CBR
    loop. The token's fingerprint (issue_type + profile + rooms) is stored so
    a future detection of the same SHAPE recalls this solver; the human-
    closure rules of :func:`record_dispatch_case` apply unchanged; the token's
    text excerpt is minimised at the Lock boundary before it lands."""
    return record_dispatch_case(
        folder_context, solver=solver, outcome=outcome, actor=actor,
        rationale=rationale, question=_minimise(getattr(token, "text", "")),
        fingerprint=token.fingerprint(), log_root=log_root)


def recall_for_token(
    folder_context: str,
    token,
    log_root: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Recall step of the CBR loop: prior solvers for this token's problem
    shape, evidence-ranked. issue_type narrows — a different issue with the
    same rooms does not match. Cold start returns []."""
    return retrieve(folder_context, token.fingerprint(), log_root=log_root)


# ── projections (replay) ──────────────────────────────────────────────────────

def solves_edges(
    folder_context: str,
    log_root: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Pure replay projection: one solves-edge per HUMAN-CLOSED case, in
    chain order. Open cases are history, not evidence."""
    log = MutationLog(folder_context, log_root=log_root)
    edges: list[dict[str, Any]] = []
    for evt in log.replay():
        extra = evt.extra or {}
        if extra.get("kind") != "CaseRecorded":
            continue
        if extra.get("outcome") not in EVIDENCE_OUTCOMES:
            continue
        edges.append({
            "fingerprint": extra.get("fingerprint") or {},
            "solver": extra.get("solver", ""),
            "outcome": extra["outcome"],
            "question": extra.get("question", ""),
            "receipt": evt.audit_id,
        })
    return edges


def retrieve(
    folder_context: str,
    fingerprint: dict[str, Any],
    log_root: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Rank solvers for a problem fingerprint by verified evidence.

    Deterministic and auditable: facet compatibility (conservative), then
    one row per solver with its evidence count and receipts. Ties break by
    solver id so the order is stable across runs."""
    by_solver: dict[str, dict[str, Any]] = {}
    for edge in solves_edges(folder_context, log_root=log_root):
        if not _facets_compatible(edge["fingerprint"], fingerprint):
            continue
        row = by_solver.setdefault(edge["solver"], {
            "solver": edge["solver"], "evidence": 0, "receipts": []})
        row["evidence"] += 1
        row["receipts"].append(edge["receipt"])
    return sorted(by_solver.values(),
                  key=lambda r: (-r["evidence"], r["solver"]))
