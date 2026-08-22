#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Deterministic, read-only collector for the audit-the-ai board.

The skill (``SKILL.md``) narrates the audit board in chat; this script is the
thin runner behind that narration. It collects the manifest-complete skeleton
so the agent does not have to call ~20 ops by hand and get the shape right
every time. It never narrates, never proposes, never applies.

Two versions on one skeleton, exactly as the skill's cascade describes:

  - ``rvnd`` not importable            -> BLIND.  No engine row can be read.
  - ``rvnd`` importable, folder not
    routed through a live board        -> IDLE.   The watchtower is built and
                                                    switched off; still not
                                                    "with RVND".
  - ``rvnd`` importable, folder routed -> LIVE.   The full board, from the
                                                    signed record.

Read-only, hard-enforced: every call to the engine goes through ``_call()``,
which checks the ``(tool, op)`` pair against ``ALLOWED_OPS`` -- a literal
mirror of ``manifest.yaml``'s ``rvnd.tools[*].ops`` lists -- and refuses
anything not on that list. Two ops the console's audit panel exposes are
excluded from that list on purpose: ``workspace_audit`` ``verify_chain`` and
``discipline`` both write to the folder's signed chain on every invocation
(confirmed by reading ``audit_verify_chain``'s and ``run_discipline``'s own
source, and by observing the chain length grow across two live calls against
a scratch folder). Calling either from a pure-read runner would contradict
the skill's own "appends nothing" claim, so the manifest omits both and
``_call()`` refuses them explicitly (``KNOWN_SELF_RECORDING``) as a second
guard. ``workspace_lock`` ``audit_query`` (self-records) and ``workspace_erase``
mutating ops are excluded the same way the manifest already documents.

Manifest-complete: the coverage contract is ``app/src/panels/pack.json`` (22
declared panels). One row is asserted for every declared id; a gap fails
loud (non-zero exit), mirroring the console's own fail-closed panel-boot
check rather than silently shipping an incomplete board.

Honesty rules enforced structurally, not just documented:

  - blank-is-never-clean: a panel with no live signal renders ``blind`` or
    ``idle`` plus a reason -- never ``0``, never "no violations". ``fields``
    is only populated on rows the runner actually executed (``idle``/``live``
    engine state); on ``blind``/``host`` rows it stays ``null``.
  - omit-don't-fake: a field this runner did not read is absent from the
    output, never invented or defaulted to zero.
  - fail-closed: an op call that errors is recorded as an error on that row,
    never silently treated as "no findings".

Usage::

    WORKSPACES_ALLOW_UNREGISTERED=1 python3 render_board.py <folder> [--json|--text]

No network. No writes to disk beyond stdout. Exits non-zero on a manifest
gap or an unreadable coverage contract.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[5]  # .../plugin/rvnd/skills/audit-the-ai/scripts/render_board.py
PACK_JSON = REPO_ROOT / "app" / "src" / "panels" / "pack.json"


# ---------------------------------------------------------------------------
# The allow-set: a literal mirror of manifest.yaml's rvnd.tools[*].ops lists.
# Every call this runner makes is checked against this table before it is
# made. Nothing outside it is ever invoked, whatever a panel spec below asks
# for -- a bug in a panel spec fails the call, not the boundary.
# ---------------------------------------------------------------------------
ALLOWED_OPS: dict[str, set[str]] = {
    "workspace_workflow": {
        "governance_live", "governance_register", "egress_board", "coverage_matrix",
        "governance_graph", "governance_map", "governance_kg", "connector_list",
        "lane_capabilities", "list", "active", "queue", "inspect_stuck",
        "transport_audit", "approval_list",
    },
    "workspace_audit": {
        "tail", "shadow_scan", "overrides", "override_recurrence", "calibration",
    },
    "workspace_policy": {"snapshot", "party_list", "juris_packs"},
    "workspace_matrix": {"show"},
    "workspace_lock": {"threshold_get", "setup_status"},
    "workspace_conformity": {
        "evidence_pack", "oversight_attestation", "trigger_map",
        "drift_report", "risk_register", "threat_model",
    },
    "workspace_grounder": {"coverage", "bibliography", "swarm.frontier", "oversight.feed"},
    "workspace_lens": {"log", "precedent_list"},
    "workspace_contract": {"list_reviews", "state", "obligations", "list_approvals"},
    "workspace_model": {"list", "status", "attest_status"},
    "workspace_capture": {"read"},
    "workspace_memory": {"recent"},
    "workspace_mirror": {"list"},
    "workspace_dispatch": {"decision_pending", "list_pinned", "recent"},
    "workspace_legal": {"card.list"},
    "workspace_erase": {"status"},
    "workspace_session": {"verify_bytes"},
}

