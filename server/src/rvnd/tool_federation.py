# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governance bus (B′) — federate a third-party governance tool's verdict.

Rvnd is the NEUTRAL audited layer that joins N third-party governance tools into one
signed decision. The doctrine (rvnd-neutral-audit-substrate + governor-not-doer):

  * Rvnd NEVER calls the tool and NEVER asserts its normativity. The HOST invokes the
    tool (per the mcp_tool adapter — invoking here would bypass the host's tool-call
    audit); Rvnd RECORDS *that the tool returned X over input H* and MAPS X to the
    canonical tri-state via verdict.from_risk_tier. "Attributed, not asserted."
  * The mapping is data, not code: the neutral risk-tier→tri-state table lives in
    verdict.py; which tier holds/denies for THIS workspace is policy the registry
    authors.
  * Replay-safe: the record carries the verdict + an input DIGEST + the raw output —
    an auditor reconstructs the decision without re-running a (possibly
    non-deterministic) tool.
  * Strictest-wins join (verdict.strictest), fail-closed: a lone DENY from any source
    denies; disagreement is RECORDED per source, never hidden behind the winner.
  * Killable: a revoked tool's verdicts are dropped from the join (the kill switch).
  * Resolvable: a recorded human override picks ONE of the words the tools actually
    emitted (never invents a verdict, never blesses a floor); any later movement of
    the join's ground truth — a new linked verdict, a channel or group revocation,
    a group-floor change — supersedes it fail-closed.

A federated tool is a registered connector (connectors.register_connector); its
verdicts attach by the connector's ``use_cases`` link — the same linkage the tag
data-lens uses.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from .connectors import list_connectors
from .mutation_log import LogEvent, MutationLog
from .verdict import Verdict, coerce, from_risk_tier, strictest

_TOOL_VERDICT = "tool-verdict"
_TOOL_REVOKED = "tool-revoked"
_GROUP_POLICY = "group-policy"
_GROUP_REVOKED = "group-revoked"
_FED_OVERRIDE = "federation-override"


def _digest(s: str) -> str:
    return "sha256:" + hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:32]


def _append(folder: str, actor: str, log_root: Optional[str], pair_id: str, extra: dict) -> str:
    log = MutationLog(Path(folder), log_root=Path(log_root) if log_root else None)
    return log.append(LogEvent(event="system", folder_path=str(folder),
                               pair_id=pair_id, channel="system",
                               actor=actor or "host", extra=extra))


def record_tool_verdict(folder: str, *, connector_id: str, raw_tier: str,
                        input_ref: str = "", actor: str = "host",
                        log_root: Optional[str] = None) -> dict[str, Any]:
    """Record that a federated tool returned ``raw_tier`` over the input ``input_ref``.
    Rvnd maps it to the tri-state (attributed, not asserted) and signs the record.
    The HOST already invoked the tool; this never touches the network."""
    if not (connector_id or "").strip():
        raise ValueError("a tool verdict needs the connector_id of the federated tool")
    v = from_risk_tier(raw_tier)
    rec = {"kind": _TOOL_VERDICT, "connector_id": connector_id,
           "raw_output": raw_tier, "input_digest": _digest(input_ref),
           "verdict": v.value}
    audit_id = _append(folder, actor, log_root, connector_id, rec)
    return {"ok": True, "connector_id": connector_id, "verdict": v.value,
            "input_digest": rec["input_digest"], "audit_id": audit_id}


def revoke_tool(folder: str, *, connector_id: str, actor: str = "user",
                reason: str = "", log_root: Optional[str] = None) -> dict[str, Any]:
    """The kill switch: a revoked tool's verdicts are dropped from every future join."""
    audit_id = _append(folder, actor, log_root, connector_id,
                       {"kind": _TOOL_REVOKED, "connector_id": connector_id, "reason": reason})
    return {"ok": True, "connector_id": connector_id, "revoked": True, "audit_id": audit_id}


