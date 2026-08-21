# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the retrievable-audit-chain MCP work (2026-05-22).

Surfaces ``audit_id`` (already on ``LogEvent``) in the wire shape of
``recent_dispatches``, ``active_workflows``, ``dispatch_skill``, and
``record_dispatch``; adds ``get_audit_event(event_id, folder_context)``
MCP tool that resolves an id back to the full event.

The multi-skill-builder feedback called this gap "the most urgent" —
compliance plugins must include audit IDs in Canonical Output § [6]
Audit Trail; without retrievable IDs the deliverable can only say
"it's in the mutation log somewhere" which is not audit-grade.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path


from rvnd.mutation_log import LogEvent, MutationLog
from rvnd.pinned_skills import pin_skill, record_dispatch
from rvnd.workflows import (
    Workflow,
    WorkflowStep,
    active_workflows,
    define_workflow,
    recent_dispatches,
    run_workflow,
)


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ---------------------------------------------------------------------------
# audit_id surfacing
# ---------------------------------------------------------------------------


def test_record_dispatch_returns_audit_id(tmp_path):
    """``record_dispatch`` returns the audit_id of the event it just wrote."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    out = record_dispatch(
        folder, "p:s", query="q", chosen_via="test", log_root=log_root,
    )
    assert "audit_id" in out, out
    assert UUID_RE.match(out["audit_id"]), out["audit_id"]


def test_recent_dispatches_carries_audit_id(tmp_path):
    """Every event row from recent_dispatches has a non-empty audit_id."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    record_dispatch(folder, "p:a", chosen_via="t", log_root=log_root)
    record_dispatch(folder, "p:b", chosen_via="t", log_root=log_root)
    events = recent_dispatches(folder, log_root=log_root)
    assert len(events) == 2
    for e in events:
        assert "audit_id" in e
        assert UUID_RE.match(e["audit_id"]), e


def test_workflow_event_rows_carry_audit_id(tmp_path):
    """workflow-event rows (not only skill-dispatch) carry audit_id."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    wf = Workflow(name="flow", steps=[WorkflowStep(skill_id="p:a")])
    define_workflow(folder, wf, log_root=log_root)
    run_workflow(folder, "flow",
                 dispatcher=lambda **kw: {"ok": True}, log_root=log_root)
    events = recent_dispatches(folder, log_root=log_root)
    wf_events = [e for e in events if e["kind"] == "workflow-event"]
    assert wf_events, events
    for e in wf_events:
        assert UUID_RE.match(e["audit_id"]), e


def test_active_workflows_carries_audit_id(tmp_path):
    """active_workflows (HOTL panel feed) also carries audit_id."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    log = MutationLog(folder, log_root=log_root)
    log.append(LogEvent(
        event="system", folder_path=str(folder), pair_id="workflow-event",
        actor="t",
        extra={"run_id": "wfrun:x", "workflow": "f", "step_index": -1,
               "state": "running", "skill_id": ""},
    ))
    out = active_workflows(folder, log_root=log_root)
    assert out and UUID_RE.match(out[0]["audit_id"])


# ---------------------------------------------------------------------------
# get_audit_event MCP tool
# ---------------------------------------------------------------------------


def _fresh_mcp(monkeypatch, log_root: Path):
    """Reload mcp_server and patch _log_root to the test log."""
    import rvnd.mcp_server as srv
    importlib.reload(srv)
    monkeypatch.setattr("rvnd.mcp_serving._log_root", lambda: log_root)
    return srv


def test_dispatch_skill_returns_audit_id(tmp_path, monkeypatch):
    """The MCP ``dispatch_skill`` wrapper surfaces audit_id."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    pin_skill(str(folder), "p:something", log_root=log_root)
    out = srv.dispatch_skill(
        folder_context=str(folder),
        skill_id="p:something",
        query="x",
    )
    assert out["ok"] is True, out
    assert "audit_id" in out
    assert UUID_RE.match(out["audit_id"])


def test_get_audit_event_resolves_dispatch_event(tmp_path, monkeypatch):
    """End-to-end: dispatch a workspace skill, take its audit_id, look it up."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    disp = srv.dispatch_skill(
        folder_context=str(folder),
        skill_id="workspace:workspace-policy",  # bypass works without pin
        query="show",
    )
    assert disp["ok"] is True, disp
    aid = disp["audit_id"]
    assert UUID_RE.match(aid)

    out = srv.get_audit_event(event_id=aid, folder_context=str(folder))
    assert out["ok"] is True, out
    e = out["event"]
    assert e["audit_id"] == aid
    assert e["pair_id"] == "skill-dispatch"
    assert e["extra"]["skill_id"] == "workspace:workspace-policy"
    assert e["extra"]["chosen_via"] == "user"
    assert e["timestamp"]  # ISO 8601 string, populated


def test_get_audit_event_unknown_id_returns_not_found(tmp_path, monkeypatch):
    """Unknown event_id surfaces a clean error, not a stack trace."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.get_audit_event(
        event_id="00000000-0000-0000-0000-000000000000",
        folder_context=str(folder),
    )
    assert out["ok"] is False
    assert "not found" in out["error"].lower()


def test_get_audit_event_requires_event_id(tmp_path, monkeypatch):
    """Empty event_id is rejected with a clean error."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.get_audit_event(event_id="", folder_context=str(folder))
    assert out["ok"] is False
    assert "event_id is required" in out["error"]


def test_get_audit_event_discovery_scan(tmp_path, monkeypatch):
    """When folder_context is omitted, scan known workspaces and find it."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    # Register the folder so the discovery scan can see it
    from rvnd.registry import add_known_workspace
    add_known_workspace(str(folder), log_root=log_root)

    disp = srv.dispatch_skill(
        folder_context=str(folder),
        skill_id="workspace:workspace-workspace",
        query="x",
    )
    aid = disp["audit_id"]
    # Look up WITHOUT folder_context — must still find via scan
    out = srv.get_audit_event(event_id=aid)
    assert out["ok"] is True, out
    assert out["event"]["audit_id"] == aid
    assert out["found_in"] == str(folder)


def test_get_audit_event_workflow_event_chain(tmp_path, monkeypatch):
    """run_workflow writes workflow-event rows; their audit_ids resolve too."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    srv.define_workflow(
        folder_context=str(folder),
        name="probe",
        steps=[{"skill_id": "workspace:workspace-policy", "query": "p",
                "on_failure": "stop"}],
    )
    srv.run_workflow(folder_context=str(folder), name="probe")
    events = srv.recent_dispatches(folder_context=str(folder))["events"]
    wf_audit_ids = [e["audit_id"] for e in events
                     if e["kind"] == "workflow-event"]
    assert wf_audit_ids, events
    # Pick one and look it up
    out = srv.get_audit_event(event_id=wf_audit_ids[0],
                              folder_context=str(folder))
    assert out["ok"] is True, out
    assert out["event"]["pair_id"] == "workflow-event"
    assert out["event"]["extra"]["workflow"] == "probe"


# ---------------------------------------------------------------------------
# server_info registration
# ---------------------------------------------------------------------------


def test_get_audit_event_listed_in_server_info(tmp_path, monkeypatch):
    """0.6.6+: get_audit_event lives behind the workspace_audit facade; server_info
    advertises the facade and the op stays reachable through it."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    info = srv.server_info()
    assert "workspace_audit" in info["tools"], info["tools"]
    ops = {o["op"] for o in srv.workspace_audit("help")["ops"]}
    assert "get_event" in ops, ops