# Ops excluded from the manifest allow-set above because reading the actual
# implementation (and observing it live) showed they mutate the signed chain
# on every call. Named here so _call() can refuse them with a specific reason
# even if a future edit re-adds them to the manifest -- a second guard that
# keeps this runner pure-read regardless. See the module docstring.
KNOWN_SELF_RECORDING = {
    ("workspace_audit", "verify_chain"): (
        "audit_verify_chain() self-logs a 'system' event with "
        "extra.kind='verify_chain_read' to the folder's mutation log on "
        "every call (its own docstring names this 'Audit-of-audit (D8)'); "
        "observed live: the folder's chain length grew by one after a "
        "single verify_chain call against a scratch folder."
    ),
    ("workspace_audit", "discipline"): (
        "discipline_audit() runs run_discipline(..., write_audit=True) "
        "with no way to pass write_audit=False through the facade op; "
        "observed live: the folder's chain length grew by one after a "
        "single discipline call against a scratch folder."
    ),
}


class ReadOnlyViolation(RuntimeError):
    """Raised when a panel spec asks this runner to call an op outside the
    manifest allow-set, or one of the two known self-recording ops. Never
    caught anywhere in this module -- an attempt to exceed the boundary
    is a bug in this file, not a per-row failure to degrade gracefully."""


def _call(mcp_server: Any, tool: str, op: str, params: dict[str, Any]) -> dict[str, Any]:
    """The one gate every engine call passes through. Refuses anything not
    literally listed in ALLOWED_OPS, and anything in KNOWN_SELF_RECORDING
    even though it is listed there -- read-only is enforced here, once,
    structurally, not left to each panel builder to remember."""
    if (tool, op) in KNOWN_SELF_RECORDING:
        raise ReadOnlyViolation(
            f"{tool}(op={op!r}) self-records on every call and is excluded from "
            f"this pure-read skill: {KNOWN_SELF_RECORDING[(tool, op)]}"
        )
    allowed = ALLOWED_OPS.get(tool)
    if allowed is None or op not in allowed:
        raise ReadOnlyViolation(
            f"{tool}(op={op!r}) is not in the manifest allow-set for this skill "
            f"(manifest.yaml rvnd.tools); refusing to call it"
        )
    fn = getattr(mcp_server, tool)
    return fn(op, params)


# ---------------------------------------------------------------------------
# Engine detection
# ---------------------------------------------------------------------------

def _detect_engine() -> tuple[Any, str]:
    """Try to import the engine facade. Returns (module_or_None, reason)."""
    try:
        from rvnd import mcp_server  # noqa: PLC0415 -- intentionally lazy/optional
    except Exception as exc:  # noqa: BLE001 -- any import failure means BLIND
        return None, f"{type(exc).__name__}: {exc}"
    return mcp_server, ""


