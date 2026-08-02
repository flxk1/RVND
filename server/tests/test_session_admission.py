# SPDX-License-Identifier: AGPL-3.0-only
"""The live session gate binds registry, lane, policy, folder, uid and status."""
import os

import pytest

from workspaces.governance_lane import GovernanceLane, register_lane
from workspaces.parties import register_party, set_party_status
from workspaces.operations import operate
from workspaces.session_admission import governance_open, verify_operation_session
from workspaces.session_capability import CapabilityError, CapabilityVerifier
from workspaces import signing
from workspaces.mcp_serving import clear_request_principal, set_request_principal
from workspaces.use_case import register_use_case

pytestmark = [
    pytest.mark.live_session_admission,
    pytest.mark.live_egress_capability,
]


@pytest.fixture()
def authority(tmp_path, monkeypatch):
    folder = tmp_path / "workspace"
    log = tmp_path / "log"
    keys = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keys))
    register_party(str(folder), "bot", "agent", grade="L2", log_root=str(log))
    register_lane(folder, GovernanceLane(
        lane_id="lane-bot",
        agent="bot",
        max_grade="L2",
        action_classes=("classify",),
        folder=str(folder),
        policy_fingerprint="sha256:approved",
        approved_by="controller",
        rationale="bounded classifier",
    ), log_root=log)
    set_request_principal("bot", "bot")
    yield str(folder), str(log)
    clear_request_principal()


def test_open_and_verify_live_authority(authority):
    folder, log = authority
    opened = governance_open(
        folder, party="bot", policy_fingerprint="sha256:approved", log_root=log
    )
    claims = verify_operation_session(
        folder,
        agent_id="bot",
        capability_token=opened["capability_token"],
        log_root=log,
    )
    assert claims.party == "bot"
    assert claims.uid == os.getuid()
    assert opened["capability_token"] not in str(opened["claims"])


def test_missing_unknown_and_wrong_policy_are_denied(authority):
    folder, log = authority
    with pytest.raises(CapabilityError, match="required"):
        verify_operation_session(
            folder, agent_id="bot", capability_token="", log_root=log
        )
    set_request_principal("ghost", "ghost")
    with pytest.raises(CapabilityError, match="registered"):
        governance_open(
            folder, party="ghost", policy_fingerprint="sha256:approved",
            log_root=log,
        )
    set_request_principal("bot", "bot")
    with pytest.raises(CapabilityError, match="does not match"):
        governance_open(
            folder, party="bot", policy_fingerprint="sha256:wrong", log_root=log
        )


def test_kill_switch_invalidates_already_minted_session(authority):
    folder, log = authority
    token = governance_open(
        folder, party="bot", policy_fingerprint="sha256:approved", log_root=log
    )["capability_token"]
    set_party_status(folder, "bot", "killed", log_root=log)
    with pytest.raises(CapabilityError, match="killed"):
        verify_operation_session(
            folder, agent_id="bot", capability_token=token, log_root=log
        )


def test_verifier_rejects_wrong_folder_uid_and_revoked_nonce(authority):
    folder, log = authority
    opened = governance_open(
        folder, party="bot", policy_fingerprint="sha256:approved", log_root=log
    )
    verifier = CapabilityVerifier(signing.identity_public_key_or_none())
    with pytest.raises(CapabilityError, match="folder"):
        verifier.verify(opened["capability_token"], expected_folder="/elsewhere")
    with pytest.raises(CapabilityError, match="uid"):
        verifier.verify(opened["capability_token"], expected_uid=os.getuid() + 1)
    verifier.revoke(opened["claims"]["nonce"])
    with pytest.raises(CapabilityError, match="revoked"):
        verifier.verify(opened["capability_token"])


def test_open_requires_verified_request_principal(authority):
    folder, log = authority
    clear_request_principal()
    with pytest.raises(CapabilityError, match="verified request principal"):
        governance_open(
            folder, party="bot", policy_fingerprint="sha256:approved",
            log_root=log,
        )


def test_open_accepts_authenticated_loopback_session(authority):
    folder, log = authority
    set_request_principal("bot", "bot", rung="loopback-session")
    opened = governance_open(
        folder, party="bot", policy_fingerprint="sha256:approved",
        log_root=log,
    )
    assert opened["ok"] is True
    assert opened["claims"]["party"] == "bot"


def test_operate_requires_and_accepts_real_session(authority):
    folder, log = authority
    register_use_case(
        folder,
        use_case_id="classify",
        name="classify",
        fingerprint={"domain": "test"},
        risk="low",
        allowed_agents=["bot"],
        actor="controller",
        log_root=log,
    )
    refused = operate(
        folder,
        use_case_id="classify",
        agent_id="bot",
        issues=[],
        now_epoch=1,
        journal=False,
        log_root=log,
    )
    token = governance_open(
        folder, party="bot", policy_fingerprint="sha256:approved", log_root=log
    )["capability_token"]
    admitted = operate(
        folder,
        use_case_id="classify",
        agent_id="bot",
        issues=[],
        now_epoch=1,
        capability_token=token,
        journal=False,
        log_root=log,
    )
    assert refused["final"] == "refused"
    assert "session capability required" in refused["reason"]
    assert admitted["final"] != "refused"
    set_request_principal("mallory", "mallory")
    with pytest.raises(CapabilityError, match="does not match"):
        governance_open(
            folder, party="bot", policy_fingerprint="sha256:approved",
            log_root=log,
        )
