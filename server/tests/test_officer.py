# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Officer policy binding for governed oversight.

The tests cover scope, strictest-wins composition, reserved-act routing,
action-gate integration, monotonicity, and the MCP preview operation.
"""
from __future__ import annotations

import os

from rvnd import officer as OF

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

DPO = OF.Officer(officer_id="dpo-officer", name="Data Protection Officer",
                 oversees=["gate:hiring", "gate:profiling"], control_form="single_approver",
                 escalation_party="dpo", policy=["ai-act-art-26", "gdpr-art-22"])


def test_governs_only_its_scope():
    assert DPO.governs("gate:hiring") and not DPO.governs("gate:logging")


def test_tightens_a_loose_gate():
    o = OF.oversight_for(DPO, gate_floor="auto")     # gate would auto-run
    assert "pre_approval" in o["guarantees"]         # officer imposes approval
    assert o["tightened"] is True and o["escalation_party"] == "dpo"


def test_cannot_loosen_a_stricter_gate():
    o = OF.oversight_for(DPO, gate_floor="two_approvers")   # gate stricter than the officer
    assert "two_approvers" in o["guarantees"]        # floor holds — officer can't loosen it
    assert o["control_form"] != "single_approver"    # not reduced to the officer's weaker form


def test_reserved_act_routes_to_human():
    r = OF.route_reserved(DPO, {"action": "approve automated rejection", "gate": "gate:hiring"})
    assert r["to"] == "dpo" and r["decided_by"] == "human" and r["auto"] is False
    assert r["via"] == "oversight_dispatch"        # delegates delivery to the existing stack


def test_officer_escalates_a_gate_verdict():
    from rvnd import action_gate as AG
    req = AG.ActionRequest(agent="ai_system", action_class="hiring", autonomy_grade="L2")
    assert AG.gate(req).verdict.value == "GO"                         # base: benign action goes
    d = AG.gate(req, officers=[OF.Officer("dpo", "DPO", oversees=["hiring"],
                                          control_form="block", escalation_party="dpo")])
    assert d.verdict.value == "NO-GO"                                 # officer tightened it
    assert d.audit_triple["officers"]["escalated"] is True
    assert d.audit_triple["officers"]["escalation_party"] == "dpo"   # the human rides in the audit
    c = AG.gate(req, officers=[OF.Officer("dpo", "DPO", oversees=["hiring"], control_form="single_approver")])
    assert c.verdict.value == "CONDITIONAL"                           # approver form → sign-off


def test_officer_scoped_and_monotone():
    from rvnd import action_gate as AG
    req = AG.ActionRequest(agent="ai_system", action_class="hiring", autonomy_grade="L2")
    # an officer that does not govern this gate changes nothing
    assert AG.gate(req, officers=[OF.Officer("x", "X", oversees=["other"], control_form="block")]).verdict.value == "GO"
    # an 'auto' control form does not escalate
    assert AG.gate(req, officers=[OF.Officer("a", "A", oversees=["hiring"], control_form="auto")]).verdict.value == "GO"
    # strictest across officers wins — block dominates approver (tighten-only, never relax)
    d = AG.gate(req, officers=[OF.Officer("a", "A", oversees=["hiring"], control_form="single_approver"),
                               OF.Officer("b", "B", oversees=["hiring"], control_form="block")])
    assert d.verdict.value == "NO-GO"


def test_officer_op_previews_oversight_and_is_discoverable():
    # the mechanism is already wired+tested into action_gate (O5/O6); the op makes an officer
    # PREVIEW reachable via the dispatch. Production auto-loading of
    # registered officers needs an officer store (persistence) and is separate.
    from rvnd import mcp_server as M
    r = M.workspace_workflow("officer", {
        "folder_context": "", "officer_id": "dpo-officer", "oversees": ["gate:hiring"],
        "control_form": "single_approver", "escalation_party": "dpo", "gate_floor": "auto"})
    assert r["officer"]["officer_id"] == "dpo-officer"
    assert r["oversight"]["tightened"] is True and r["oversight"]["escalation_party"] == "dpo"
    r2 = M.workspace_workflow("officer", {
        "folder_context": "", "oversees": ["gate:hiring"], "escalation_party": "dpo",
        "act": {"action": "approve automated rejection", "gate": "gate:hiring"}})
    assert r2["reserved_route"]["to"] == "dpo" and r2["reserved_route"]["via"] == "oversight_dispatch"
    ops = {row["op"] for row in M.workspace_workflow("ops", {"folder_context": ""})["ops"]}
    assert "officer" in ops