def _compute_engine_state(mcp_server: Any, folder: str) -> tuple[str, dict[str, Any], str]:
    """One governance_live read decides idle vs live for the whole board,
    per the skill's own cascade rule. Returns (state, raw_response, reason).
    state is 'idle' or 'live' -- 'blind' is decided earlier, at import time,
    never here."""
    try:
        resp = _call(mcp_server, "workspace_workflow", "governance_live",
                     {"folder_context": folder})
    except ReadOnlyViolation:
        raise
    except Exception as exc:  # noqa: BLE001 -- a broken read is still "not live"
        return "idle", {}, f"governance_live raised {type(exc).__name__}: {exc}"
    if not isinstance(resp, dict):
        return "idle", {}, "governance_live returned a non-dict response"
    if resp.get("error"):
        return "idle", resp, f"governance_live error: {resp['error']}"
    chain = resp.get("chain") or []
    sessions = resp.get("sessions") or []
    leases = resp.get("leases") or []
    certificates = resp.get("certificates") or []
    if chain or sessions or leases or certificates:
        return "live", resp, ""
    return ("idle", resp,
            "engine present; governance_live shows no sessions, leases, chain "
            "entries or certificates for this folder -- built and switched off")


# ---------------------------------------------------------------------------
# Receipt extraction (best-effort, never fabricated)
# ---------------------------------------------------------------------------

_RECEIPT_KEYS = ("audit_id", "seq", "hash", "prev_hash", "snapshot", "digest")


def _extract_receipts(result: Any) -> dict[str, Any]:
    """Shallow, best-effort scan for the receipt fields the skill's honesty
    rule asks a filled row to carry (chain seq/hash, versum snapshot digest,
    audit_id). Only ever reads keys already present in the op's own
    response; never invents one. Not exhaustive -- a deep walk of every
    nested shape is out of scope for a thin runner."""
    found: dict[str, Any] = {}
    if not isinstance(result, dict):
        return found
    for key in _RECEIPT_KEYS:
        if key in result and result[key] not in (None, "", []):
            found[key] = result[key]
    chain = result.get("chain")
    if isinstance(chain, list) and chain:
        last = chain[-1]
        if isinstance(last, dict):
            for key in ("seq", "hash", "prev_hash"):
                if key in last:
                    found.setdefault(f"chain_last_{key}", last[key])
    return found


# ---------------------------------------------------------------------------
# Panel machinery
# ---------------------------------------------------------------------------

class PanelResult:
    __slots__ = ("panel_id", "title", "dimension", "state", "op_calls",
                 "fields", "reason_if_empty")

    def __init__(self, panel_id: str, title: str, dimension: str):
        self.panel_id = panel_id
        self.title = title
        self.dimension = dimension
        self.state = "blind"
        self.op_calls: list[dict[str, Any]] = []
        self.fields: dict[str, Any] | None = None
        self.reason_if_empty: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "panel_id": self.panel_id,
            "title": self.title,
            "dimension": self.dimension,
            "state": self.state,
            "op_calls": self.op_calls,
            "fields": self.fields,
            "reason_if_empty": self.reason_if_empty,
        }


