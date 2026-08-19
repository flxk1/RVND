# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""A chain→.lg→apply round-trip must not silently drop a
reservation. _loom_apply re-registered every gate with fingerprint={}, so a
no-op load→apply blanked reserved_acts (latest-wins) and a reserved task
became auto-eligible — silent governance widening. Editors panel.

G2 (2026-06-25): reservations now authored/ingested, not from a legal enum.
The reservation under test is AUTHORED via a `.lg reserve <gate> by <role>`
sentence (routed through patch_apply -> register_use_case(policy_reservations=)),
not auto-derived from an `automated_decision` fingerprint. The E1 round-trip
property (no silent drop) is identical — only its SOURCE changed."""
from __future__ import annotations

import os

import pytest

from rvnd import mcp_server as M


# A reserved use case AUTHORED via a `.lg reserve` sentence (G2 doctrine):
# the gate `uc-decide` is reserved to a data-protection officer by ingested policy.
# patch_apply routes this through register_use_case(policy_reservations=), so the
# use case carries a reserved act exactly as a chain/round-trip consumer expects.
_RESERVED_NETLIST = (
    "actor bot7\n"
    "human dpo role data-protection\n"
    "gate uc-decide risk high grant bot7\n"
    "reserve uc-decide by dpo\n"
    "cord bot7 -> uc-decide\n"
    "cord uc-decide -> master\n")


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "org"
    f.mkdir()
    # G2: a RESERVED use case, authored via `.lg reserve uc-decide by dpo`
    # (no longer auto-derived from an automated_decision fingerprint).
    res = M.workspace_workflow(op="patch_apply", params={
        "folder_context": str(f), "actor": "alex", "netlist": _RESERVED_NETLIST})
    assert res["ok"], res
    return str(f)


def _uc(folder, uc="uc-decide"):
    return M.workspace_workflow(op="use_case_get",
                           params={"folder_context": folder, "use_case_id": uc})["use_case"]


def _reserved(folder, uc="uc-decide"):
    return (_uc(folder, uc) or {}).get("reserved_acts") or []


def test_reservation_present_after_register(folder):
    # G2 (2026-06-25): reservations now authored/ingested, not from a legal enum.
    assert _reserved(folder), "precondition: the use case should carry its authored reservation"


def test_noop_roundtrip_preserves_reservation(folder):
    # chain → netlist → patch_apply (a no-op load→apply through the real ops)
    nl = M.workspace_workflow(op="governance_netlist", params={"folder_context": folder})["netlist"]
    res = M.workspace_workflow(op="patch_apply",
                          params={"folder_context": folder, "actor": "alex", "netlist": nl})
    assert res["ok"], res
    assert _reserved(folder), "E1: the authored reservation was DROPPED by the round-trip"
    assert "uc-decide" in res.get("preserved_reservations", []), \
        "apply should surface (not silently keep) the carried-forward reservation"


def test_negative_control_no_authored_reservation_has_none(folder):
    # G2 (2026-06-25): reservations now authored/ingested, not from a legal enum.
    # Negative control (so the positive tests are not vacuous): a use case with NO
    # authored `reserve` sentence carries NO reserved act. Under the old doctrine
    # this control proved a blank fingerprint dropped a legal reservation; now it
    # proves the engine never invents a reservation absent an authored one.
    assert _reserved(folder)        # the fixture's authored gate IS reserved
    M.workspace_workflow(op="patch_apply", params={
        "folder_context": folder, "actor": "alex",
        "netlist": ("actor bot7\ngate uc-plain risk high grant bot7\n"
                    "cord bot7 -> uc-plain\ncord uc-plain -> master\n")})
    assert not _reserved(folder, "uc-plain"), \
        "control: a use case with no authored reserve MUST carry no reserved_acts"


def test_structural_change_preserves_reservation_and_grade(tmp_path, monkeypatch):
    # G2 (2026-06-25): reservations now authored/ingested, not from a legal enum.
    # A genuine structural change (add an agent) must preserve BOTH the AUTHORED
    # reservation AND the earned grade. contract_id is content-addressed from
    # (risk + the contract inputs), so an unchanged contract_id proves the grade
    # was carried forward (re-registering with prior_approvals=0 would change it).
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = str(tmp_path / "org"); os.makedirs(f)
    M.workspace_policy("party_register", {"folder_context": f, "party_id": "bot7", "kind": "agent", "grade": "L2"})
    M.workspace_policy("party_register", {"folder_context": f, "party_id": "bot9", "kind": "agent", "grade": "L1"})
    # author the reservation via .lg, then harden the grade via a direct register
    # that carries the authored reserved act forward (sticky policy reservation).
    M.workspace_workflow(op="patch_apply", params={
        "folder_context": f, "actor": "alex",
        "netlist": ("actor bot7\nhuman dpo role data-protection\n"
                    "gate uc-decide risk high grant bot7\nreserve uc-decide by dpo\n"
                    "cord bot7 -> uc-decide\ncord uc-decide -> master\n")})
    from rvnd.use_case import register_use_case as _ruc
    authored = _uc(f, "uc-decide")["reserved_acts"]
    _ruc(f, use_case_id="uc-decide", name="Decide",
         fingerprint={}, risk="high", allowed_agents=["bot7"], actor="alex",
         prior_approvals=30, carry_reserved=authored, log_root=M._log_root())
    before = _uc(f, "uc-decide")
    cid0 = before["contract_id"]

    # real change: a patch that grants bot9 authority on uc-decide
    patch = {
        "nodes": [{"id": "party:bot7", "class": "actor"},
                  {"id": "party:bot9", "class": "actor"},
                  {"id": "uc-decide", "class": "gate", "name": "Decide", "risk_floor": "high"},
                  {"id": "master", "class": "master"}],
        "cords": [{"from": "party:bot7", "to": "uc-decide"},
                  {"from": "party:bot9", "to": "uc-decide"},
                  {"from": "uc-decide", "to": "master"}],   # egress path to the boundary
    }
    res = M.workspace_workflow(op="patch_apply", params={"folder_context": f, "actor": "alex", "patch": patch})
    assert res["ok"], res
    after = _uc(f, "uc-decide")
    assert after["reserved_acts"], "reservation must survive a real structural change"
    assert after["contract_id"] == cid0, "earned grade was reset (contract inputs not carried forward)"
    assert any("bot9" in a for a in (after["allowed_agents"] or [])), "the change must take effect"


def test_id_collision_roundtrip_preserves_reservation(folder):
    # A party and a use case sharing a bare id force governance_graph to KEEP the
    # "uc:" prefix in the netlist; the round-trip must map "uc:shared" back to the
    # bare "shared" record and preserve its reservation (blocker: prefix miss).
    # G2 (2026-06-25): 'shared' is reserved via an AUTHORED `.lg reserve` sentence,
    # not the automated_decision enum. A party and a use case both named 'shared'
    # force governance_graph to keep the "uc:" prefix in the netlist.
    M.workspace_policy("party_register", {"folder_context": folder, "party_id": "shared",
                                     "kind": "agent", "grade": "L1"})
    M.workspace_workflow(op="patch_apply", params={
        "folder_context": folder, "actor": "alex",
        "netlist": ("actor bot7\nhuman dpo role data-protection\n"
                    "gate shared risk high grant bot7\nreserve shared by dpo\n"
                    "cord bot7 -> shared\ncord shared -> master\n")})
    assert _reserved(folder, "shared"), "precondition: 'shared' use case is reserved"
    nl = M.workspace_workflow(op="governance_netlist", params={"folder_context": folder})["netlist"]
    assert "uc:shared" in nl, "precondition: the collision should keep the uc: prefix in the netlist"
    res = M.workspace_workflow(op="patch_apply",
                          params={"folder_context": folder, "actor": "alex", "netlist": nl})
    assert res["ok"], res
    assert _reserved(folder, "shared"), "collision round-trip silently dropped the reservation"
