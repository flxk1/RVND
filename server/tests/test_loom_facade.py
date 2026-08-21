# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""MCP facade ops patch_validate/patch_apply speak Loomground v0.5.

The tests cover validation, old-dialect rejection, chain writes via the
bijection, fail-closed apply, malformed input errors, and declarations reaching
the chain.
"""
from __future__ import annotations

import os
import pytest

from rvnd import mcp_server as M
from rvnd.governance_graph import governance_graph

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
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"; ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def test_validate_accepts_v05_rejects_old_dialect(env):
    v = M.workspace_workflow(op="patch_validate", params={
        "folder_context": env["ws"], "netlist": V05})
    assert v["ok"], v.get("errors")
    assert v["projection"]["nodes"][-1] == {"id": "master", "class": "master"}
    # the pre-standard dialect is no longer accepted
    old = M.workspace_workflow(op="patch_validate", params={
        "folder_context": env["ws"], "netlist": "agent a1\nuse-case uc risk low\n"})
    assert not old["ok"]


def test_apply_writes_via_bijection(env, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", env["lr"] + "/keys")
    r = M.workspace_workflow(op="patch_apply", params={
        "folder_context": env["ws"], "actor": "alex", "netlist": V05})
    assert r["ok"], r.get("errors")
    g = r["graph"]
    agents = {n["id"] for n in g["nodes"] if n["kind"] == "agent"}
    humans = {n["id"] for n in g["nodes"] if n["kind"] == "human"}
    ucs = {n["id"] for n in g["nodes"] if n["kind"] == "use_case"}
    assert agents == {"party:bot7"}
    assert humans == {"party:alice"}
    assert ucs == {"uc:draft", "uc:decide"}
    auth = {(e["from"], e["to"]) for e in g["edges"] if e["kind"] == "authority"}
    assert auth == {("party:bot7", "uc:draft"), ("party:bot7", "uc:decide")}


def test_apply_failclosed_human_authority(env):
    bad = "human alice role legal\ngate g risk low\ncord alice -> g\n"
    r = M.workspace_workflow(op="patch_apply", params={
        "folder_context": env["ws"], "actor": "alex", "netlist": bad})
    assert not r["ok"]
    g = governance_graph(env["ws"], log_root=None)
    assert g["summary"]["agents"] == 0 and g["summary"]["use_cases"] == 0


def test_surface_unchanged():                                  # F4
    assert len(M._DECLARED_TOOLS) == 24
    ops = {o["op"] for o in M.workspace_workflow(op="help")["ops"]}
    assert {"patch_validate", "patch_apply"} <= ops


def test_malformed_input_returns_clean_error(env):
    # Non-string netlist and malformed patch dicts must return {ok:False},
    # never crash the op (critic panel finding).
    for params in (
        {"folder_context": env["ws"], "netlist": None},
        {"folder_context": env["ws"], "patch": ["not", "a", "dict"]},
        {"folder_context": env["ws"], "patch": {"nodes": None}},
        {"folder_context": env["ws"], "patch": {"nodes": ["foo"]}},
        {"folder_context": env["ws"], "patch": {"nodes": [{"id": "x"}]}},  # missing class
    ):
        r = M.workspace_workflow(op="patch_validate", params=params)
        assert r["ok"] is False and "errors" in r, params


def test_reserve_declaration_reaches_the_chain(env):
    # A reserve declaration must show in the projection AND be WRITTEN to the
    # chain on apply (a reserved act on the matching use case) — no longer merely
    # surfaced as pending, and never silently dropped.
    net = ("actor bot\nhuman dpo role dpo\ngate automated_decision risk high grant bot\n"
           "reserve automated_decision by dpo\n"
           "cord bot -> automated_decision\ncord automated_decision -> master\n")
    v = M.workspace_workflow(op="patch_validate", params={
        "folder_context": env["ws"], "netlist": net})
    assert v["ok"] and v["projection"]["reservations"][0]["kind"] == "automated_decision"
    r = M.workspace_workflow(op="patch_apply", params={
        "folder_context": env["ws"], "actor": "alex", "netlist": net})
    assert r["ok"]
    assert "reservations" not in r["pending"]        # enforced now, not deferred
    ucn = {n["id"]: n for n in r["graph"]["nodes"] if n["kind"] == "use_case"}
    assert ucn["uc:automated_decision"]["reserved"]  # reserved act landed on the chain