# Each spec: (panel_id, title, dimension, [(tool, op, extra_params), ...]).
# extra_params is merged over {"folder_context": folder}; pass {} to accept
# the default. Every (tool, op) here must be in ALLOWED_OPS and must not be
# in KNOWN_SELF_RECORDING, or the run fails loud via ReadOnlyViolation.
def _panel_specs(folder: str, now_epoch: int) -> list[tuple[str, str, str, list[tuple[str, str, dict]]]]:
    return [
        ("ai", "AI & Capture", "#1 AI system + model card + capture", [
            ("workspace_model", "list", {}),
            ("workspace_model", "status", {"probe_endpoint": False}),
            ("workspace_capture", "read", {}),
            ("workspace_dispatch", "list_pinned", {}),
            ("workspace_dispatch", "recent", {}),
        ]),
        ("govlive", "Live governance", "spine (#2/#8/#9/#13/#14)", [
            ("workspace_workflow", "governance_live", {}),
            ("workspace_audit", "tail", {"limit": 30}),
            ("workspace_workflow", "approval_list", {"now": now_epoch}),
            # lane_capabilities is per-agent (Task B); resolved separately in
            # _build_govlive() once the roster is known, not listed here.
        ]),
        ("data", "Local data", "#4 local knowledge", [
            ("workspace_memory", "recent", {}),
            ("workspace_mirror", "list", {}),
        ]),
        ("grounder", "Sources & gaps", "#4 attribution", [
            ("workspace_grounder", "coverage", {}),
            ("workspace_grounder", "bibliography", {}),
            ("workspace_grounder", "swarm.frontier", {}),
            ("workspace_grounder", "oversight.feed", {}),
        ]),
        ("coverage", "Coverage", "#17 capability matrix (coverage)", [
            ("workspace_workflow", "governance_graph", {}),
            ("workspace_workflow", "coverage_matrix", {}),
        ]),
        ("decision", "Decision", "#6 reasoning/decision", [
            ("workspace_dispatch", "decision_pending", {}),
        ]),
        ("legal", "Standing facts", "legal standing facts (own row)", [
            ("workspace_legal", "card.list", {}),
        ]),
        ("obligations", "Obligations", "#10 obligations", [
            ("workspace_contract", "obligations", {}),
        ]),
        ("egress", "Egress board", "#10 egress", [
            ("workspace_workflow", "egress_board", {}),
        ]),
        ("lock", "Privacy Lock", "#5 PII / privacy lock", [
            ("workspace_lock", "threshold_get", {}),
            ("workspace_lock", "setup_status", {}),
        ]),
        # "erasure" has no listed panel-spec entry: workspace_erase(status)
        # requires a specific request_id (no board-level listing op exists in
        # the manifest allow-set). See _build_erasure().
        ("protections", "Policy", "#9 oversight dial + posture", [
            ("workspace_policy", "snapshot", {}),
            ("workspace_policy", "juris_packs", {}),
            ("workspace_policy", "party_list", {}),
        ]),
        ("conformity", "Conformity", "#12 compliance/conformity", [
            ("workspace_conformity", "evidence_pack", {}),
            ("workspace_conformity", "oversight_attestation", {}),
            ("workspace_conformity", "trigger_map", {}),
            ("workspace_conformity", "drift_report", {}),
            ("workspace_conformity", "risk_register", {}),
            ("workspace_conformity", "threat_model", {}),
        ]),
        ("audit", "Audit trail", "#14 proof / signed record", [
            # verify_chain and discipline are deliberately excluded --
            # see KNOWN_SELF_RECORDING and the module docstring.
            ("workspace_audit", "shadow_scan", {}),
            ("workspace_audit", "overrides", {}),
            ("workspace_audit", "override_recurrence", {}),
            ("workspace_audit", "calibration", {}),
            ("workspace_model", "attest_status", {}),
        ]),
        ("approvals", "Sign-offs", "#9 human oversight", [
            ("workspace_contract", "list_approvals", {}),
            ("workspace_workflow", "approval_list", {"now": now_epoch}),
        ]),
        ("roles", "Roles & competence", "#16 parties", [
            ("workspace_policy", "party_list", {}),
        ]),
        ("workflow", "Run board", "#2/#3 runs & fleet", [
            ("workspace_workflow", "list", {}),
            ("workspace_workflow", "active", {}),
            ("workspace_workflow", "queue", {}),
            ("workspace_workflow", "inspect_stuck", {}),
            ("workspace_workflow", "transport_audit", {}),
        ]),
        ("contract", "Contract execution", "#7 contract", [
            ("workspace_contract", "list_reviews", {}),
            ("workspace_contract", "state", {}),
        ]),
        ("federation", "Connected tools", "federation (own row)", [
            ("workspace_workflow", "connector_list", {}),
        ]),
        ("lens", "Spend & limits", "#11 cost/budget + precedents", [
            ("workspace_lens", "log", {}),
            ("workspace_lens", "precedent_list", {}),
        ]),
        # "bringin" has no listed panel-spec entry: every workspace_ingest op
        # mutates (path/url/skill/stem/assemble_work); the manifest carries
        # no pure-read op for this panel. See _build_bringin().
        ("map", "Policy map", "#7 rules map", [
            ("workspace_workflow", "governance_map", {}),
        ]),
    ]


