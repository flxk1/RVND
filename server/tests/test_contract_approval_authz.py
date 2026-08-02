# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""#58 — actor↔signer authorization on record_contract_approval.

Decided rule (opt-in per workspace via access control; fail-closed when ON):
  - OFF (default): unchanged free-text local-first path (only the roster check).
  - ON: the signer must be a registered, ACTIVE *human*; the recording caller
    (actor) must be that signer OR a party the signer has delegated to. Competence
    is NOT required on this path.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from workspaces.contracts import reviews as cr
from workspaces import parties as pt
from workspaces import approvals as ap
from workspaces.policy import set_access_control


@pytest.fixture
def workspace(tmp_path: Path):
    wsp = tmp_path / "wsp"; wsp.mkdir()
    lr = tmp_path / "logs"; lr.mkdir()
    return wsp, lr


def _req(wsp, lr, signers):
    return cr.request_contract_approval(wsp, contract_id="X", signers=signers, log_root=lr)


# --- OFF (default): behaviour unchanged --------------------------------------

def test_off_default_freetext_unchanged(workspace):
    """Access control OFF (the default) keeps the local-first path: a rostered
    free-text signer signs, no party registration needed, actor need not match."""
    wsp, lr = workspace
    req = _req(wsp, lr, ["a", "b"])
    res = cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                      signer="a", decision="approved",
                                      actor="whoever", log_root=lr)
    assert res["signer_decisions"]["a"]["decision"] == "approved"


# --- ON: signer must be a registered, active human ---------------------------

def _enable(wsp):
    set_access_control(wsp, True)

def test_on_self_human_active_granted(workspace):
    wsp, lr = workspace
    _enable(wsp)
    pt.register_party(str(wsp), "alice", "human", log_root=str(lr))
    req = _req(wsp, lr, ["alice"])
    res = cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                      signer="alice", decision="approved",
                                      actor="alice", log_root=lr)
    assert res["overall_state"] == cr.APPROVAL_APPROVED


def test_on_unregistered_signer_rejected(workspace):
    wsp, lr = workspace
    _enable(wsp)
    req = _req(wsp, lr, ["ghost"])
    with pytest.raises(ValueError):
        cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                    signer="ghost", decision="approved",
                                    actor="ghost", log_root=lr)
    assert cr.list_contract_approvals(wsp, log_root=lr)[0]["overall_state"] == cr.APPROVAL_PENDING


def test_on_agent_signer_rejected(workspace):
    """An AI agent may never sign off a reserved/human-required action."""
    wsp, lr = workspace
    _enable(wsp)
    pt.register_party(str(wsp), "botx", "agent", log_root=str(lr))
    req = _req(wsp, lr, ["botx"])
    with pytest.raises(ValueError):
        cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                    signer="botx", decision="approved",
                                    actor="botx", log_root=lr)


def test_on_suspended_signer_rejected(workspace):
    wsp, lr = workspace
    _enable(wsp)
    pt.register_party(str(wsp), "alice", "human", log_root=str(lr))
    pt.set_party_status(str(wsp), "alice", "suspended", log_root=str(lr))
    req = _req(wsp, lr, ["alice"])
    with pytest.raises(ValueError):
        cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                    signer="alice", decision="approved",
                                    actor="alice", log_root=lr)


# --- ON: actor↔signer binding with delegation --------------------------------

def test_on_actor_not_signer_no_delegation_rejected(workspace):
    wsp, lr = workspace
    _enable(wsp)
    pt.register_party(str(wsp), "alice", "human", log_root=str(lr))
    req = _req(wsp, lr, ["alice"])
    with pytest.raises(ValueError):
        cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                    signer="alice", decision="approved",
                                    actor="bob", log_root=lr)