def set_group_floor(folder: str, *, group_id: str, floor: str, actor: str = "user",
                    log_root: Optional[str] = None) -> dict[str, Any]:
    """Set a GROUP's policy floor — an MCP client/tenant sends N channels; this is the
    group-bus minimum that governs ALL of them collectively (a channel may be stricter,
    never looser). permit|hold|deny; latest-wins per group."""
    fl = (floor or "permit").strip().lower()
    if fl not in ("permit", "hold", "deny"):
        raise ValueError(f"floor must be permit|hold|deny, got {floor!r}")
    if not (group_id or "").strip():
        raise ValueError("group_id required")
    audit_id = _append(folder, actor, log_root, "group:" + group_id,
                       {"kind": _GROUP_POLICY, "group_id": group_id, "floor": fl})
    return {"ok": True, "group_id": group_id, "floor": fl, "audit_id": audit_id}


def revoke_group(folder: str, *, group_id: str, actor: str = "user", reason: str = "",
                 log_root: Optional[str] = None) -> dict[str, Any]:
    """Group kill switch: mute a whole client/tenant — every channel in the group is
    dropped from every future join at once."""
    if not (group_id or "").strip():        # match set_group_floor — never revoke "" (would mute ungrouped)
        raise ValueError("group_id required")
    audit_id = _append(folder, actor, log_root, "group:" + group_id,
                       {"kind": _GROUP_REVOKED, "group_id": group_id, "reason": reason})
    return {"ok": True, "group_id": group_id, "revoked": True, "audit_id": audit_id}


