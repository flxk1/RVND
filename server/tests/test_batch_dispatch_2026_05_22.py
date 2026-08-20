# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for batch dispatch (multi-skill-builder proposal #1, 2026-05-22).

`dispatch_skills_batch` runs N skills against one folder in one MCP call,
sharing a `batch_id` across every constituent audit event. Multi-agent
orchestrators stop paying N+1 round-trips; compliance plugins can group
related dispatches under one id in their Canonical Output.

Semantics under test:
- Best-effort: per-skill failures do not abort the batch.
- Order preserved: results align with input.
- Duplicates allowed: each occurrence gets its own audit event.
- workspace:* bypass works inside the batch (bug-1 fix carries through).
- batch_id is stamped into every constituent event's `extra`.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path


from workspaces.mutation_log import MutationLog
from workspaces.pinned_skills import pin_skill


UUID_RE  = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
BATCH_RE = re.compile(r"^batch:[0-9a-f]{16}$")


def _fresh_mcp(monkeypatch, log_root: Path):
    import workspaces.mcp_server as srv
    importlib.reload(srv)
    monkeypatch.setattr("workspaces.mcp_serving._log_root", lambda: log_root)
    return srv


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_batch_happy_path_three_workspace_skills(tmp_path, monkeypatch):
    """Three workspace:* skills batch-dispatch cleanly with shared batch_id."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    out = srv.dispatch_skills_batch(
        skill_ids=[
            "workspace:workspace-policy",
            "workspace:workspace-workspace",
            "workspace:workspace-capture",
        ],
        folder_context=str(folder),
        query="probe",
    )
    assert out["ok"] is True, out
    assert BATCH_RE.match(out["batch_id"]), out["batch_id"]
    assert out["ok_count"] == 3
    assert out["fail_count"] == 0
    assert len(out["results"]) == 3
    for r in out["results"]:
        assert r["ok"] is True, r
        assert UUID_RE.match(r["audit_id"]), r
        assert r["provenance"] == "system"  # workspace:* bypass


def test_batch_audit_ids_are_distinct(tmp_path, monkeypatch):
    """Each dispatched skill gets its own audit_id even with the same query."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.dispatch_skills_batch(
        skill_ids=["workspace:workspace-policy", "workspace:workspace-workspace"],
        folder_context=str(folder),
        query="x",
    )
    ids = [r["audit_id"] for r in out["results"]]
    assert len(set(ids)) == 2, ids


def test_batch_id_is_stamped_into_every_audit_event(tmp_path, monkeypatch):
    """Walk the mutation log: every event from this batch carries batch_id."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.dispatch_skills_batch(
        skill_ids=["workspace:workspace-policy", "workspace:workspace-workspace"],
        folder_context=str(folder),
        query="x",
    )
    bid = out["batch_id"]
    log = MutationLog(folder, log_root=log_root)
    matching = [e for e in log.replay()
                if e.pair_id == "skill-dispatch"
                and (e.extra or {}).get("batch_id") == bid]
    assert len(matching) == 2, [e.extra for e in log.replay()]


def test_batch_results_align_with_input_order(tmp_path, monkeypatch):
    """results[i].skill_id == skill_ids[i] for every i."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    inp = ["workspace:workspace-capture", "workspace:workspace-policy", "workspace:workspace-workspace"]
    out = srv.dispatch_skills_batch(
        skill_ids=inp, folder_context=str(folder),
    )
    assert [r["skill_id"] for r in out["results"]] == inp


