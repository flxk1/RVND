# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""session_governance / connected_agents_governance — the live-session-to-chain
join. The signed chain is keyed by the true per-session actor (the id the
PreToolUse hook records); a live MCP connection carries that same id as
``session_id`` (CLAUDE_CODE_SESSION_ID). So a connection whose session_id equals
a chain actor IS that acting session's real presence — its verdict is the
actor's real lane disposition, never a fabrication, and the identity is
``witnessed`` (chain-proven), not merely observed.

  python -m pytest server/tests/test_session_governance.py -q
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rvnd import connected_agents as ca
from rvnd import governance_live as gl
from rvnd.governance_live import (connected_agents_governance,
                                  governance_live, session_governance)
from rvnd.mutation_log import LogEvent, MutationLog

_SID = "sess-4eeae25c"          # a host session id == the chain actor
_OTHER = "sess-ffffffff"        # a session that never acted on this chain


def _act(log: MutationLog, folder: str, actor: str, action: str) -> None:
    """Append one acting event, keyed by the session-id actor — the shape the
    PreToolUse monitor journals when a session takes a governed action."""
    log.append(LogEvent(
        event="system", folder_path=folder, pair_id=f"{actor}:{action}",
        channel="system", actor=actor,
        extra={"action": action, "kind": "ToolCall"}))


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    monkeypatch.setenv("WORKSPACE_AGENTS_DIR", str(tmp_path / "agents"))
    folder = str(tmp_path / "ws")
    Path(folder).mkdir()
    lr = tmp_path / "logroot"
    log = MutationLog(folder, log_root=lr)
    _act(log, folder, _SID, "workspace_dispatch")     # this session acted twice
    _act(log, folder, _SID, "workspace_conformity")
    return {"folder": folder, "lr": str(lr)}


def test_live_connection_joins_its_chain_actor_by_session_id(seeded):
    # The core mechanism: a connection carrying session_id == chain actor surfaces
    # as that actor's LIVE presence with its real (fail-closed 'refused') verdict.
    ca.register_connection(agent="claude-code", session_id=_SID)
    out = session_governance(seeded["folder"], log_root=seeded["lr"])
    assert out["ok"] and out["session_count"] == 1
    s = out["sessions"][0]
    assert s["actor"] == _SID
    assert s["connected"] is True                      # a live connection matched
    assert s["session_id"] == _SID
    assert s["identity_tier"] == "witnessed"           # chain-proven, not observed
    assert s["verdict"] == "refused"                   # real lane disposition (no lane)
    assert s["event_count"] == 2
    assert s["connid"] and s["pid"]


def test_acting_actor_without_a_connection_is_chain_only(seeded):
    # An actor on the chain with no live connection is real, but not "live".
    out = session_governance(seeded["folder"], log_root=seeded["lr"])
    s = out["sessions"][0]
    assert s["actor"] == _SID
    assert s["connected"] is False
    assert s["connid"] is None and s["pid"] is None
    assert s["identity_tier"] == "witnessed"           # still chain-proven


def test_idle_connection_that_never_acted_is_presence_only(seeded):
    # A connection whose session_id is NOT on the chain is idle presence — it
    # lands in connected_only, never fabricated into an acting session.
    ca.register_connection(agent="idle-bot", session_id=_OTHER)
    out = session_governance(seeded["folder"], log_root=seeded["lr"])
    assert all(s["actor"] != _OTHER for s in out["sessions"])
    idle = out["connected_only"]
    assert len(idle) == 1 and idle[0]["session_id"] == _OTHER
    assert idle[0]["client"]["tier"] == "observed"     # descriptive presence tier


def test_client_info_tier_is_observed_never_witnessed(seeded):
    # clientInfo travels tier 'observed' (seen at the transport handshake), never
    # fused with the witnessed chain identity.
    cid = ca.register_connection(agent="claude-code", session_id=_SID)
    ca.update_client_info(cid, name="cursor", version="1.4.2")
    out = session_governance(seeded["folder"], log_root=seeded["lr"])
    client = out["sessions"][0]["client"]
    assert client == {"name": "cursor", "version": "1.4.2", "tier": "observed"}


