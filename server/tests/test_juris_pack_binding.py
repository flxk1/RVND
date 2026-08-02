# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Folder ↔ pack-stack binding.

Written before the logic. The TASKS verification lines: a folder with
[eu-base, de-overlay] resolves composed forms on an action's footprint;
a dated pack is INACTIVE before its effective date. Binding semantics
follow the TDM cascade precedent (§ 1.5 strictest-wins): ancestors'
packs cascade down and a sub-folder cannot remove them — declaring more
packs only ever tightens. The setter is audited like every policy change.
"""
from __future__ import annotations

import json
import os

import pytest

from workspaces.controlforms import guarantees, leq
from workspaces.mutation_log import MutationLog

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    parent = tmp_path / "org"
    child = parent / "sub"
    sibling = parent / "other"
    for d in (parent, child, sibling):
        d.mkdir(parents=True)
    return {"parent": str(parent), "child": str(child),
            "sibling": str(sibling), "lr": str(tmp_path / "logs")}


# --- declaration + audit ------------------------------------------------------

def test_set_and_resolve_round_trip(env):
    from workspaces.juris_packs import resolve_folder_packs, set_folder_packs
    r = set_folder_packs(env["parent"], ["eu-base", "de-overlay"],
                         log_root=env["lr"])
    assert r["ok"] is True
    ids = [p["pack_id"] for p in resolve_folder_packs(env["parent"])]
    assert ids == ["eu-base", "de-overlay"]


def test_unknown_pack_refused_nothing_persisted(env):
    from workspaces.juris_packs import resolve_folder_packs, set_folder_packs
    with pytest.raises(ValueError):
        set_folder_packs(env["parent"], ["eu-base", "atlantis"],
                         log_root=env["lr"])
    assert resolve_folder_packs(env["parent"]) == []


def test_setter_is_audited(env):
    from workspaces.juris_packs import set_folder_packs
    set_folder_packs(env["parent"], ["eu-base"], actor="alex",
                     log_root=env["lr"])
    log = MutationLog(env["parent"], log_root=env["lr"])
    hits = [e for e in log.replay()
            if (e.extra or {}).get("policy_change") == "juris_packs"
            and e.actor == "alex"]
    assert hits, "pack-stack change must land on the chain"


# --- cascade: ancestors bind, descendants only add ----------------------------

def test_child_inherits_and_extends_parent_stack(env):
    from workspaces.juris_packs import resolve_folder_packs, set_folder_packs
    set_folder_packs(env["parent"], ["eu-base"], log_root=env["lr"])
    set_folder_packs(env["child"], ["de-overlay"], log_root=env["lr"])
    ids = [p["pack_id"] for p in resolve_folder_packs(env["child"])]
    assert ids == ["eu-base", "de-overlay"]   # ancestor first, then own


def test_child_cannot_remove_ancestor_pack(env):
    from workspaces.juris_packs import resolve_folder_packs, set_folder_packs
    set_folder_packs(env["parent"], ["eu-base"], log_root=env["lr"])
    set_folder_packs(env["child"], [], log_root=env["lr"])
    assert [p["pack_id"] for p in resolve_folder_packs(env["child"])] == \
        ["eu-base"]


def test_siblings_independent(env):
    from workspaces.juris_packs import resolve_folder_packs, set_folder_packs
    set_folder_packs(env["child"], ["de-overlay"], log_root=env["lr"])
    assert resolve_folder_packs(env["sibling"]) == []


def test_duplicate_declaration_is_idempotent(env):
    from workspaces.juris_packs import resolve_folder_packs, set_folder_packs
    set_folder_packs(env["parent"], ["eu-base"], log_root=env["lr"])
    set_folder_packs(env["child"], ["eu-base", "de-overlay"],
                     log_root=env["lr"])
    ids = [p["pack_id"] for p in resolve_folder_packs(env["child"])]
    assert ids == ["eu-base", "de-overlay"]   # deduped, ancestor-first


# --- effective dating ----------------------------------------------------------

def test_dated_pack_inactive_before_its_date(env, tmp_path):
    from workspaces.juris_packs import folder_required_forms, set_folder_packs
    dated = tmp_path / "future-pack.json"
    dated.write_text(json.dumps({
        "pack_id": "future", "version": "1", "jurisdiction": "XX",
        "effective_from": "2027-01-01",
        "controls": {"personal-data": "four_eyes"},
    }), encoding="utf-8")
    set_folder_packs(env["parent"], [str(dated)], log_root=env["lr"])
    before = folder_required_forms(env["parent"], ["personal-data"],
                                   as_of="2026-06-12")
    after = folder_required_forms(env["parent"], ["personal-data"],
                                  as_of="2027-01-01")
    assert before == []
    assert after and leq("four_eyes", after[0])


# --- the verification line: stack resolves on an action's footprint ------------

def test_folder_stack_resolves_composed_forms(env):
    from workspaces.juris_packs import (
        folder_required_forms, load_reference_pack, set_folder_packs)
    set_folder_packs(env["parent"], ["eu-base", "de-overlay"],
                     log_root=env["lr"])
    forms = folder_required_forms(env["parent"], ["personal-data"],
                                  as_of="2026-06-12")
    assert len(forms) == 1
    for name in ("eu-base", "de-overlay"):
        pack = load_reference_pack(name)
        assert leq(pack["controls"]["personal-data"], forms[0])


def test_workspace_matrix_explain_resolves_folder_footprint(env):
    from workspaces import mcp_server
    from workspaces.juris_packs import set_folder_packs
    set_folder_packs(env["parent"], ["eu-base", "de-overlay"],
                     log_root=env["lr"])
    ex = mcp_server.workspace_matrix("explain", {
        "folder_context": env["parent"], "grade": "L0",
        "oversight": "autonomous", "footprint": ["personal-data"]})
    got = frozenset(ex["guarantees"])
    assert leq("expert_review", got)      # de-overlay's demand arrived
    assert leq("single_approver", got)    # eu-base's demand arrived
    assert ex["light"] == "go"            # the painted grid is untouched


def test_workspace_policy_juris_packs_op_round_trip(env):
    from workspaces import mcp_server
    r = mcp_server.workspace_policy("juris_packs", {
        "folder_context": env["parent"], "packs": ["eu-base"]})
    assert r["ok"] is True and r["juris_packs"] == ["eu-base"]
    g = mcp_server.workspace_policy("juris_packs", {
        "folder_context": env["parent"]})
    assert [p["pack_id"] for p in g["resolved"]] == ["eu-base"]
