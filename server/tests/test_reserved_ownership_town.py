# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""T-own — reserved-to-a-skilled-agent ownership is ENFORCED, not asserted.

A task reserved to agent-A must refuse a second agent (agent-B) at four
INDEPENDENT layers, each proven against the real module (no enforcement mocks),
and the reserved decision must clear only through a competence-matched quorum:

  L1  use-case allowlist  — agent_permitted(uc, "agent-B") is False
  L2  signed session      — B cannot mint A's capability (principal != party)
                            and cannot redeem A's token (claims.party != agent)
  L3  run lease           — B's enqueue on A's held (folder, workflow) is refused
  L4  signed chain        — a decision NOT signed by the workspace's identity key
                            (B forging with a foreign key) fails verify_chain
  Q   competence quorum   — an out-of-set (under-competent) hand does not clear
                            the decision; a competence-matched quorum does

Honest scope of the four layers (real modules, verified):
  * L4 is single-host-identity signing, not per-party keying — the chain has no
    "A's key vs B's key". So B is modelled as a foreign key (an attacker who
    lacks the workspace identity key); the chain rejects it. That is the real
    tamper-evidence the ownership claim rests on.
  * A competence-mismatched vote is REFUSED by not counting at resolve time
    (state stays `pending`), not by raising — resolve_approval is a pure
    projection; decide_approval records every cast vote.
  * The refusals are fail-closed (raise / return False) at the gate; the signed
    CLEARING (the approval votes) is what lands on the chain as signed events.

The live session gate is stubbed by conftest unless opted in, so this module
declares live_session_admission (see test_session_admission.py).
"""
from __future__ import annotations

import json
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from rvnd import signing
from rvnd.approvals import (
    decide_approval, request_from_reservation, resolve_approval)
from rvnd.governance_lane import GovernanceLane, register_lane
from rvnd.mcp_serving import clear_request_principal, set_request_principal
from rvnd.mutation_log import (
    LogEvent, MutationLog, _canonical_event_hash, _signed_bytes)
from rvnd.parties import register_party
from rvnd.queue import enqueue_run
from rvnd import session_admission
from rvnd.session_capability import CapabilityError
from rvnd.use_case import agent_permitted, register_use_case

pytestmark = [
    pytest.mark.live_session_admission,
    pytest.mark.live_egress_capability,
]

_POLICY = "sha256:approved"
_T0 = 1_900_000_000.0


@pytest.fixture()
def reserved(tmp_path, monkeypatch):
    """A task reserved to agent-A: A is a registered agent with an approved lane
    and the use-case allowlist; B is a registered agent too (so every refusal is
    ABOUT ownership, never 'B is not an agent'); a competent approval board of
    humans stands ready to clear A's decision."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    folder = tmp_path / "ws"
    folder.mkdir()
    log = tmp_path / "log"

    register_party(str(folder), "agent-A", "agent", grade="L2", log_root=log)
    register_party(str(folder), "agent-B", "agent", grade="L2", log_root=log)
    register_lane(folder, GovernanceLane(
        lane_id="lane-A",
        agent="agent-A",
        max_grade="L2",
        action_classes=("classify",),
        folder=str(folder),
        policy_fingerprint=_POLICY,
        approved_by="controller",
        rationale="reserved to agent-A",
    ), log_root=log)
    register_use_case(
        str(folder), use_case_id="reserved-task", name="reserved-task",
        fingerprint={"domain": "town"}, risk="low",
        allowed_agents=["agent-A"], actor="controller", log_root=log)

    # A competence-matched approval board (humans with distinct competences).
    for pid, comp in (("lara", ["legal"]), ("finn", ["finance"]),
                      ("rita", ["risk"]), ("mara", ["marketing"])):
        register_party(str(folder), pid, "human", name=pid.title(),
                       competences=comp, log_root=log)
    return {"folder": str(folder), "log": log}


# ---------------------------------------------------------------------------
# L1 — use-case allowlist
# ---------------------------------------------------------------------------
def test_layer1_use_case_allowlist_refuses_B(reserved):
    f, log = reserved["folder"], reserved["log"]
    assert agent_permitted(f, "reserved-task", "agent-A", log_root=log) is True
    assert agent_permitted(f, "reserved-task", "agent-B", log_root=log) is False


# ---------------------------------------------------------------------------
# L2 — signed session principal (B cannot act as A)
# ---------------------------------------------------------------------------
def test_layer2_signed_principal_refuses_B_acting_as_A(reserved):
    f, log = reserved["folder"], reserved["log"]

    # A, as itself, mints and redeems a real capability.
    set_request_principal("agent-A", "agent-A")
    try:
        opened = session_admission.governance_open(f, party="agent-A",
                                 policy_fingerprint=_POLICY, log_root=log)
        assert opened["ok"] is True
        token_A = opened["capability_token"]
    finally:
        clear_request_principal()

    # (a) B cannot MINT A's capability — the principal must equal the party.
    set_request_principal("agent-B", "agent-B")
    try:
        with pytest.raises(CapabilityError, match="does not match"):
            session_admission.governance_open(f, party="agent-A",
                            policy_fingerprint=_POLICY, log_root=log)
    finally:
        clear_request_principal()

    # (b) B cannot REDEEM A's token — the verify party gate refuses it.
    with pytest.raises(CapabilityError, match="party mismatch"):
        session_admission.verify_operation_session(f, agent_id="agent-B",
                                 capability_token=token_A, log_root=log)


