# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for contract_reviews — persistence + approvals + traffic-light derivation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workspaces.contracts import reviews as cr


@pytest.fixture
def workspace(tmp_path: Path):
    """Each test gets a fresh log root."""
    wsp = tmp_path / "wsp"
    wsp.mkdir()
    log_root = tmp_path / "logs"
    log_root.mkdir()
    return wsp, log_root


# ---------------------------------------------------------------------------
# Traffic-light derivation
# ---------------------------------------------------------------------------

def test_traffic_light_approve_no_findings():
    assert cr.derive_traffic_light("Approve", []) == cr.TRAFFIC_GREEN


def test_traffic_light_block_decision():
    assert cr.derive_traffic_light("Block", []) == cr.TRAFFIC_RED


def test_traffic_light_critical_finding_overrides_decision():
    findings = [{"severity": "Critical"}]
    assert cr.derive_traffic_light("Approve", findings) == cr.TRAFFIC_RED


def test_traffic_light_high_finding_amber():
    findings = [{"severity": "High"}]
    assert cr.derive_traffic_light("Approve", findings) == cr.TRAFFIC_AMBER


def test_traffic_light_approve_with_conditions_amber():
    assert cr.derive_traffic_light("Approve with Conditions", []) == cr.TRAFFIC_AMBER


def test_traffic_light_pending_approvals_amber():
    assert cr.derive_traffic_light("Approve", [], approvals_pending=1) == cr.TRAFFIC_AMBER


def test_traffic_light_rejected_approval_red():
    assert cr.derive_traffic_light("Approve", [], approvals_rejected=1) == cr.TRAFFIC_RED


def test_traffic_light_grey_when_no_decision():
    assert cr.derive_traffic_light("", []) == cr.TRAFFIC_GREY


# ---------------------------------------------------------------------------
# Contract review roundtrip
# ---------------------------------------------------------------------------

def test_record_and_list_review(workspace):
    wsp, lr = workspace
    res = cr.record_contract_review(
        wsp,
        contract_id="ACME-2026-SYNC-0001",
        decision="Approve with Conditions",
        findings_json={
            "sections": {
                "3_consolidated_findings": [
                    {"severity": "High",   "agent": "agent-09", "issue": "Liability cap low"},
                    {"severity": "Medium", "agent": "agent-15", "issue": "Sync term unclear"},
                ],
            },
        },
        jurisdiction_anchors=["EU", "US"],
        audience_side="label-indie",
        contract_type="sync-licence",
        total_value_eur=42500.0,
        log_root=lr,
    )
    assert res["traffic_light"] == cr.TRAFFIC_AMBER  # High finding present
    assert res["contract_id"] == "ACME-2026-SYNC-0001"

    rows = cr.list_contract_reviews(wsp, log_root=lr)
    assert len(rows) == 1
    r = rows[0]
    assert r["contract_id"] == "ACME-2026-SYNC-0001"
    assert r["decision"] == "Approve with Conditions"
    assert r["traffic_light"] == cr.TRAFFIC_AMBER
    assert r["audience_side"] == "label-indie"
    assert "EU" in r["jurisdictions"]
    assert r["findings_count"] == 2
    assert r["findings_by_severity"]["High"] == 1
    assert r["findings_by_severity"]["Medium"] == 1


def test_latest_review_per_contract(workspace):
    wsp, lr = workspace
    cr.record_contract_review(wsp, contract_id="C-1", decision="Block",
                               findings_json=[{"severity": "Critical"}], log_root=lr)
    cr.record_contract_review(wsp, contract_id="C-1", decision="Approve",
                               findings_json=[], log_root=lr)
    rows = cr.list_contract_reviews(wsp, log_root=lr)
    assert len(rows) == 1
    assert rows[0]["decision"] == "Approve"
    assert rows[0]["traffic_light"] == cr.TRAFFIC_GREEN


def test_filter_by_jurisdiction(workspace):
    wsp, lr = workspace
    cr.record_contract_review(wsp, contract_id="C-EU", decision="Approve",
                               jurisdiction_anchors=["EU"], log_root=lr)
    cr.record_contract_review(wsp, contract_id="C-US", decision="Approve",
                               jurisdiction_anchors=["US"], log_root=lr)
    cr.record_contract_review(wsp, contract_id="C-BOTH", decision="Approve",
                               jurisdiction_anchors=["EU", "US"], log_root=lr)
    rows = cr.list_contract_reviews(wsp, filters={"jurisdiction": "US"}, log_root=lr)
    ids = {r["contract_id"] for r in rows}
    assert ids == {"C-US", "C-BOTH"}