def federated_decision(folder: str, *, use_case_id: str, local: Verdict = Verdict.PERMIT,
                       log_root: Optional[str] = None) -> dict[str, Any]:
    """Join the LOCAL verdict with every non-revoked federated tool linked to this use
    case (strictest-wins). Returns the decision PLUS a per-source breakdown, so a
    human sees the spread — disagreement is recorded, never swallowed by the winner."""
    linked = {c.get("connector_id"): c for c in list_connectors(folder, log_root=log_root)
              if use_case_id in (c.get("use_cases") or [])}
    linked_groups = {(c.get("group") or "").strip()
                     for c in linked.values()} - {""}          # groups that govern this join
    latest: dict[str, dict[str, Any]] = {}     # connector_id -> latest tool-verdict record
    revoked: set[str] = set()                  # individually-killed channels
    group_floor: dict[str, str] = {}           # group_id -> latest policy floor
    group_revoked: set[str] = set()            # killed groups (whole client/tenant)
    override_evt = None                        # latest human override for this use case
    # The override's lifetime tracks the join's GROUND TRUTH: it is superseded
    # (fail-closed, stays visible) when ANYTHING the join is made of moves after
    # it — a linked tool speaks again, a linked channel or its group is revoked
    # (the kill switch must beat an older loosening), or a linked group's floor
    # changes (the newest tightening must beat an older loosening).
    override_stale = False
    log = MutationLog(Path(folder), log_root=Path(log_root) if log_root else None)
    for evt in log.replay():
        e = evt.extra or {}
        kind = e.get("kind")
        if kind == _GROUP_POLICY:
            _gid = (e.get("group_id") or "").strip()
            if _gid:                       # ignore a tampered empty group_id (else it would
                group_floor[_gid] = e.get("floor", "permit")   # phantom-govern ungrouped channels)
                if override_evt is not None and _gid in linked_groups:
                    override_stale = True  # a linked group's floor moved after the override
            continue
        if kind == _GROUP_REVOKED:
            _gid = (e.get("group_id") or "").strip()
            if _gid:
                group_revoked.add(_gid)
                if override_evt is not None and _gid in linked_groups:
                    override_stale = True  # a linked source's group was killed after the override
            continue
        if kind == _FED_OVERRIDE:
            if e.get("use_case_id") == use_case_id:
                override_evt = evt             # latest-wins; a newer override restarts
                override_stale = False         # its lifetime (supersession is per override)
            continue
        cid = e.get("connector_id")
        if not cid or cid not in linked:
            continue
        if kind == _TOOL_REVOKED:
            revoked.add(cid)
            if override_evt is not None:       # revoke_tool promises the revoked reading is
                override_stale = True          # dropped from every future join — the override
                                               # must not carry it onward
        elif kind == _TOOL_VERDICT:
            latest[cid] = e                    # latest-wins per tool
            if override_evt is not None:       # any newer reading from a linked source
                override_stale = True          # supersedes the human override (fail-closed)
    sources = []
    revoked_sources = []                       # killed channels that HAD a verdict (kept visible)
    verdicts = [local]
    for cid in sorted(linked):
        grp = (linked[cid].get("group") or "").strip()
        # floor: absent ⇒ permit (no floor, back-compat); a corrupt value ⇒ DENY (fail-safe),
        # never a crash or a silent permit. Verdicts read from the log are coerced the same.
        _fs = linked[cid].get("floor")
        floor = Verdict.PERMIT if _fs is None else coerce(_fs, default=Verdict.DENY)
        # GROUP policy: the channel's group floor governs it collectively; absent ⇒ permit,
        # corrupt ⇒ DENY. A channel can only be made STRICTER by its group, never looser.
        gfloor = coerce(group_floor.get(grp), default=Verdict.DENY) if grp in group_floor else Verdict.PERMIT
        rec = latest.get(cid)
        tool_v = coerce(rec.get("verdict"), default=Verdict.DENY) if rec else None
        if cid in revoked or (grp and grp in group_revoked):   # channel OR its group killed
            # record EVERY killed channel (even with no verdict) so a revocation's full
            # membership is reconstructable; carry the group floor that governed it.
            revoked_sources.append({"connector_id": cid, "group": grp,
                                    "group_floor": gfloor.value,
                                    "verdict": tool_v.value if tool_v is not None else None,
                                    "raw_output": rec.get("raw_output") if rec else None})
            continue
        # the channel's effective contribution = strictest(channel floor, GROUP floor, verdict)
        chan_v = strictest(*([floor, gfloor] + ([tool_v] if tool_v else [])))
        if tool_v is None and chan_v == Verdict.PERMIT:
            continue                           # no verdict + no floor → contributes nothing; omit
            # (a tool that REPORTED permit is kept visible; a floor that holds/denies is kept)
        verdicts.append(chan_v)
        sources.append({"connector_id": cid, "verdict": chan_v.value,
                        "group": grp, "group_floor": gfloor.value,
                        "floor": floor.value,
                        "tool_verdict": tool_v.value if tool_v else None,
                        "raw_output": rec.get("raw_output") if rec else None,
                        "input_digest": rec.get("input_digest") if rec else None})
    decision = strictest(*verdicts)
    distinct = {s["verdict"] for s in sources} | {local.value}
    # Human override (additive keys only — decision/sources/disagreement keep their
    # exact meaning). While a non-superseded override stands, effective_decision is
    # the human's pick; a superseded override stops applying but stays visible.
    override = None
    effective = decision
    if override_evt is not None:
        oe = override_evt.extra or {}
        ov = coerce(oe.get("verdict"), default=Verdict.DENY)   # tampered override → DENY
        override = {"verdict": ov.value, "actor": override_evt.actor,
                    "reason": oe.get("reason", ""),
                    "audit_id": override_evt.audit_id,
                    "spread_digest": oe.get("spread_digest"),
                    "superseded": override_stale}
        if not override_stale:
            effective = ov
    return {"use_case_id": use_case_id, "decision": decision.value,
            "local": local.value, "sources": sources,
            "disagreement": len(distinct) > 1,
            "revoked": sorted(revoked), "revoked_sources": revoked_sources,
            "override": override, "effective_decision": effective.value}