# ---------------------------------------------------------------------------
# L3 — run lease (one active run per (folder, workflow))
# ---------------------------------------------------------------------------
def test_layer3_run_lease_refuses_B(reserved):
    f, log = reserved["folder"], reserved["log"]
    enqueue_run(f, "reserved-task", enqueued_by="agent-A", log_root=log)
    with pytest.raises(ValueError, match="already_queued|already_running"):
        enqueue_run(f, "reserved-task", enqueued_by="agent-B", log_root=log)


# ---------------------------------------------------------------------------
# L4 — signed chain rejects a foreign-key (non-identity) decision
# ---------------------------------------------------------------------------
def test_layer4_signed_chain_rejects_foreign_key_decision(reserved):
    f, log = reserved["folder"], reserved["log"]
    mlog = MutationLog(f, log_root=log)

    # A's legitimate decision, signed by the workspace identity key on append.
    mlog.append(LogEvent(
        event="ingest", folder_path=f, pair_id="sha256:decision-A",
        actor="agent-A", extra={"kind": "ReservedDecision", "by": "agent-A"}))
    assert mlog.verify_chain().ok is True

    # B forges a decision with a FOREIGN key it controls (it lacks the
    # workspace identity key). Same host_id + valid prev_hash, so the ONLY
    # thing wrong is the signature — isolating the signature gate.
    lines = [json.loads(x) for x in mlog.log_file.read_text().splitlines() if x.strip()]
    foreign = Ed25519PrivateKey.generate()
    forged = {
        "event": "ingest", "channel": "system", "folder_path": f,
        "pair_id": "sha256:decision-B", "lifecycle_state": "", "problem_id": "",
        "source_hash": "", "actor": "agent-B", "audit_id": "forged-by-B",
        "ts": 1.0, "extra": {"kind": "ReservedDecision", "by": "agent-B"},
        "prev_hash": _canonical_event_hash(lines[-1]), "signature": "",
        "host_id": lines[-1].get("host_id", ""),
    }
    forged["signature"] = signing.sign_bytes(
        _signed_bytes({**forged, "signature": ""}), foreign)
    mlog.log_file.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lines + [forged]) + "\n")

    result = mlog.verify_chain()
    assert result.ok is False
    assert result.signature_failures, "foreign-key decision must fail the signature gate"
    assert any(sf.get("audit_id") == "forged-by-B" for sf in result.signature_failures)


# ---------------------------------------------------------------------------
# Q — the reserved decision clears ONLY through a competence-matched quorum
# ---------------------------------------------------------------------------
_QUORUM = {"kind": "reserved-task", "by": "2 of { legal, finance, risk }"}


def _open_clearance(reserved, rid="clear-1"):
    return request_from_reservation(
        reserved["folder"], rid, _QUORUM, requester="agent-A",
        now=_T0, log_root=reserved["log"])


def test_under_competent_hand_does_not_clear(reserved):
    f, log = reserved["folder"], reserved["log"]
    _open_clearance(reserved)
    decide_approval(f, "clear-1", "approve", actor="lara", now=_T0 + 10, log_root=log)   # legal ✓
    decide_approval(f, "clear-1", "approve", actor="mara", now=_T0 + 20, log_root=log)   # marketing ∉ set
    r = resolve_approval(f, "clear-1", now=_T0 + 60, log_root=log)
    assert r["state"] == "pending"                    # one competent hand short; mara does not count
    assert "mara" not in set(r.get("approvers", []))


def test_competence_matched_quorum_clears(reserved):
    f, log = reserved["folder"], reserved["log"]
    _open_clearance(reserved)
    assert resolve_approval(f, "clear-1", now=_T0 + 60, log_root=log)["needed"] == 2
    decide_approval(f, "clear-1", "approve", actor="lara", now=_T0 + 10, log_root=log)   # legal
    assert resolve_approval(f, "clear-1", now=_T0 + 60, log_root=log)["state"] == "pending"
    decide_approval(f, "clear-1", "approve", actor="finn", now=_T0 + 20, log_root=log)   # finance
    r = resolve_approval(f, "clear-1", now=_T0 + 60, log_root=log)
    assert r["state"] == "granted"
    assert set(r["approvers"]) == {"lara", "finn"}


def test_signed_clearing_lands_on_the_chain(reserved):
    """The clearing (the approval votes) is journalled as signed chain events —
    the audit trail the ownership decision rests on. verify_chain stays green."""
    f, log = reserved["folder"], reserved["log"]
    _open_clearance(reserved)
    decide_approval(f, "clear-1", "approve", actor="lara", now=_T0 + 10, log_root=log)
    decide_approval(f, "clear-1", "approve", actor="finn", now=_T0 + 20, log_root=log)
    events = list(MutationLog(f, log_root=log).replay())

    def _kind(e):
        return (e.extra or {}).get("kind", "")

    assert any(e.pair_id == "approval:clear-1" and _kind(e) == "ApprovalRequested"
               for e in events), "the clearance request must be journalled"
    signed_votes = {e.actor for e in events
                    if e.pair_id == "approval:clear-1" and _kind(e) == "ApprovalDecision"}
    assert signed_votes == {"lara", "finn"}       # both competent votes are on the chain
    # And the whole clearing trail is cryptographically intact.
    assert MutationLog(f, log_root=log).verify_chain().ok is True
