# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Local commercial-capacity projection over signed workspace histories.

This module is read-only. It performs no activation, enforcement, telemetry or
network access. Counts are reconstructed from party status and use-case
authority events after every contributing chain has been verified.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .mutation_log import MutationLog
from .workspace_registry import list_known_workspaces


def _legacy_uid(folder: str, party_id: str) -> str:
    raw = f"{Path(folder).expanduser().resolve()}\0{party_id}"
    return "legacy-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _enabled(parties: dict[tuple[str, str], dict[str, Any]],
             use_cases: dict[tuple[str, str], set[str]]) -> set[str]:
    authorised = {(folder, party_id)
                  for (folder, _), allowed in use_cases.items()
                  for party_id in allowed}
    return {
        rec["agent_uid"]
        for key, rec in parties.items()
        if rec.get("party_kind") == "agent"
        and rec.get("status", "active") == "active"
        and key in authorised
    }


def capacity_report(*, log_root: Optional[Path] = None,
                    folders: Optional[Iterable[str]] = None,
                    from_epoch: Optional[float] = None,
                    to_epoch: Optional[float] = None,
                    licensed_capacity: Optional[int] = None) -> dict[str, Any]:
    """Return current and peak enabled-agent capacity from signed histories.

    ``folders`` defaults to the known-workspace registry. A broken or unreadable
    chain makes ``verified`` false; its events are excluded and the report is
    marked incomplete rather than silently treated as zero.
    """
    start = float(from_epoch) if from_epoch is not None else float("-inf")
    end = float(to_epoch) if to_epoch is not None else time.time()
    if end < start:
        raise ValueError("report end precedes start")
    if licensed_capacity is not None and licensed_capacity < 0:
        raise ValueError("licensed_capacity must be non-negative")

    paths = list(folders) if folders is not None else [
        str(w.get("path", "")) for w in list_known_workspaces(log_root=log_root)
        if w.get("path")]
    events: list[tuple[float, str, str, Any]] = []
    chains: list[dict[str, Any]] = []
    for folder in sorted(set(paths)):
        try:
            log = MutationLog(folder, log_root=log_root)
            verification = log.verify_chain()
            replayed = list(log.replay())
            cryptographically_verified = (
                verification.ok
                and verification.legacy_events == 0
                and verification.unsigned_events == 0
            )
            chains.append({"folder": folder,
                           "ok": cryptographically_verified,
                           "chain_integrity_ok": verification.ok,
                           "legacy_events": verification.legacy_events,
                           "unsigned_events": verification.unsigned_events,
                           "total_events": verification.total_events,
                           "head_audit_id": (replayed[-1].audit_id
                                             if replayed else None)})
            if not cryptographically_verified:
                continue
            for event in replayed:
                if event.ts <= end:
                    events.append((event.ts, event.audit_id, folder, event))
        except Exception as exc:
            chains.append({"folder": folder, "ok": False,
                           "error": type(exc).__name__})

    events.sort(key=lambda item: (item[0], item[1]))
    parties: dict[tuple[str, str], dict[str, Any]] = {}
    use_cases: dict[tuple[str, str], set[str]] = {}
    peak: set[str] = set()
    peak_at: Optional[float] = None
    at_start: Optional[set[str]] = None

    def observe(ts: float) -> None:
        nonlocal peak, peak_at
        active = _enabled(parties, use_cases)
        if len(active) > len(peak):
            peak = active
            peak_at = ts

    for ts, _, folder, event in events:
        if at_start is None and ts >= start:
            at_start = _enabled(parties, use_cases)
            if len(at_start) > len(peak):
                peak, peak_at = set(at_start), start if start != float("-inf") else ts
        extra = event.extra or {}
        kind = extra.get("kind")
        party_id = str(extra.get("party_id", ""))
        if kind == "PartyRegistered" and party_id:
            key = (folder, party_id)
            prior_status = parties.get(key, {}).get("status", "active")
            uid = str(extra.get("agent_uid") or _legacy_uid(folder, party_id))
            parties[key] = {"party_kind": extra.get("party_kind"),
                            "agent_uid": uid, "status": prior_status}
        elif kind == "PartyStatus" and party_id:
            key = (folder, party_id)
            if key in parties:
                parties[key]["status"] = extra.get("status", "active")
        elif kind == "UseCaseRegistered":
            use_case_id = str(extra.get("use_case_id", ""))
            if use_case_id:
                use_cases[(folder, use_case_id)] = {
                    str(v) for v in (extra.get("allowed_agents") or [])}
        if ts >= start:
            observe(ts)

    if at_start is None:
        at_start = _enabled(parties, use_cases)
        peak = set(at_start)
        peak_at = None
    current = _enabled(parties, use_cases)
    verified = bool(chains) and all(c.get("ok") for c in chains)
    identity_basis = ("workspace_slot" if any(uid.startswith("legacy-")
                                              for uid in current | peak)
                      else "agent_uid")
    report_salt = secrets.token_bytes(16)
    peak_refs = sorted(hashlib.sha256(report_salt + uid.encode("utf-8"))
                       .hexdigest()[:20] for uid in peak)
    result = {
        "schema": "rvnd/licence-usage/v1",
        "generated_at_epoch": time.time(),
        "from_epoch": None if start == float("-inf") else start,
        "to_epoch": end,
        "licensed_capacity": licensed_capacity,
        "capacity_source": ("user_supplied" if licensed_capacity is not None
                            else "not_supplied"),
        "current_enabled_agents": len(current),
        "peak_enabled_agents": len(peak),
        "peak_at_epoch": peak_at,
        "identity_basis": identity_basis,
        "verified": verified,
        "incomplete": not verified,
        "workspace_count": len(paths),
        "chains": chains,
        "peak_agent_refs": peak_refs,
    }
    if licensed_capacity is not None:
        result["within_capacity"] = len(peak) <= licensed_capacity
    return result