def _run_ops(mcp_server: Any, folder: str, ops: list[tuple[str, str, dict]],
             op_calls: list[dict[str, Any]], errors: list[str],
             fields: dict[str, Any]) -> None:
    """Execute one panel's op list against the live engine, recording every
    attempted call (so a reader can see exactly what evidence was sought)
    and every result or error (so a failure never disappears as a silent
    empty field)."""
    for tool, op, extra in ops:
        params = {"folder_context": folder, **extra}
        op_calls.append({"tool": tool, "op": op, "params": params})
        try:
            result = _call(mcp_server, tool, op, params)
        except ReadOnlyViolation:
            raise
        except Exception as exc:  # noqa: BLE001 -- fail-closed per row, not per run
            errors.append(f"{tool}({op}): {type(exc).__name__}: {exc}")
            continue
        if isinstance(result, dict) and result.get("error") and "ok" not in result:
            errors.append(f"{tool}({op}): {result['error']}")
            continue
        key = op.replace(".", "_")
        fields[key] = result
        receipts = _extract_receipts(result)
        if receipts:
            fields.setdefault("_receipts", {})[key] = receipts


def _declare_only(panel_id: str, title: str, dimension: str, ops: list[tuple[str, str, dict]],
                   reason: str) -> PanelResult:
    """A panel with no callable pure-read op available at all: declare what
    the console's own op would be, without ever attempting it, so the gap is
    visible rather than silently missing."""
    row = PanelResult(panel_id, title, dimension)
    row.op_calls = [{"tool": t, "op": o, "params": None, "skipped": True} for t, o, _ in ops]
    row.state = "blind"
    row.fields = None
    row.reason_if_empty = reason
    return row


def _build_erasure() -> PanelResult:
    return _declare_only(
        "erasure", "Erasure", "#5 erasure / GDPR tombstones",
        [("workspace_erase", "status", {})],
        "workspace_erase(status) requires a specific request_id (no default; "
        "verified against erase_status()'s signature and a live call that "
        "refused with \"missing params: ['request_id']\"). The manifest "
        "allow-set carries no listing op for erasure, so this row cannot be "
        "filled at board level -- only a per-request query could fill it.",
    )


def _build_bringin() -> PanelResult:
    return _declare_only(
        "bringin", "Bring-in", "bring-in (own row)",
        [("workspace_ingest", "path", {}), ("workspace_ingest", "url", {}),
         ("workspace_ingest", "skill", {})],
        "every workspace_ingest op mutates (path/url/skill/stem/"
        "assemble_work ingest source material); the manifest's own comment "
        "on this facade says so ('all ops mutate -- declare available "
        "inputs only'). 'Available inputs' is host-declarable by the "
        "narrating agent from its own session context; this offline, "
        "session-less runner has none to declare.",
    )


