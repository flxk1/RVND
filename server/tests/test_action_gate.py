# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Action-gating verdict substrate — fast-path, GO/CONDITIONAL/NO-GO,
standing-approval-as-edge, promotion-gate-as-graph-query."""

from datetime import date
import workspaces.action_gate as ag
from workspaces.action_gate import ActionRequest, StandingApproval, Verdict


def test_benign_action_fast_path_go():
    d = ag.gate(ActionRequest("agent-1", "read_folder", "L2"))
    assert d.verdict is Verdict.GO and d.fast_path
    assert d.audit_triple["subject"] == "agent-1"


def test_l0_interactive_benign_is_conditional():
    d = ag.gate(ActionRequest("agent-1", "read_folder", "L0"))
    assert d.verdict is Verdict.CONDITIONAL and d.fast_path


def test_high_risk_below_required_grade_is_no_go():
    # personal-data needs grade ≥ 2; at L1 → NO-GO.
    d = ag.gate(ActionRequest("a", "export", "L1", footprint=("personal-data",)))
    assert d.verdict is Verdict.NO_GO
    assert "below required" in d.reason


def test_high_risk_sufficient_grade_no_approval_is_conditional():
    d = ag.gate(ActionRequest("a", "export", "L2", footprint=("personal-data",)))
    assert d.verdict is Verdict.CONDITIONAL
    assert not d.fast_path


def test_standing_approval_edge_short_circuits_to_go():
    appr = StandingApproval("a", "export", obligation_pair="gdpr-art6-1",
                            until="2099-01-01")
    d = ag.gate(ActionRequest("a", "export", "L2", footprint=("personal-data",)),
                standing_approvals=[appr], as_of=date(2024, 1, 1))
    assert d.verdict is Verdict.GO and d.fast_path
    assert d.obligation_pairs == ["gdpr-art6-1"]
    # The approval is representable as a graph edge.
    assert appr.to_edge()["predicate"] == "approved-under"


def test_expired_standing_approval_does_not_apply():
    appr = StandingApproval("a", "export", "gdpr-art6-1", until="2020-01-01")
    d = ag.gate(ActionRequest("a", "export", "L2", footprint=("personal-data",)),
                standing_approvals=[appr], as_of=date(2024, 1, 1))
    assert d.verdict is Verdict.CONDITIONAL   # falls back to sign-off


def test_restrictive_posture_raises_the_bar():
    # personal-data normally needs L2; restrictive posture pushes it to L3.
    req = ActionRequest("a", "export", "L2", footprint=("personal-data",))
    assert ag.gate(req, posture="restrictive").verdict is Verdict.NO_GO
    assert ag.gate(req, posture="balanced").verdict is Verdict.CONDITIONAL


def test_prohibited_action_is_no_go_regardless_of_grade():
    d = ag.gate(ActionRequest("a", "delete_audit_log", "L4"),
                prohibited_actions=["delete_audit_log"])
    assert d.verdict is Verdict.NO_GO


def test_promotion_gate_blocks_on_open_critical():
    history = [
        {"subject": "a", "verdict": "GO", "object": "x"},
        {"subject": "a", "verdict": "NO-GO", "object": "export"},   # open critical
    ]
    d = ag.promotion_gate("a", "L2", "L3", history)
    assert d.verdict is Verdict.NO_GO and "open Critical" in d.reason


def test_promotion_gate_clear_when_no_open_criticals():
    history = [
        {"subject": "a", "verdict": "GO", "object": "x"},
        {"subject": "a", "verdict": "CONDITIONAL", "object": "y", "signed_off": True},
    ]
    d = ag.promotion_gate("a", "L2", "L3", history)
    assert d.verdict is Verdict.GO


def test_promotion_gate_rejects_non_promotion():
    d = ag.promotion_gate("a", "L3", "L2", [])
    assert d.verdict is Verdict.NO_GO and "not a promotion" in d.reason
