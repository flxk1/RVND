# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""governance_graph v0.5 projection.

The tests cover v0.5 relabeling, verdict alphabet, legacy projection
compatibility, unknown-verdict clamping, id collisions, and duplicate ids.
"""
from __future__ import annotations

import os
import time
import pytest

from workspaces import mcp_server as M
from workspaces import loomground_lang as L
from workspaces.governance_graph import governance_graph, governance_graph_v05, _project_v05
from workspaces.loomground_lang import VERDICTS

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

V05 = """
actor  bot7
human  alice  role legal
gate   draft   risk low    grant bot7
gate   decide  risk high   grant bot7
cord bot7   -> draft
cord bot7   -> decide
cord draft  -> master
cord decide -> master
"""


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = str(tmp_path / "org"); os.makedirs(ws)
    M.workspace_workflow(op="patch_apply", params={
        "folder_context": ws, "actor": "alex", "netlist": V05})
    return ws


def test_v05_projection_relabels(seeded):
    g = governance_graph_v05(seeded)
    classes = {n["id"]: n["class"] for n in g["nodes"]}
    assert classes["bot7"] == "actor"
    assert classes["alice"] == "human"
    assert classes["draft"] == "gate" and classes["decide"] == "gate"
    assert classes["master"] == "master"
    # v0.5 attributes
    alice = next(n for n in g["nodes"] if n["id"] == "alice")
    assert alice["role"] == "legal"
    decide = next(n for n in g["nodes"] if n["id"] == "decide")
    assert decide["risk_floor"] == "high"
    # typed cords, bare ids
    ctypes = {(c["from"], c["to"]): c["type"] for c in g["cords"]}
    assert ctypes[("bot7", "draft")] == "authority"
    assert ctypes[("draft", "master")] == "egress"
    assert g["nodes"][-1]["class"] == "master"  # master projected last


def test_v05_verdict_alphabet(seeded):
    M.workspace_workflow(op="operate", params={
        "folder_context": seeded, "use_case_id": "draft", "agent_id": "bot7",
        "issues": [{"issue_id": "i1", "issue_type": "", "completeness": "high"}],
        "now_epoch": int(time.time())})
    g = governance_graph_v05(seeded)
    assert "draft" in g["verdicts"]
    assert g["verdicts"]["draft"]["verdict"] in VERDICTS


def test_legacy_projection_unchanged(seeded):
    g = governance_graph(seeded)
    kinds = {n["kind"] for n in g["nodes"]}
    assert "agent" in kinds and "use_case" in kinds  # old vocabulary intact
    assert "summary" in g and g["summary"]["use_cases"] == 2


def test_failsafe_unknown_verdict_clamps_most_restrictive():
    # Critic-panel fix: an unrecognised verdict must NOT coerce to the releasing
    # `auto`; it clamps to the most restrictive symbol and is warned.
    legacy = {"folder_context": "x",
              "nodes": [{"id": "uc:g", "kind": "use_case", "risk": "high"},
                        {"id": "master", "kind": "master"}],
              "edges": [{"from": "uc:g", "to": "master", "kind": "egress",
                         "verdict": "weird"}],
              "summary": {}}
    out = _project_v05(legacy)
    assert out["verdicts"]["g"]["verdict"] == VERDICTS[-1] == "prohibited"
    assert any("clamped" in w for w in out.get("warnings", []))


def test_id_collision_keeps_prefix_and_warns():
    # Critic-panel fix: a same-named actor+gate must not silently merge.
    legacy = {"folder_context": "x",
              "nodes": [{"id": "party:draft", "kind": "agent"},
                        {"id": "uc:draft", "kind": "use_case", "risk": "low"},
                        {"id": "master", "kind": "master"}],
              "edges": [{"from": "party:draft", "to": "uc:draft", "kind": "authority"}],
              "summary": {}}
    out = _project_v05(legacy)
    ids = sorted(n["id"] for n in out["nodes"])
    assert ids == ["master", "party:draft", "uc:draft"]   # prefixes kept, no merge
    assert out["warnings"]


def test_validate_rejects_duplicate_ids():
    # the language fix that prevents the collision via patch_apply
    patch = L.parse("actor draft\ngate draft risk low\ncord draft -> master\n")
    assert not L.validate(patch)["ok"]