def _build_govlive(mcp_server: Any | None, engine_state: str, folder: str,
                    now_epoch: int, specs_by_id: dict) -> PanelResult:
    panel_id, title, dimension, ops = specs_by_id["govlive"]
    row = PanelResult(panel_id, title, dimension)
    if mcp_server is None:
        row.op_calls = [{"tool": t, "op": o, "params": None, "skipped": True} for t, o, _ in ops]
        row.state = "blind"
        row.reason_if_empty = "rvnd not importable"
        return row
    fields: dict[str, Any] = {}
    errors: list[str] = []
    _run_ops(mcp_server, folder, ops, row.op_calls, errors, fields)
    # Task B: lane_capabilities is the per-agent #17 surface. It needs an
    # actor id the board does not otherwise have, so the roster is read
    # first (workspace_policy party_list, already an allowed op) and each
    # agent party is queried in turn. Bounded so one folder with a very
    # large roster cannot make the board unbounded.
    try:
        roster = _call(mcp_server, "workspace_policy", "party_list",
                        {"folder_context": folder, "kind": "agent"})
    except ReadOnlyViolation:
        raise
    except Exception as exc:  # noqa: BLE001
        roster = None
        errors.append(f"workspace_policy(party_list) for lane_capabilities roster: "
                       f"{type(exc).__name__}: {exc}")
    agent_ids: list[str] = []
    if isinstance(roster, dict):
        for party in (roster.get("parties") or [])[:25]:
            pid = party.get("party_id") if isinstance(party, dict) else None
            if pid:
                agent_ids.append(pid)
    lane_caps: dict[str, Any] = {}
    for actor in agent_ids:
        params = {"folder_context": folder, "actor": actor}
        row.op_calls.append({"tool": "workspace_workflow", "op": "lane_capabilities",
                              "params": params})
        try:
            lane_caps[actor] = _call(mcp_server, "workspace_workflow",
                                      "lane_capabilities", params)
        except ReadOnlyViolation:
            raise
        except Exception as exc:  # noqa: BLE001
            errors.append(f"workspace_workflow(lane_capabilities, actor={actor}): "
                           f"{type(exc).__name__}: {exc}")
    if not agent_ids:
        row.op_calls.append({
            "tool": "workspace_workflow", "op": "lane_capabilities",
            "params": None, "skipped": True,
        })
        fields.setdefault("_notes", []).append(
            "lane_capabilities (Task B's per-agent #17 surface) needs a "
            "registered agent party_id; workspace_policy(party_list, "
            "kind='agent') returned none for this folder, so no per-agent "
            "capability rows could be read."
        )
    else:
        fields["lane_capabilities"] = lane_caps
    if errors:
        fields.setdefault("_errors", []).extend(errors)
    row.state = engine_state
    row.fields = fields if fields else None
    if not fields:
        row.reason_if_empty = "engine idle for this folder" if engine_state == "idle" else ""
    return row


def build_board(folder: str) -> list[PanelResult]:
    """Build the full 22-row board. Raises ReadOnlyViolation if a panel spec
    tries to exceed the allow-set (a bug in this file, never suppressed)."""
    now_epoch = int(time.time())
    mcp_server, blind_reason = _detect_engine()
    engine_state = "blind"
    live_reason = blind_reason
    if mcp_server is not None:
        engine_state, _live_resp, live_reason = _compute_engine_state(mcp_server, folder)

    specs = _panel_specs(folder, now_epoch)
    specs_by_id = {pid: (pid, title, dim, ops) for pid, title, dim, ops in specs}

    rows: list[PanelResult] = []
    for panel_id, title, dimension, ops in specs:
        if panel_id == "govlive":
            rows.append(_build_govlive(mcp_server, engine_state, folder, now_epoch, specs_by_id))
            continue
        row = PanelResult(panel_id, title, dimension)
        if mcp_server is None:
            row.op_calls = [{"tool": t, "op": o, "params": None, "skipped": True} for t, o, _ in ops]
            row.state = "blind"
            row.reason_if_empty = f"rvnd not importable ({blind_reason})"
            rows.append(row)
            continue
        fields: dict[str, Any] = {}
        errors: list[str] = []
        _run_ops(mcp_server, folder, ops, row.op_calls, errors, fields)
        if errors:
            fields.setdefault("_errors", []).extend(errors)
        row.state = engine_state
        row.fields = fields if fields else None
        if not fields:
            row.reason_if_empty = live_reason or (
                "engine idle for this folder" if engine_state == "idle" else "")
        rows.append(row)

    rows.append(_build_erasure())
    rows.append(_build_bringin())

    if mcp_server is None:
        for row in rows:
            if row.panel_id in ("ai", "data", "grounder", "lens"):
                row.state = "host"
                row.reason_if_empty = (
                    "host-declarable only by the narrating agent (its own model "
                    "identity, tools available, coarse local-source count, or "
                    "declared token spend) -- this offline, session-less runner "
                    "has no such context to read; every engine-backed sub-field "
                    f"stays blind because rvnd is not importable ({blind_reason})"
                )

    return rows


