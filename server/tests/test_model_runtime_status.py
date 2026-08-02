# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The workspace_model status op — per-task readiness, the Tier C scan backend,
and endpoint reachability, read-only. Declares recorded/probed state; runs no
model and never fabricates health."""
from __future__ import annotations

import json

from workspaces import mcp_server as srv


def _status(**params):
    return srv.workspace_model("status", params)


def test_status_shape_and_serialisable(monkeypatch):
    monkeypatch.delenv("WORKSPACE_LOCAL_LLM_URL", raising=False)
    out = _status()
    assert out["ok"] is True
    r = out["readiness"]
    assert r["version"] == "model_capability/v1"
    assert set(r["ready"]) | set(r["degraded"]) == set(r["tasks"])
    tc = out["tier_c"]
    assert isinstance(tc["backend"], str) and tc["backend"]
    assert isinstance(tc["available"], bool) and isinstance(tc["fail_closed"], bool)
    json.dumps(out)                               # MCP boundary shape


def test_status_endpoint_unconfigured_is_honest(monkeypatch):
    monkeypatch.delenv("WORKSPACE_LOCAL_LLM_URL", raising=False)
    out = _status()
    assert out["endpoint"]["reachable"] is False   # unconfigured reads as unreachable


def test_status_can_skip_the_endpoint_probe():
    out = _status(probe_endpoint=False)
    assert "endpoint" not in out


def test_degraded_task_names_its_action(monkeypatch):
    # With no models registered, at least one task degrades and its projection
    # names the bounded fallback the seam will take.
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", "/nonexistent/models-dir")
    out = _status(probe_endpoint=False)
    r = out["readiness"]
    assert r["degraded"], "no registry should leave tasks degraded"
    task = r["tasks"][r["degraded"][0]]
    assert task["capable"] is False and task.get("action")


def test_help_lists_status_op():
    ops = {o["op"] for o in srv.workspace_model("help")["ops"]}
    assert "status" in ops