def record_federation_override(folder: str, *, use_case_id: str, verdict: str,
                               actor: str, reason: str,
                               log_root: Optional[str] = None) -> dict[str, Any]:
    """Record a human's resolution of a federated disagreement. Refused unless the
    current join for the use case actually disagrees, and the chosen verdict must
    be a word a non-revoked TOOL actually emitted — never a floor-driven
    contribution, never the internal local default. A human resolves a split
    between real tool readings; fewer than two distinct emitted words means the
    split (if any) is authored policy, resolved by editing the floor. The signed
    record carries the per-source spread (and its digest) so replay reconstructs
    what the human saw."""
    if not (use_case_id or "").strip():
        raise ValueError("an override needs the use_case_id whose split it resolves")
    if not (actor or "").strip():
        raise ValueError("an override needs a named actor; an anonymous override is refused")
    if not (reason or "").strip():
        raise ValueError("an override needs a reason; a silent override is refused")
    join = federated_decision(folder, use_case_id=use_case_id, log_root=log_root)
    if not join["disagreement"]:
        raise ValueError(
            f"nothing to resolve for {use_case_id!r}: the join reads "
            f"{join['decision']!r} without disagreement")
    # The candidate set is the TOOL-EMITTED words only: the raw verdicts of the
    # non-revoked, non-group-revoked sources in the current join. Floor-driven
    # contributions are not candidates — a floor is authored policy, not a reading.
    emitted = sorted({s["tool_verdict"] for s in join["sources"] if s.get("tool_verdict")})
    if len(emitted) < 2:
        raise ValueError(
            f"no tool split to resolve for {use_case_id!r}: the tools emitted "
            f"{emitted}; a floors-vs-tool split is authored policy — resolve it "
            "by editing the floor, not by overriding")
    spread = {s["connector_id"]: s["verdict"] for s in join["sources"]}
    v = (verdict or "").strip().lower()
    if v not in emitted:
        raise ValueError(
            f"override verdict {verdict!r} is not among the tool-emitted readings "
            f"{emitted}; a human picks one of the real readings, never invents one")
    spread_digest = _digest(json.dumps(spread, sort_keys=True, separators=(",", ":")))
    rec = {"kind": _FED_OVERRIDE, "use_case_id": use_case_id, "verdict": v,
           "reason": reason, "spread": spread, "spread_digest": spread_digest,
           "decision_before": join["decision"]}
    audit_id = _append(folder, actor, log_root, "override:" + use_case_id, rec)
    return {"ok": True, "use_case_id": use_case_id, "verdict": v, "actor": actor,
            "spread_digest": spread_digest, "audit_id": audit_id}


def tool_call_plan(folder: str, *, connector_id: str, input_ref: str = "",
                   log_root: Optional[str] = None) -> dict[str, Any]:
    """Project the call descriptor the HOST uses to invoke a federated tool over
    ``input_ref`` (pull model, read-only, no log write). RVND plans, never calls:
    the host executes the tool and reports back through record_tool_verdict —
    attributed, not asserted. Fail-closed: an unknown or unbound connector, a
    revoked connector, or a killed group refuses — a killed tool is never
    planned for invocation."""
    cid = (connector_id or "").strip()
    if not cid:
        raise ValueError("tool_call_plan needs a connector_id")
    rec = next((c for c in list_connectors(folder, log_root=log_root)
                if c.get("connector_id") == cid), None)
    if rec is None:
        raise ValueError(f"unknown connector {connector_id!r}")
    tref = rec.get("tool_ref")
    if not isinstance(tref, dict) or not str(tref.get("tool_name") or "").strip():
        raise ValueError(f"connector {connector_id!r} carries no tool_ref binding")
    grp = (rec.get("group") or "").strip()
    log = MutationLog(Path(folder), log_root=Path(log_root) if log_root else None)
    for evt in log.replay():
        e = evt.extra or {}
        kind = e.get("kind")
        if kind == _TOOL_REVOKED and e.get("connector_id") == cid:
            raise ValueError(f"connector {connector_id!r} is revoked; "
                             "a killed tool is never planned for invocation")
        if kind == _GROUP_REVOKED and grp and (e.get("group_id") or "").strip() == grp:
            raise ValueError(f"group {grp!r} is revoked; "
                             "a killed group's tools are never planned for invocation")
    mapping = tref.get("arg_mapping") or {}
    payload = {"input_ref": input_ref}
    args = {mapping.get(k, k): v for k, v in payload.items()}
    return {"kind": "mcp_tool_call_descriptor",
            "tool_name": str(tref["tool_name"]),
            "args": args,
            "input_digest": _digest(input_ref),
            "provenance": {"connector_id": cid, "group": grp,
                           "folder_context": str(folder)},
            # the return path: the host records the tool's answer through the
            # existing tool_verdict op — RVND signs THAT the tool said X over
            # digest(input_ref), never that X is true.
            "map_result_via": "tool_verdict"}