# ---------------------------------------------------------------------------
# Manifest-completeness
# ---------------------------------------------------------------------------

def _load_declared_panel_ids() -> list[str]:
    if not PACK_JSON.exists():
        print(f"error: coverage contract not found: {PACK_JSON}", file=sys.stderr)
        raise SystemExit(3)
    try:
        pack = json.loads(PACK_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: coverage contract unreadable: {PACK_JSON}: {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
    panels = pack.get("panels")
    if not isinstance(panels, list) or not panels:
        print(f"error: coverage contract has no panels: {PACK_JSON}", file=sys.stderr)
        raise SystemExit(3)
    ids = []
    for p in panels:
        pid = p.get("id") if isinstance(p, dict) else None
        if not pid:
            print(f"error: coverage contract has a panel with no id: {p!r}", file=sys.stderr)
            raise SystemExit(3)
        ids.append(pid)
    return ids


def _assert_manifest_complete(rows: list[PanelResult]) -> None:
    declared = _load_declared_panel_ids()
    rendered = [r.panel_id for r in rows]
    declared_set, rendered_set = set(declared), set(rendered)
    missing = [pid for pid in declared if pid not in rendered_set]
    extra = [pid for pid in rendered if pid not in declared_set]
    dupes = sorted({pid for pid in rendered if rendered.count(pid) > 1})
    if missing or extra or dupes:
        print("error: audit board is not manifest-complete against "
              f"{PACK_JSON}", file=sys.stderr)
        if missing:
            print(f"  missing panel row(s): {missing}", file=sys.stderr)
        if extra:
            print(f"  panel row(s) not declared in pack.json: {extra}", file=sys.stderr)
        if dupes:
            print(f"  duplicate panel row(s): {dupes}", file=sys.stderr)
        raise SystemExit(4)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_json(rows: list[PanelResult], folder: str) -> str:
    doc = {
        "schema": "rvnd.audit-the-ai.board/v1",
        "folder_context": folder,
        "panel_count": len(rows),
        "rows": [r.to_dict() for r in rows],
    }
    return json.dumps(doc, indent=2, sort_keys=False, default=str)


def render_text(rows: list[PanelResult], folder: str) -> str:
    lines = [f"audit-the-ai board -- folder: {folder}", f"{len(rows)} panel rows", ""]
    for r in rows:
        lines.append(f"[{r.state:5s}] {r.panel_id:12s} {r.title}  ({r.dimension})")
        if r.reason_if_empty:
            lines.append(f"         reason: {r.reason_if_empty}")
        if r.fields:
            keys = [k for k in r.fields if not k.startswith("_")]
            lines.append(f"         fields: {', '.join(keys) or '(none)'}")
            if r.fields.get("_errors"):
                for e in r.fields["_errors"]:
                    lines.append(f"         ! {e}")
        lines.append(f"         op_calls: {len(r.op_calls)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect the audit-the-ai board: pure-read, manifest-complete, "
                    "fail-closed. Prints to stdout only.")
    parser.add_argument("folder", help="folder_context to audit")
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_const", dest="fmt", const="json")
    fmt.add_argument("--text", action="store_const", dest="fmt", const="text")
    parser.set_defaults(fmt="json")
    args = parser.parse_args(argv)

    try:
        rows = build_board(args.folder)
    except ReadOnlyViolation as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _assert_manifest_complete(rows)

    if args.fmt == "text":
        print(render_text(rows, args.folder))
    else:
        print(render_json(rows, args.folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
