# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the three bugs surfaced by the WORKSPACES_v0.6.3 test-plan run
on 2026-05-21:

1. Workspace's own skills (``workspace:*``) must dispatch via the MCP
   ``dispatch_skill`` wrapper without requiring an explicit pin. The Workspace
   plugin's skills are part of the system's functional surface — pinning
   them is meaningless because installing the plugin makes them available
   the same way any normal Claude / Cursor skill is.
2. Audit-event timestamps (``skill-dispatch`` and ``workflow-event`` rows
   from ``recent_dispatches`` / ``active_workflows``) must be non-empty
   ISO 8601 UTC strings. Bug was: ``getattr(e, "timestamp", "")`` where
   the actual ``LogEvent`` field is ``ts: float``.
3. ``recent_dispatches`` must sort newest-first; falls out of (2) once
   timestamps are populated.
"""
from __future__ import annotations

import re
import time
from pathlib import Path


from workspaces.mutation_log import LogEvent, MutationLog
from workspaces.workflows import (
    _event_ts_iso,
    _events_for_folder,
    active_workflows,
    define_workflow,
    recent_dispatches,
    run_workflow,
)


# ---------------------------------------------------------------------------
# Bug 2 + 3 — audit timestamps & DESC ordering
# ---------------------------------------------------------------------------


ISO_8601_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_event_ts_iso_renders_real_timestamp():
    """``_event_ts_iso`` returns an ISO 8601 UTC string for a real ``ts``."""
    e = LogEvent(
        event="system",
        folder_path="/tmp/x",
        pair_id="skill-dispatch",
        actor="test",
        extra={"skill_id": "p:s", "query": "", "chosen_via": "test"},
    )
    out = _event_ts_iso(e)
    assert ISO_8601_UTC_RE.match(out), f"not ISO-8601 UTC: {out!r}"


def test_event_ts_iso_handles_missing_or_zero_ts():
    """Defensive: zero / missing ``ts`` returns empty string, never raises."""

    class _Fake:
        ts = 0

    assert _event_ts_iso(_Fake()) == ""
    assert _event_ts_iso(object()) == ""


def _seed_dispatches(folder: Path, log_root: Path, n: int) -> None:
    """Write ``n`` skill-dispatch events at known increasing times."""
    log = MutationLog(folder, log_root=log_root)
    for i in range(n):
        e = LogEvent(
            event="system",
            folder_path=str(folder),
            pair_id="skill-dispatch",
            actor="test",
            extra={
                "skill_id":   f"p:s{i}",
                "query":      "",
                "chosen_via": "test",
            },
        )
        # Force a strictly-increasing timestamp so the sort is unambiguous
        # and not vulnerable to clock granularity.
        e.ts = time.time() + i
        log.append(e)


def test_recent_dispatches_timestamps_are_non_empty_iso(tmp_path):
    """Every event returned by recent_dispatches has a non-empty ISO ts."""
    folder = tmp_path / "wks"
    folder.mkdir()
    log_root = tmp_path / "log"
    _seed_dispatches(folder, log_root, 3)

    events = recent_dispatches(str(folder), log_root=log_root)
    assert len(events) == 3, events
    for e in events:
        assert e["timestamp"], f"empty timestamp: {e!r}"
        assert ISO_8601_UTC_RE.match(e["timestamp"]), \
            f"bad ISO: {e['timestamp']!r}"


def test_recent_dispatches_sorts_desc_newest_first(tmp_path):
    """Newest-first ordering — the bug-3 fix."""
    folder = tmp_path / "wks"
    folder.mkdir()
    log_root = tmp_path / "log"
    _seed_dispatches(folder, log_root, 5)

    events = recent_dispatches(str(folder), log_root=log_root)
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps, reverse=True), timestamps
    # And it's not vacuous: at least two distinct timestamps
    assert len(set(timestamps)) >= 2, timestamps


def test_events_for_folder_skill_dispatch_carries_timestamp(tmp_path):
    """The lower-level reader also carries the timestamp through."""
    folder = tmp_path / "wks"
    folder.mkdir()
    log_root = tmp_path / "log"
    _seed_dispatches(folder, log_root, 1)
    out = _events_for_folder(folder, include_workflows=True, log_root=log_root)
    assert out, out
    assert out[0]["kind"] == "skill-dispatch"
    assert ISO_8601_UTC_RE.match(out[0]["timestamp"])


def test_workflow_event_timestamps_are_populated(tmp_path):
    """workflow-event rows (not just skill-dispatch) carry the timestamp."""
    from workspaces.workflows import Workflow, WorkflowStep
    folder = tmp_path / "wks"
    folder.mkdir()
    log_root = tmp_path / "log"
    wf = Workflow(name="flow", steps=[WorkflowStep(skill_id="p:a")])
    define_workflow(str(folder), wf, log_root=log_root)
    run_workflow(str(folder), "flow",
                 dispatcher=lambda **kw: {"ok": True}, log_root=log_root)

    events = recent_dispatches(str(folder), log_root=log_root)
    wf_events = [e for e in events if e["kind"] == "workflow-event"]
    assert wf_events, events
    for e in wf_events:
        assert e["timestamp"], f"empty workflow-event ts: {e!r}"
        assert ISO_8601_UTC_RE.match(e["timestamp"])


def test_active_workflows_timestamp_populated(tmp_path):
    """active_workflows surface (used by the HOTL panel) must also carry ts."""
    folder = tmp_path / "wks"
    folder.mkdir()
    log_root = tmp_path / "log"
    log = MutationLog(folder, log_root=log_root)
    # Synthesize a run-level workflow-event that is still "running"
    log.append(LogEvent(
        event="system",
        folder_path=str(folder),
        pair_id="workflow-event",
        actor="test",
        extra={"run_id": "wfrun:abc", "workflow": "flow",
               "step_index": -1, "state": "running", "skill_id": ""},
    ))
    out = active_workflows(folder, log_root=log_root)
    assert out, out
    assert out[0]["state"] == "running"
    assert out[0]["timestamp"], f"empty active_workflows ts: {out[0]!r}"
    assert ISO_8601_UTC_RE.match(out[0]["timestamp"])


# ---------------------------------------------------------------------------
# Bug 1 — workspace:* skills dispatch without a pin
# ---------------------------------------------------------------------------


def _fresh_mcp(monkeypatch, log_root: Path):
    """Import workspaces.mcp_server fresh and point _log_root at tmp_path."""
    import importlib

    import workspaces.mcp_server as srv
    importlib.reload(srv)
    monkeypatch.setattr("workspaces.mcp_serving._log_root", lambda: log_root)
    return srv


def test_dispatch_skill_rejects_unpinned_external(tmp_path, monkeypatch):
    """Unpinned NON-workspace skill is still rejected (regression guard)."""
    folder = tmp_path / "wks"
    folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.dispatch_skill(
        folder_context=str(folder),
        skill_id="other-plugin:other-skill",
        query="x",
    )
    assert out["ok"] is False
    assert "not in resolved pinned set" in out.get("error", "")
    assert out["in_resolved_set"] is False


def test_dispatch_skill_bypass_for_workspace_skill(tmp_path, monkeypatch):
    """``workspace:workspace-policy`` dispatches without an explicit pin."""
    folder = tmp_path / "wks"
    folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.dispatch_skill(
        folder_context=str(folder),
        skill_id="workspace:workspace-policy",
        query="show policy",
    )
    assert out["ok"] is True, out
    assert out["skill_id"] == "workspace:workspace-policy"
    # in_resolved_set reflects "is it explicitly pinned" — for the system-
    # dispatched workspace skill, it's False, but ok is True. Provenance
    # makes the bypass visible to clients that care.
    assert out["in_resolved_set"] is False
    assert out["provenance"] == "system"


def test_dispatch_skill_workspace_skill_when_pinned_keeps_own_provenance(
    tmp_path, monkeypatch,
):
    """If the user explicitly pins a workspace skill, provenance is 'own'."""
    folder = tmp_path / "wks"
    folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    from workspaces.pinned_skills import pin_skill
    pin_skill(str(folder), "workspace:workspace-policy", log_root=log_root)

    out = srv.dispatch_skill(
        folder_context=str(folder),
        skill_id="workspace:workspace-policy",
        query="show policy",
    )
    assert out["ok"] is True, out
    assert out["in_resolved_set"] is True
    assert out["provenance"] == "own"


def test_run_workflow_can_use_workspace_skills_without_prepin(
    tmp_path, monkeypatch,
):
    """End-to-end: a workflow whose steps reference workspace:* skills runs
    even when nothing is pinned. This pins the scenario that failed before the fix."""
    folder = tmp_path / "wks"
    folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    srv.define_workflow(
        folder_context=str(folder),
        name="intake",
        steps=[
            {"skill_id": "workspace:workspace-policy",   "query": "p", "on_failure": "stop"},
            {"skill_id": "workspace:workspace-workspace", "query": "w", "on_failure": "stop"},
        ],
    )
    out = srv.run_workflow(folder_context=str(folder), name="intake")
    assert out["ok"] is True, out
    assert out["final_state"] == "done"
    assert len(out["steps"]) == 2
    assert all(s["state"] == "done" for s in out["steps"]), out["steps"]
