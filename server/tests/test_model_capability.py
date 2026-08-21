# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Capability layer for choosing local models or deterministic degradation.

The tests cover model matching, selection, registry projection, readiness output,
and the read-only MCP operation.
"""
from __future__ import annotations

import os

from rvnd import model_capability as MC
from rvnd import models_registry as MR
from rvnd.models_registry import ModelEntry

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def test_capable_model_runs_local():
    big = MC.infer_profile("qwen-32b", params_billions=32)
    assert big.tier == "large"
    m = MC.match("extraction", big)
    assert m.capable and m.action == "run_local" and not m.missing


def test_too_small_model_degrades_not_hallucinates():
    tiny = MC.infer_profile("phi-mini", params_billions=1.5)        # small → no json
    m = MC.match("extraction", tiny)
    assert not m.capable and m.action == "deterministic"           # cues-only, not garbage
    assert "json" in m.missing


def test_interpretation_escalates_to_human_on_small():
    med = MC.infer_profile("mistral-7b", params_billions=7)         # medium → no reasoning/long_context
    m = MC.match("interpretation", med)
    assert not m.capable and m.action == "escalate_human"


def test_no_model_degrades():
    m = MC.match("privacy_semantic", None)
    assert not m.capable and m.action == "deterministic" and m.model_id is None


def test_select_picks_best_capable_else_degrades():
    small = MC.infer_profile("phi-mini", params_billions=1.5)
    big = MC.infer_profile("qwen-32b", params_billions=32)
    chosen = MC.select("extraction", [small, big])
    assert chosen.capable and chosen.model_id == "qwen-32b"         # only the large one is capable
    # ask_routing only needs classification → even the small model runs it
    assert MC.select("ask_routing", [small]).action == "run_local"
    # nothing capable of interpretation → degrade
    assert MC.select("interpretation", [small]).action == "escalate_human"


# ── the bridge to the real registry (the wiring that makes this module LIVE) ──
def test_profile_from_registry_id_parses_size():
    assert MC.profile_from_registry_id("llama-3-8b-instruct").tier == "medium"
    assert MC.profile_from_registry_id("qwen2.5-32b").tier == "large"
    assert MC.profile_from_registry_id("phi-mini").tier == "small"     # no size hint → conservative


def test_for_task_reads_the_registry(monkeypatch):
    monkeypatch.setattr(MR, "list_models", lambda: [ModelEntry(id="qwen-32b")])
    m = MC.for_task("extraction")
    assert m.capable and m.action == "run_local" and m.model_id == "qwen-32b"
    monkeypatch.setattr(MR, "list_models", lambda: [ModelEntry(id="phi-2b")])   # small only
    assert MC.for_task("extraction").action == "deterministic"
    monkeypatch.setattr(MR, "list_models", lambda: [])                          # nothing pulled
    assert MC.for_task("interpretation").action == "escalate_human"


def test_readiness_projection(monkeypatch):
    monkeypatch.setattr(MR, "list_models", lambda: [ModelEntry(id="mistral-7b")])   # medium
    r = MC.readiness()
    assert r["version"] == "model_capability/v1"
    assert "extraction" in r["ready"] and "interpretation" in r["degraded"]
    assert set(r["tasks"]) == set(MC.TASKS)


def test_capability_op_is_readonly_and_discoverable(monkeypatch):
    from rvnd import mcp_server as M
    monkeypatch.setattr(MR, "list_models", lambda: [ModelEntry(id="qwen-32b")])
    full = M.workspace_workflow("model_capability", {"folder_context": ""})
    assert full["version"] == "model_capability/v1" and "extraction" in full["ready"]
    one = M.workspace_workflow("model_capability", {"folder_context": "", "task": "interpretation"})
    assert one["task"] == "interpretation" and one["capable"]          # 32b large → reasoning ok
    ops = {row["op"] for row in M.workspace_workflow("ops", {"folder_context": ""})["ops"]}
    assert "model_capability" in ops