def test_batch_resolves_pinned_set_once_via_pinned_skill(tmp_path, monkeypatch):
    """A non-workspace but pinned skill comes back with provenance='own'."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    pin_skill(str(folder), "my-plugin:research", log_root=log_root)
    out = srv.dispatch_skills_batch(
        skill_ids=["my-plugin:research", "workspace:workspace-policy"],
        folder_context=str(folder),
        query="run",
    )
    assert out["ok"] and out["ok_count"] == 2
    pins = {r["skill_id"]: r["provenance"] for r in out["results"]}
    assert pins["my-plugin:research"] == "own"
    assert pins["workspace:workspace-policy"] == "system"


# ---------------------------------------------------------------------------
# Partial failure
# ---------------------------------------------------------------------------


def test_batch_partial_failure_preserves_good_dispatches(tmp_path, monkeypatch):
    """One bad skill + two good workspace skills → 1 fail + 2 ok in results."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.dispatch_skills_batch(
        skill_ids=[
            "other-plugin:nope",            # not pinned, not workspace
            "workspace:workspace-policy",         # bypass
            "workspace:workspace-workspace",      # bypass
        ],
        folder_context=str(folder),
    )
    assert out["ok"] is True
    assert out["ok_count"] == 2
    assert out["fail_count"] == 1
    by_skill = {r["skill_id"]: r for r in out["results"]}
    bad = by_skill["other-plugin:nope"]
    assert bad["ok"] is False
    assert "not in resolved pinned set" in bad["error"]
    assert bad["in_resolved_set"] is False
    assert by_skill["workspace:workspace-policy"]["ok"] is True
    assert by_skill["workspace:workspace-workspace"]["ok"] is True


def test_batch_empty_skill_id_rejected_individually(tmp_path, monkeypatch):
    """An empty string in the list is a per-row failure, batch continues."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.dispatch_skills_batch(
        skill_ids=["", "workspace:workspace-policy"],
        folder_context=str(folder),
    )
    assert out["ok"] is True
    assert out["ok_count"] == 1
    assert out["fail_count"] == 1
    assert out["results"][0]["ok"] is False
    assert "empty skill_id" in out["results"][0]["error"]


def test_batch_duplicate_skill_ids_get_distinct_audit_ids(tmp_path, monkeypatch):
    """Two dispatches of the same skill produce two events with same skill_id,
    different audit_ids, same batch_id."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.dispatch_skills_batch(
        skill_ids=["workspace:workspace-policy", "workspace:workspace-policy"],
        folder_context=str(folder),
    )
    assert out["ok_count"] == 2
    a, b = out["results"]
    assert a["skill_id"] == b["skill_id"] == "workspace:workspace-policy"
    assert a["audit_id"] != b["audit_id"]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_batch_empty_list_rejected(tmp_path, monkeypatch):
    """An empty skill_ids list is a clean error at the batch level."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.dispatch_skills_batch(
        skill_ids=[], folder_context=str(folder),
    )
    assert out["ok"] is False
    assert "non-empty" in out["error"]


def test_batch_non_list_input_rejected(tmp_path, monkeypatch):
    """A non-list (e.g., a single string) is rejected at the batch level."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.dispatch_skills_batch(
        skill_ids="workspace:workspace-policy",  # bug: passed a string, not a list
        folder_context=str(folder),
    )
    assert out["ok"] is False
    assert "non-empty list" in out["error"]


# ---------------------------------------------------------------------------
# Integration with audit-chain (#22)
# ---------------------------------------------------------------------------


def test_batch_audit_ids_resolvable_via_get_audit_event(tmp_path, monkeypatch):
    """Every audit_id from the batch resolves via get_audit_event."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.dispatch_skills_batch(
        skill_ids=["workspace:workspace-policy", "workspace:workspace-workspace"],
        folder_context=str(folder),
    )
    for r in out["results"]:
        if not r["ok"]: continue
        rev = srv.get_audit_event(event_id=r["audit_id"],
                                  folder_context=str(folder))
        assert rev["ok"] is True
        assert rev["event"]["audit_id"] == r["audit_id"]
        # The batch_id stamping is observable from the resolved event
        assert rev["event"]["extra"].get("batch_id") == out["batch_id"]


def test_batch_dispatch_listed_in_server_info(tmp_path, monkeypatch):
    """0.6.6+: dispatch_skills_batch lives behind the workspace_dispatch facade;
    server_info advertises the facade and the op stays reachable through it."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    info = srv.server_info()
    assert "workspace_dispatch" in info["tools"], info["tools"]
    ops = {o["op"] for o in srv.workspace_dispatch("help")["ops"]}
    assert "dispatch_batch" in ops, ops
