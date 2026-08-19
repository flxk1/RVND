# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Obligations + redress declared in a .lg patch are WRITTEN to the chain.

Previously `_loom_apply` surfaced obligation/redress declarations as `pending`
(declared but dropped on apply). They are declared duties (obligation) / remedies
(redress) that ride WITH the gate, so they must persist, project, and carry
forward on a structure-only re-apply (E1) — the same no-silent-drop rule the
reservations already follow."""
from __future__ import annotations
import os, tempfile

import rvnd.mcp_server as M
from rvnd.use_case import get_use_case


def _ws():
    os.environ.setdefault("WORKSPACE_KEY_DIR", tempfile.mkdtemp())
    os.environ.setdefault("WORKSPACE_L0_LOG_ROOT", tempfile.mkdtemp())
    return tempfile.mkdtemp(prefix="oblig_")


_WITH = ("actor bot\nhuman officer role officer\n"
         "gate disclose risk low grant bot\n"
         "obligation ai-disclosure on disclose\n"
         "redress disclose by officer overturn\n"
         "cord bot -> disclose\ncord disclose -> master\n")
_STRUCT = ("actor bot\ngate disclose risk low grant bot\n"
           "cord bot -> disclose\ncord disclose -> master\n")


def test_obligation_and_redress_persist_to_chain():
    ws = _ws()
    v = M.workspace_workflow(op="patch_validate", params={"folder_context": ws, "netlist": _WITH})
    assert v["ok"]
    r = M.workspace_workflow(op="patch_apply", params={"folder_context": ws, "actor": "alex", "netlist": _WITH})
    assert r["ok"]
    # no longer deferred to pending
    assert "obligations" not in r["pending"] and "redress" not in r["pending"]
    assert r["applied"]["obligations"] >= 1 and r["applied"]["redress"] >= 1
    # written to the chain
    uc = get_use_case(ws, "disclose", log_root=M._log_root())
    assert uc["obligations"] and uc["obligations"][0]["obligation"] == "ai-disclosure"
    assert uc["redress"] and uc["redress"][0].get("kind") == "disclose" and uc["redress"][0].get("by") == "officer"
    # projected on the graph node (so the UI can show them)
    node = next(n for n in r["graph"]["nodes"] if n["id"] == "uc:disclose")
    assert node["obligations"] and node["redress"]


def test_obligation_carried_forward_on_structure_only_reapply():
    # the netlist editor's "load current" carries STRUCTURE only; re-applying it
    # must NOT silently drop the obligation/redress already on the chain (E1).
    ws = _ws()
    M.workspace_workflow(op="patch_apply", params={"folder_context": ws, "actor": "f", "netlist": _WITH})
    r2 = M.workspace_workflow(op="patch_apply", params={"folder_context": ws, "actor": "f", "netlist": _STRUCT})
    assert r2["ok"]
    uc = get_use_case(ws, "disclose", log_root=M._log_root())
    assert uc["obligations"], "obligation dropped on a structure-only re-apply (E1)"
    assert uc["redress"], "redress dropped on a structure-only re-apply (E1)"


def test_malformed_declaration_item_does_not_crash_apply():
    # a hand-built patch with a non-dict obligation/redress item must be skipped,
    # not crash _loom_apply (the netlist parser only emits dicts, but be defensive).
    import rvnd.loomground_lang as L
    ws = _ws()
    patch = L.parse("actor bot\ngate g1 risk low grant bot\ncord bot -> g1\ncord g1 -> master\n")
    patch["obligations"] = ["malformed", {"obligation": "x", "on": "g1"}]
    patch["redress"] = [42, {"kind": "g1", "by": "officer"}]
    r = M._loom_apply(ws, patch, actor="t", log_root=M._log_root())
    assert r["ok"]                                   # did not crash on the junk items
    uc = get_use_case(ws, "g1", log_root=M._log_root())
    assert uc["obligations"] and uc["obligations"][0]["obligation"] == "x"   # the good one landed
    assert uc["redress"] and uc["redress"][0]["by"] == "officer"


def test_reservations_are_sticky_across_applies():
    # Adding a reservation must NOT drop a prior one (no silent drop, mirroring
    # sticky prohibitions). Two applies, each reserving the same gate by a different
    # role → BOTH survive on the chain.
    ws = _ws()
    M.workspace_workflow(op="patch_apply", params={"folder_context": ws, "actor": "f",
        "netlist": "actor bot\nhuman dpo role dpo\ngate g1 risk low grant bot\nreserve g1 by dpo\ncord bot -> g1\ncord g1 -> master\n"})
    M.workspace_workflow(op="patch_apply", params={"folder_context": ws, "actor": "f",
        "netlist": "actor bot\nhuman ciso role ciso\ngate g1 risk low grant bot\nreserve g1 by ciso\ncord bot -> g1\ncord g1 -> master\n"})
    acts = {a.get("reserved_to") for a in get_use_case(ws, "g1", log_root=M._log_root())["reserved_acts"]}
    assert "dpo" in acts and "ciso" in acts, f"a prior reservation was dropped: {acts}"


def test_no_reservation_from_fingerprint_and_policy_is_sticky():
    """G2 (2026-06-25): reservations now authored/ingested, not from a legal enum.

    There is no legal catalog, so a fingerprint (changed or not) NEVER introduces a
    reservation on its own. Only an AUTHORED policy reservation does — and that
    policy reservation is sticky across a re-registration that carries it forward
    (mirroring sticky prohibitions / no silent drop)."""
    from rvnd.use_case import register_use_case, get_use_case
    ws = _ws()
    # a fingerprint that ONCE drove a legal reservation now derives none
    register_use_case(ws, use_case_id="g", name="g", fingerprint={"issue_type": "automated_decision"},
                      risk="high", allowed_agents=[], actor="f", log_root=M._log_root())
    assert not get_use_case(ws, "g", log_root=M._log_root())["reserved_acts"], \
        "a fingerprint must NOT auto-derive a reservation — there is no legal catalog"

    # author a POLICY reservation, then re-register (fingerprint changed) carrying it
    register_use_case(ws, use_case_id="g", name="g", fingerprint={"issue_type": "automated_decision"},
                      risk="high", allowed_agents=[], actor="f", log_root=M._log_root(),
                      policy_reservations={"g": {"reserved_to": "dpo", "act_type": "review",
                                                 "source": "company policy 4.2"}})
    authored = get_use_case(ws, "g", log_root=M._log_root())["reserved_acts"]
    assert any(a.get("basis_kind") == "policy" for a in authored), "expected the authored policy reservation"
    register_use_case(ws, use_case_id="g", name="g", fingerprint={"issue_type": "liability_cap"},
                      risk="high", allowed_agents=[], actor="f", carry_reserved=authored, log_root=M._log_root())
    new = get_use_case(ws, "g", log_root=M._log_root())["reserved_acts"]
    assert any(a.get("basis_kind") == "policy" and a.get("reserved_to") == "dpo" for a in new), \
        "a POLICY reservation must be sticky across a fingerprint change (no silent drop)"
    # and still: nothing legal materialised from the fingerprint change
    assert not any(a.get("basis_kind") == "law" for a in new), "no legal reservation may exist under G2"


def test_obligation_for_unknown_gate_is_rejected_fail_closed():
    # Loomground v0.8 (SYNTAX §3): an obligation must attach to a DECLARED gate.
    # This used to apply with a 'not applied' warning (surfaced, not dropped);
    # the language now makes the graph ill-formed at apply, so patch_apply
    # refuses outright — still no silent drop, one step stricter.
    ws = _ws()
    net = (_STRUCT + "obligation ai-disclosure on ghost\n")
    r = M.workspace_workflow(op="patch_apply", params={"folder_context": ws, "actor": "f", "netlist": net})
    assert not r["ok"]
    assert any("ghost" in e for e in r.get("errors", [])), r.get("errors")