def test_filter_by_min_severity(workspace):
    wsp, lr = workspace
    cr.record_contract_review(wsp, contract_id="A", decision="Approve",
                               findings_json=[{"severity": "Low"}], log_root=lr)
    cr.record_contract_review(wsp, contract_id="B", decision="Approve",
                               findings_json=[{"severity": "High"}], log_root=lr)
    cr.record_contract_review(wsp, contract_id="C", decision="Block",
                               findings_json=[{"severity": "Critical"}], log_root=lr)
    rows = cr.list_contract_reviews(wsp, filters={"min_severity": "High"}, log_root=lr)
    ids = {r["contract_id"] for r in rows}
    assert ids == {"B", "C"}


def test_filter_by_traffic_light(workspace):
    wsp, lr = workspace
    cr.record_contract_review(wsp, contract_id="G", decision="Approve", log_root=lr)
    cr.record_contract_review(wsp, contract_id="A", decision="Approve with Conditions",
                               log_root=lr)
    cr.record_contract_review(wsp, contract_id="R", decision="Block",
                               findings_json=[{"severity": "Critical"}], log_root=lr)
    greens = cr.list_contract_reviews(wsp, filters={"traffic_light": "green"}, log_root=lr)
    assert {r["contract_id"] for r in greens} == {"G"}
    reds = cr.list_contract_reviews(wsp, filters={"traffic_light": "red"}, log_root=lr)
    assert {r["contract_id"] for r in reds} == {"R"}


def test_large_findings_spill_to_sibling_file(workspace):
    wsp, lr = workspace
    big_findings = {
        "sections": {
            "3_consolidated_findings": [
                {"severity": "Medium", "agent": f"agent-{i}",
                 "issue": "x" * 2000, "recommendation": "y" * 2000}
                for i in range(20)
            ]
        }
    }
    res = cr.record_contract_review(wsp, contract_id="BIG",
                                     decision="Approve with Conditions",
                                     findings_json=big_findings, log_root=lr)
    # Spilled to sibling file
    assert res.get("spill_path")
    assert Path(res["spill_path"]).exists()
    rows = cr.list_contract_reviews(wsp, include_findings=True, log_root=lr)
    assert rows
    inflated = rows[0]["findings_json"]
    assert len(inflated["sections"]["3_consolidated_findings"]) == 20


# ---------------------------------------------------------------------------
# Approval queue lifecycle
# ---------------------------------------------------------------------------

def test_request_approval_creates_pending(workspace):
    wsp, lr = workspace
    # Relative-future deadline so the test never goes stale against the clock.
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    res = cr.request_contract_approval(wsp,
                                        contract_id="ACME-001",
                                        signers=["alice\x40example.com", "bob\x40example.com"],
                                        deadline=future,
                                        requested_by="alex", log_root=lr)
    assert res["state"] == cr.APPROVAL_PENDING
    rows = cr.list_contract_approvals(wsp, log_root=lr)
    assert len(rows) == 1
    assert rows[0]["overall_state"] == cr.APPROVAL_PENDING


def test_signoff_partial_keeps_pending(workspace):
    wsp, lr = workspace
    req = cr.request_contract_approval(wsp, contract_id="X",
                                        signers=["a", "b"], log_root=lr)
    cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                  signer="a", decision="approved", log_root=lr)
    rows = cr.list_contract_approvals(wsp, log_root=lr)
    assert rows[0]["overall_state"] == cr.APPROVAL_PENDING
    assert rows[0]["signer_decisions"]["a"]["decision"] == "approved"


def test_offroster_signer_cannot_sign(workspace):
    # D12/M6 — authority: only a signer the approval was requested from may sign
    # off. An off-roster caller is rejected (fail-closed), so it cannot self-approve.
    wsp, lr = workspace
    req = cr.request_contract_approval(wsp, contract_id="X",
                                        signers=["a", "b"], log_root=lr)
    with pytest.raises(ValueError):
        cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                      signer="mallory", decision="approved", log_root=lr)
    # the approval stays pending — no unauthorized sign-off landed.
    rows = cr.list_contract_approvals(wsp, log_root=lr)
    assert rows[0]["overall_state"] == cr.APPROVAL_PENDING


def test_signoff_all_approved_flips_to_approved(workspace):
    wsp, lr = workspace
    req = cr.request_contract_approval(wsp, contract_id="X",
                                        signers=["a", "b"], log_root=lr)
    cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                  signer="a", decision="approved", log_root=lr)
    final = cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                          signer="b", decision="approved", log_root=lr)
    assert final["overall_state"] == cr.APPROVAL_APPROVED
    rows = cr.list_contract_approvals(wsp, state=cr.APPROVAL_APPROVED, log_root=lr)
    assert len(rows) == 1


def test_single_rejection_overrides(workspace):
    wsp, lr = workspace
    req = cr.request_contract_approval(wsp, contract_id="X",
                                        signers=["a", "b"], log_root=lr)
    cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                  signer="a", decision="approved", log_root=lr)
    final = cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                          signer="b", decision="rejected",
                                          comment="cap too low", log_root=lr)
    assert final["overall_state"] == cr.APPROVAL_REJECTED
    rows = cr.list_contract_approvals(wsp, state=cr.APPROVAL_REJECTED, log_root=lr)
    assert len(rows) == 1
    assert rows[0]["signer_decisions"]["b"]["comment"] == "cap too low"


