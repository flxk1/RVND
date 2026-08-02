# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Security dashboard — an auditable projection over the signed chain's security decisions.

Same shape as `conformity` (evidence-as-API: a read-only projection over the mutation log, never
writes state) and the governance-map contract (summary + roll-ups + group-by), so it renders in
the same panel machinery and exports over the same connector. It counts and groups the security
events the gates already emit — `IngestQuarantine`, a `CardGate` verdict, the privacy
`EraseGuardHit` — into: what was admitted / held / rejected, which rules fired, from which source.

Because the source is the tamper-evident hash chain, this is EVIDENCE, not telemetry — a hold or
reject can be replayed and verified. It DISPLAYS; it does not certify.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from . import card_gate as _cg

SCHEMA_VERSION = "security/v1"
#: Honesty rides in the DATA, not just docstrings: any consumer (panel, connector, export) that
#: reads the projection also reads what it does NOT promise. The ingest gate is a denylist
#: tripwire over known shapes — it reduces specific vectors; a clean board is not proof of safety.
LIMITS = {
    "kind": "tripwire, not containment",
    "statement": "Reports the security decisions the gates recorded — what was flagged, held, or "
                 "rejected. It does NOT certify safety: the ingest gate is a denylist over known "
                 "injection/file-shape patterns (coverage-bounded), so a clean board means 'no "
                 "known-bad pattern matched', not 'safe'. Declares, never certifies.",
    "erasure": "Erasure is data-level: a purge tombstones the pair content out of this folder's "
               "chain and blocks re-ingestion. It does not rewrite history elsewhere — a copy "
               "that already left the boundary, an agent's memory, or a downstream system is "
               "out of its reach.",
}
#: decision events (a gate verdict) + the human-release marker that clears a hold
_DECISION_KINDS = frozenset({"IngestQuarantine", "CardGate", "EraseGuardHit"})
SECURITY_KINDS = _DECISION_KINDS | frozenset({"QuarantineReleased"})
FACETS = ("verdict", "rule", "source", "kind")
#: worst-first severity RANK — max() over the raw strings is lexicographic ("low" > "high")
#: and would systematically UNDER-report a mixed-severity row on a security panel.
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


def _row(e: dict[str, Any]) -> dict[str, Any]:
    kind = e.get("kind", "")
    raw = e.get("admission") or e.get("verdict") or ("deny" if kind == "EraseGuardHit" else "hold")
    threats = e.get("threats") or []
    rules = [t.get("label") for t in threats if t.get("label")]
    if not rules and e.get("rule"):
        rules = [e["rule"]]
    return {
        "kind": kind,
        "verdict": _cg.normalise(raw),                 # shared lattice: allow | hold | deny
        "raw_verdict": raw,
        "source": e.get("file_path") or e.get("source") or e.get("candidate") or "(unknown)",
        "rules": rules,
        "severity": max((t.get("severity", "") for t in threats),
                        key=lambda s: _SEVERITY_RANK.get(s, 0), default=""),
        "reason": e.get("reason", ""),
        "event_id": e.get("event_id") or e.get("pair_id") or "",
    }


def _facet(row: dict[str, Any], facet: str) -> list[str]:
    if facet == "rule":
        return row["rules"] or ["(no rule)"]           # a row explodes across its rules
    return [row.get("verdict" if facet == "verdict" else facet, "(unknown)")]


def project(events: list[dict[str, Any]], *, group_by: str = "verdict") -> dict[str, Any]:
    """Project security events → the dashboard contract: version · summary · group tree · rows.
    ``events`` are the ``extra`` payloads off the chain (each carrying a ``kind``)."""
    if group_by not in FACETS:
        raise ValueError(f"unknown facet {group_by!r}; one of {FACETS}")
    rows = [_row(e) for e in events if e.get("kind") in _DECISION_KINDS]
    released = {e.get("released_event_id") or e.get("event_id") or i
                for i, e in enumerate(events) if e.get("kind") == "QuarantineReleased"}
    n_released = len(released)
    vc = Counter(r["verdict"] for r in rows)
    rule_c: Counter = Counter(x for r in rows for x in (r["rules"] or []))
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        for k in _facet(r, group_by):
            buckets.setdefault(k, []).append(r)
    groups = [{
        "key": k, "count": len(v),
        "allow": sum(1 for r in v if r["verdict"] == _cg.ALLOW),
        "hold": sum(1 for r in v if r["verdict"] == _cg.HOLD),
        "deny": sum(1 for r in v if r["verdict"] == _cg.DENY),
        "worst_verdict": _cg.strictest(r["verdict"] for r in v),
        "event_ids": [r["event_id"] for r in v],
    } for k, v in buckets.items()]
    # gaps-first: most-denied then most-held bubble up
    groups.sort(key=lambda g: (-g["deny"], -g["hold"], -g["count"], g["key"]))
    return {
        "version": SCHEMA_VERSION, "grouped_by": group_by, "limits": LIMITS,
        "summary": {
            "total": len(rows),
            "admitted": vc[_cg.ALLOW], "held": vc[_cg.HOLD], "rejected": vc[_cg.DENY],
            "released": n_released,
            "holds_pending": max(0, vc[_cg.HOLD] - n_released),   # live: held minus human-released
            "sources": len({r["source"] for r in rows}),
            "top_rules": rule_c.most_common(5),
        },
        "groups": groups, "rows": rows,
    }


def from_log(folder: str, *, log_root: Optional[str] = None, group_by: str = "verdict") -> dict[str, Any]:
    """Read the folder's signed chain and project its security events — the live dashboard.
    Read-only: it replays the log, never appends."""
    from .memory import WorkspaceMemory
    mem = WorkspaceMemory(folder, log_root=log_root, actor="user")
    events = [dict(getattr(e, "extra", None) or {}, event_id=getattr(e, "pair_id", ""))
              for e in mem._own_log.replay()
              if (getattr(e, "extra", None) or {}).get("kind") in SECURITY_KINDS]
    return project(events, group_by=group_by)