def test_on_delegate_granted(workspace):
    """Bob may sign for Alice once Alice has delegated signing authority to Bob
    (a dedicated signing delegation — no competence required, the decided rule)."""
    wsp, lr = workspace
    _enable(wsp)
    pt.register_party(str(wsp), "alice", "human", log_root=str(lr))
    pt.register_party(str(wsp), "bob", "human", log_root=str(lr))
    ap.delegate_signing(str(wsp), from_party="alice", to_party="bob",
                        actor="alice", now=time.time(), log_root=str(lr))
    req = _req(wsp, lr, ["alice"])
    res = cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                      signer="alice", decision="approved",
                                      actor="bob", log_root=lr)
    assert res["overall_state"] == cr.APPROVAL_APPROVED


def test_corrupt_policy_fails_closed(workspace):
    """A PRESENT-but-corrupt policy file must fail CLOSED (access control ON), so a
    corrupt policy can't silently drop the gate. (Wave-3 loop finding.)"""
    from workspaces.authorization import access_control_on
    from workspaces.policy import POLICY_FILENAME
    wsp, lr = workspace
    (wsp / POLICY_FILENAME).write_text("{ this is not valid json ", encoding="utf-8")
    assert access_control_on(str(wsp)) is True
    # and the gate engages: an unregistered signer is refused, not silently signed
    req = _req(wsp, lr, ["ghost"])
    with pytest.raises(ValueError):
        cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                    signer="ghost", decision="approved",
                                    actor="ghost", log_root=lr)


def test_absent_policy_is_off(workspace):
    """An ABSENT policy is the legitimate local-first default — access control OFF."""
    from workspaces.authorization import access_control_on
    wsp, lr = workspace
    assert access_control_on(str(wsp)) is False


def test_on_delegate_to_agent_refused(workspace):
    """Signing authority can only be delegated to a registered human."""
    wsp, lr = workspace
    _enable(wsp)
    pt.register_party(str(wsp), "alice", "human", log_root=str(lr))
    pt.register_party(str(wsp), "botx", "agent", log_root=str(lr))
    with pytest.raises(ValueError):
        ap.delegate_signing(str(wsp), from_party="alice", to_party="botx",
                            actor="alice", now=time.time(), log_root=str(lr))


def test_on_empty_actor_rejected(workspace):
    """ON: a blank actor is unattributable and must not silently self-sign."""
    wsp, lr = workspace
    _enable(wsp)
    pt.register_party(str(wsp), "alice", "human", log_root=str(lr))
    req = _req(wsp, lr, ["alice"])
    with pytest.raises(ValueError):
        cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                    signer="alice", decision="approved",
                                    actor="", log_root=lr)


def test_on_suspended_delegate_rejected(workspace):
    """A delegate suspended AFTER the delegation can no longer sign — the delegate's
    current status is re-checked at sign time (fail-closed)."""
    wsp, lr = workspace
    _enable(wsp)
    pt.register_party(str(wsp), "alice", "human", log_root=str(lr))
    pt.register_party(str(wsp), "bob", "human", log_root=str(lr))
    ap.delegate_signing(str(wsp), from_party="alice", to_party="bob",
                        actor="alice", now=time.time(), log_root=str(lr))
    pt.set_party_status(str(wsp), "bob", "suspended", log_root=str(lr))
    req = _req(wsp, lr, ["alice"])
    with pytest.raises(ValueError):
        cr.record_contract_approval(wsp, approval_id=req["approval_id"],
                                    signer="alice", decision="approved",
                                    actor="bob", log_root=lr)


def test_delegate_signing_to_suspended_refused(workspace):
    """Signing authority cannot be delegated to a suspended human."""
    wsp, lr = workspace
    _enable(wsp)
    pt.register_party(str(wsp), "alice", "human", log_root=str(lr))
    pt.register_party(str(wsp), "bob", "human", log_root=str(lr))
    pt.set_party_status(str(wsp), "bob", "suspended", log_root=str(lr))
    with pytest.raises(ValueError):
        ap.delegate_signing(str(wsp), from_party="alice", to_party="bob",
                            actor="alice", now=time.time(), log_root=str(lr))