def test_unknown_decision_rejected(workspace):
    wsp, lr = workspace
    req = cr.request_contract_approval(wsp, contract_id="X",
                                        signers=["a"], log_root=lr)
    with pytest.raises(ValueError):
        cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                      signer="a", decision="maybe", log_root=lr)


def test_record_decision_unknown_approval_rejected(workspace):
    wsp, lr = workspace
    with pytest.raises(ValueError):
        cr.record_contract_approval(wsp, approval_id="not-an-id",
                                      signer="a", decision="approved", log_root=lr)


def test_request_without_signers_rejected(workspace):
    wsp, lr = workspace
    with pytest.raises(ValueError):
        cr.request_contract_approval(wsp, contract_id="X", signers=[], log_root=lr)


def test_expired_approval_state(workspace):
    wsp, lr = workspace
    # Deadline in the past
    cr.request_contract_approval(wsp, contract_id="X", signers=["a"],
                                   deadline="2020-01-01T00:00:00Z", log_root=lr)
    rows = cr.list_contract_approvals(wsp, log_root=lr)
    assert rows[0]["overall_state"] == cr.APPROVAL_EXPIRED


def test_filter_approvals_by_contract_id(workspace):
    wsp, lr = workspace
    cr.request_contract_approval(wsp, contract_id="A", signers=["x"], log_root=lr)
    cr.request_contract_approval(wsp, contract_id="B", signers=["x"], log_root=lr)
    rows = cr.list_contract_approvals(wsp, contract_id="A", log_root=lr)
    assert len(rows) == 1
    assert rows[0]["contract_id"] == "A"


# ---------------------------------------------------------------------------
# Robustness: replay fail-safe + harmonized signer_decisions shape
# ---------------------------------------------------------------------------

def test_list_approvals_replay_error_surfaces_not_empty(workspace, monkeypatch):
    """A replay that raises must surface an error, never a silently-empty queue.

    Fail-OPEN here would read a pending approval as "nothing pending". We
    record a real pending approval, then make replay() blow up mid-stream and
    assert the call propagates (fail-closed) instead of returning [].
    """
    wsp, lr = workspace
    cr.request_contract_approval(wsp, contract_id="X", signers=["a"], log_root=lr)

    from workspaces.mutation_log import MutationLog

    def _boom(self):
        raise OSError("simulated decrypt/IO failure mid-replay")
        yield  # pragma: no cover  (makes this a generator)

    monkeypatch.setattr(MutationLog, "replay", _boom)
    with pytest.raises(RuntimeError):
        cr.list_contract_approvals(wsp, log_root=lr)


def test_record_approval_signer_decisions_nested_shape(workspace):
    """record_contract_approval must emit the SAME nested signer_decisions
    shape as list_contract_approvals (decision/comment/signed_at), so the UI
    can read ``d.decision`` uniformly from either source."""
    wsp, lr = workspace
    req = cr.request_contract_approval(wsp, contract_id="X",
                                        signers=["a", "b"], log_root=lr)
    res = cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                       signer="a", decision="approved",
                                       comment="looks fine", log_root=lr)
    d = res["signer_decisions"]["a"]
    assert isinstance(d, dict)
    assert d["decision"] == "approved"
    assert d["comment"] == "looks fine"
    assert "signed_at" in d

    # Same shape as the list view for the same approval.
    listed = cr.list_contract_approvals(wsp, log_root=lr)[0]["signer_decisions"]["a"]
    assert set(listed.keys()) == set(d.keys())


def test_replayed_unknown_decision_is_skipped(workspace):
    """A forged signoff with an arbitrary decision string must not count as an
    approval — the overall state stays pending, not approved."""
    wsp, lr = workspace
    req = cr.request_contract_approval(wsp, contract_id="X",
                                        signers=["a"], log_root=lr)
    # Forge a signoff event with a bogus decision directly on the log.
    from workspaces.mutation_log import LogEvent, MutationLog
    log = MutationLog(wsp, log_root=lr)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(wsp).expanduser().resolve()),
        pair_id=cr.PAIR_CONTRACT_APPROVAL,
        lifecycle_state="signoff",
        channel="system",
        actor="a",
        extra={
            "approval_id": req["approval_id"],
            "contract_id": "X",
            "signer": "a",
            "decision": "totally-bogus",
            "comment": "",
            "signed_at": "2026-01-01T00:00:00Z",
        },
    ))
    rows = cr.list_contract_approvals(wsp, log_root=lr)
    assert rows[0]["overall_state"] == cr.APPROVAL_PENDING
    assert "a" not in rows[0]["signer_decisions"]
