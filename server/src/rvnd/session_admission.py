# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Broker-side session opening and execution-path capability admission."""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .governance_lane import get_lane
from .mutation_log import LogEvent, MutationLog
from .parties import list_parties
from .session_capability import CapabilityError, CapabilityVerifier, mint

SESSION_SPEC = b"rvnd/session-admission/v1"


def runtime_spec_fingerprint() -> str:
    return "sha256:" + hashlib.sha256(SESSION_SPEC).hexdigest()


def _folder(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def _active_agent(folder: str, party: str, log_root: Optional[str]) -> dict:
    rows = list_parties(folder, kind="agent", log_root=log_root).get("parties", [])
    match = next((row for row in rows if row.get("party_id") == party), None)
    if match is None:
        raise CapabilityError("party is not a registered agent")
    if match.get("status", "active") != "active":
        raise CapabilityError(f"party is {match.get('status')}")
    return match


def governance_open(
    folder_context: str,
    *,
    party: str,
    policy_fingerprint: str,
    ttl_seconds: int = 900,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Mint only for an active registered agent with an approved current lane.

    The admission response also carries the agent's ``capabilities`` — the
    read-only ``lane_capabilities`` projection of the just-bound lane — so an
    agent starts the session already knowing its boundaries, provably about
    the same policy the token is bound to (identical policy_fingerprint). A
    pure read riding the response: it touches neither the audit event nor
    enforcement, and stays re-queryable mid-session via the standalone verb."""
    from .principal import get_request_principal
    principal = get_request_principal()
    if principal is None or principal.get("rung") not in {
        "proxy-verified", "loopback-session",
    }:
        raise CapabilityError("verified request principal required")
    if principal.get("party") != party:
        raise CapabilityError("principal does not match requested party")
    folder = _folder(folder_context)
    registered = _active_agent(folder, party, log_root)
    lane = get_lane(folder, party, log_root=log_root)
    if lane is None:
        raise CapabilityError("no active governance lane")
    if not lane.policy_fingerprint:
        raise CapabilityError("governance lane has no policy fingerprint")
    if lane.policy_fingerprint != policy_fingerprint:
        raise CapabilityError("active policy fingerprint does not match lane")
    token, claims = mint(
        party=party,
        lane_id=lane.lane_id,
        folder=folder,
        grade=lane.max_grade,
        policy_fingerprint=lane.policy_fingerprint,
        spec_fingerprint=runtime_spec_fingerprint(),
        uid=os.getuid(),
        ttl_seconds=ttl_seconds,
    )
    # Audit only non-secret claims. The bearer token itself must never enter logs.
    MutationLog(folder, log_root=Path(log_root) if log_root else None).append(
        LogEvent(
            event="system",
            folder_path=folder,
            pair_id=f"session:{claims.nonce}",
            channel="system",
            actor=party,
            extra={
                "kind": "GovernanceSessionOpened",
                "claims": asdict(claims),
                "agent_uid": registered.get("agent_uid", ""),
            },
        )
    )
    # The boundary projection rides the admission response (never the audit
    # event). lane_capabilities is fail-closed internally: on any read failure
    # it reports no capabilities, so admission itself is never blocked by it.
    from .lane_capabilities import lane_capabilities
    capabilities = lane_capabilities(folder, party, log_root=log_root)
    return {"ok": True, "capability_token": token, "claims": asdict(claims),
            "capabilities": capabilities}


def verify_operation_session(
    folder_context: str,
    *,
    agent_id: str,
    capability_token: str,
    log_root: Optional[str] = None,
) -> Any:
    """Re-check mutable authority on every execution; tokens never freeze it."""
    if not capability_token:
        raise CapabilityError("session capability required")
    folder = _folder(folder_context)
    _active_agent(folder, agent_id, log_root)
    claims = CapabilityVerifier.from_key_dir().verify(
        capability_token, expected_folder=folder, expected_uid=os.getuid()
    )
    if claims.party != agent_id:
        raise CapabilityError("capability party mismatch")
    if claims.spec_fingerprint != runtime_spec_fingerprint():
        raise CapabilityError("capability runtime spec mismatch")
    lane = get_lane(folder, agent_id, log_root=log_root)
    if lane is None or lane.lane_id != claims.lane_id:
        raise CapabilityError("capability governance lane is no longer active")
    if lane.policy_fingerprint != claims.policy_fingerprint:
        raise CapabilityError("capability policy is no longer active")
    if lane.max_grade != claims.grade:
        raise CapabilityError("capability grade is no longer active")
    return claims