def test_connected_agents_governance_attributes_by_session_id(seeded):
    # The agent-name-granularity join: attributed iff the join actor (session_id
    # first) appears on the chain; only then is a real verdict computed.
    ca.register_connection(agent="claude-code", session_id=_SID)
    out = connected_agents_governance(seeded["folder"], log_root=seeded["lr"])
    assert out["ok"] and out["count"] == 1
    gov = out["agents"][0]["governance"]
    assert gov["attributed"] is True
    assert gov["join_key"] == "session_id"
    assert gov["verdict"] == "refused"                 # real lane disposition
    assert gov["event_count"] == 2


def test_unattributed_connection_is_honest_neutral_never_fabricated(seeded):
    # A connection that never acted gets all-nulls — NOT a fail-closed 'refused'
    # (which would misread as an earned verdict for an agent with no history).
    ca.register_connection(agent="stranger", session_id=_OTHER)
    out = connected_agents_governance(seeded["folder"], log_root=seeded["lr"])
    gov = out["agents"][0]["governance"]
    assert gov["attributed"] is False
    assert gov["join_key"] is None
    assert gov["verdict"] is None                      # honest-neutral, not 'refused'
    assert gov["event_count"] == 0


def test_reachable_through_the_workspace_workflow_facade(seeded, monkeypatch):
    # Route evidence: both ops dispatch through the public facade, reading the log
    # root from WORKSPACE_L0_LOG_ROOT (as the other govlive ops do).
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", seeded["lr"])
    ca.register_connection(agent="claude-code", session_id=_SID)
    from rvnd.mcp_server import workspace_workflow
    sg = workspace_workflow(op="session_governance",
                            params={"folder_context": seeded["folder"]})
    assert sg["ok"] and sg["session_count"] == 1
    cag = workspace_workflow(op="connected_agents_governance",
                             params={"folder_context": seeded["folder"]})
    assert cag["ok"] and cag["count"] == 1


def test_join_projection_mutates_nothing(seeded):
    # The load-bearing invariant for any governance projection: no chain append.
    folder, lr = seeded["folder"], seeded["lr"]
    ca.register_connection(agent="claude-code", session_id=_SID)
    before = MutationLog(folder, log_root=lr).count()
    session_governance(folder, log_root=lr)
    connected_agents_governance(folder, log_root=lr)
    assert MutationLog(folder, log_root=lr).count() == before


# ── presence_ambiguous: the join key is an unauthenticated host env var, so two
# live connections can claim one actor. The collision must be FLAGGED, not hidden
# behind an arbitrary newest-wins pick. ──────────────────────────────────────

def test_presence_ambiguous_flags_session_id_collision(seeded):
    ca.register_connection(agent="claude-code", session_id=_SID)
    ca.register_connection(agent="impostor", session_id=_SID)   # same id, 2nd conn
    s = session_governance(seeded["folder"], log_root=seeded["lr"])["sessions"][0]
    assert s["actor"] == _SID and s["connected"] is True
    assert s["presence_ambiguous"] is True


def test_single_connection_is_not_ambiguous(seeded):
    ca.register_connection(agent="claude-code", session_id=_SID)
    s = session_governance(seeded["folder"], log_root=seeded["lr"])["sessions"][0]
    assert s["presence_ambiguous"] is False


# ── fail-OPEN guard: a broken reconciliation check must render "—" (None), never
# a false "0 unauthorised" that reads identical to a verified-clean board. ─────

def test_reconciliation_failure_is_none_not_false_zero(seeded, monkeypatch):
    monkeypatch.setattr(gl, "_reconciliation", lambda *a, **k: {"status": "unavailable"})
    board = governance_live(seeded["folder"], log_root=seeded["lr"])
    assert board["summary"]["unauthorised_effects"] is None      # → "—", not a false 0


def test_reconciliation_real_zero_stays_zero(seeded, monkeypatch):
    monkeypatch.setattr(gl, "_reconciliation",
                        lambda *a, **k: {"status": "reconciled",
                                         "observed_not_authorised": 0})
    board = governance_live(seeded["folder"], log_root=seeded["lr"])
    assert board["summary"]["unauthorised_effects"] == 0         # a verified zero is 0
