# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Party registry round-trip (§ 1.5): humans + agents on the chain,
routing join, kill switch as event."""
from __future__ import annotations

import os

import pytest

from workspaces.parties import (
    list_parties, register_party, route_approvers, set_party_status,
)

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def test_register_list_and_route(env):
    register_party(env["ws"], "alex", "human", name="Alex", role="controller",
                   competences=["data-protection", "music"],
                   channels=["mail:alex" + "@" + "corp.example"],
                   log_root=env["lr"])
    register_party(env["ws"], "guardian-1", "agent", owner="alex",
                   purpose="watchdog", grade="L1", log_root=env["lr"])

    everyone = list_parties(env["ws"], log_root=env["lr"])
    assert everyone["count"] == 2
    humans = list_parties(env["ws"], kind="human", log_root=env["lr"])
    assert humans["count"] == 1 and humans["parties"][0]["role"] == "controller"

    r = route_approvers(env["ws"], "data-protection", log_root=env["lr"])
    assert r["count"] == 1 and r["approvers"][0]["party_id"] == "alex"
    # Agents never route as approvers; unknown competence routes nobody.
    assert route_approvers(env["ws"], "finance", log_root=env["lr"])["count"] == 0


def test_kill_switch_removes_from_routing_immediately(env):
    register_party(env["ws"], "anna", "human",
                   competences=["legal"], log_root=env["lr"])
    assert route_approvers(env["ws"], "legal", log_root=env["lr"])["count"] == 1
    set_party_status(env["ws"], "anna", "killed", reason="left org",
                     log_root=env["lr"])
    assert route_approvers(env["ws"], "legal", log_root=env["lr"])["count"] == 0
    # Still listed (history is append-only), with status visible.
    rows = list_parties(env["ws"], log_root=env["lr"])["parties"]
    assert rows[0]["status"] == "killed"


def test_reregistration_appends_new_version(env):
    register_party(env["ws"], "alex", "human", role="controller",
                   log_root=env["lr"])
    register_party(env["ws"], "alex", "human", role="dpo",
                   log_root=env["lr"])
    rows = list_parties(env["ws"], log_root=env["lr"])
    assert rows["count"] == 1 and rows["parties"][0]["role"] == "dpo"


def test_invalid_inputs_refused(env):
    with pytest.raises(ValueError):
        register_party(env["ws"], "x", "robot", log_root=env["lr"])
    with pytest.raises(ValueError):
        register_party(env["ws"], "", "human", log_root=env["lr"])
    with pytest.raises(ValueError):
        set_party_status(env["ws"], "x", "paused", log_root=env["lr"])
