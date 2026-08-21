# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Read-only inventory/register of agents and use cases."""
from __future__ import annotations

import os
import pytest

from rvnd import mcp_server as M
from rvnd.governance_graph import governance_register

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    # Pin the log root so the facade reads (via _log_root()) and writes share it
    # regardless of a leaked env var from an earlier test (another module
    # setdefault's WORKSPACE_L0_LOG_ROOT for the session). The direct governance_register
    # import below ignores env, so the test passes this same root explicitly.
    lr = str(tmp_path / "logs")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", lr)
    w = str(tmp_path / "org"); os.makedirs(w)
    M.workspace_policy(op="party_register", params={"folder_context": w, "party_id": "bot7", "kind": "agent", "grade": "L2", "actor": "f"})
    M.workspace_workflow(op="use_case_register", params={"folder_context": w, "use_case_id": "uc-draft", "name": "uc-draft", "fingerprint": {"issue_type": "liability_cap"}, "risk": "low", "allowed_agents": ["bot7"], "actor": "f"})
    # Reservations are authored or ingested, not inferred from a legal enum.
    # uc-decide is reserved via an AUTHORED `.lg reserve` (basis_kind 'policy'),
    # no longer auto-derived from the automated_decision fingerprint.
    # no agent granted on uc-decide (allowed_agents stays empty, as before) — just
    # the gate + the authored reservation + the egress path to the boundary.
    M.workspace_workflow(op="patch_apply", params={"folder_context": w, "actor": "f",
        "netlist": ("human dpo role data-protection\n"
                    "gate uc-decide risk high\nreserve uc-decide by dpo\n"
                    "cord uc-decide -> master\n")})
    return {"ws": w, "lr": lr}


def test_register_rows(ws):
    rows = {r["id"]: r for r in governance_register(ws["ws"], log_root=ws["lr"])["rows"]}
    assert rows["party:bot7"]["type"] == "agent" and rows["party:bot7"]["authority_over"] == 1
    assert rows["uc:uc-draft"]["risk"] == "low" and rows["uc:uc-draft"]["wired"] is True
    dec = rows["uc:uc-decide"]
    # the flat claim is renamed off the overclaiming "by law": it's `reserved`
    # (the verdict) with the basis attributed separately, never folded into "law".
    assert dec["risk"] == "high" and dec["reserved"] is True and dec["verdict"] == "reserved"
    assert dec["reserved_bases"] == ["policy"]  # authored `.lg reserve` -> policy basis
    assert "reserved_by_law" not in dec         # the overclaiming field is gone
    # categorical only — no numeric score / percentage anywhere
    for r in rows.values():
        assert not any(isinstance(v, float) for v in r.values())


def test_facade_register_surface(ws):
    r = M.workspace_workflow(op="governance_register", params={"folder_context": ws["ws"]})
    assert sum(1 for x in r["rows"] if x["type"] == "use_case") == 2
    assert any(x["type"] == "agent" for x in r["rows"])
    assert len(M._DECLARED_TOOLS) == 24


def test_register_all_scope(ws):
    r = M.workspace_workflow(op="governance_register", params={"folder_context": ws["ws"], "scope": "all"})
    assert "folders" in r and isinstance(r["folders"], list) and "count" in r
