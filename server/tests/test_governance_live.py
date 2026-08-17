# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""governance_live — read-only live-governance board (honest-subset v2).

Pins the board SHAPE, the honest session derivation (admitted = unexpired AND
unrevoked; capability present iff admitted; fail-closed verdict), chain
monotonicity, run-lease projection — and, load-bearing, that the projection
MUTATES NOTHING: the chain length, the workspace registry, and the run queue
are identical before and after (the doctrine the panel exists to guarantee).

  python -m pytest server/tests/test_governance_live.py -q
"""
from __future__ import annotations

from pathlib import Path

import pytest

from workspaces import queue as _queue
from workspaces import workspace_registry as _registry
from workspaces.governance_live import governance_live
from workspaces.mutation_log import LogEvent, MutationLog
from workspaces.op_mutation import is_read, mutates

_NOW = 1_000_000.0


def _open_session(log: MutationLog, folder: str, *, nonce: str, exp: float,
                  party: str) -> None:
    """Append a GovernanceSessionOpened event, exactly as admission journals it."""
    log.append(LogEvent(
        event="system", folder_path=folder, pair_id=f"session:{nonce}",
        channel="system", actor=party,
        extra={"kind": "GovernanceSessionOpened",
               "claims": {"nonce": nonce, "exp": exp, "folder": folder,
                          "party": party, "grade": "L2", "lane_id": "lane1"}}))


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    folder = str(tmp_path / "ws")
    Path(folder).mkdir()
    lr = tmp_path / "logroot"
    log = MutationLog(folder, log_root=lr)
    _open_session(log, folder, nonce="AAA", exp=_NOW + 3600, party="alex")  # live
    _open_session(log, folder, nonce="BBB", exp=_NOW - 10, party="robin")   # expired
    _queue.enqueue_run(folder, "operate", log_root=lr)                      # one run
    return {"folder": folder, "lr": lr}


def _board(seeded):
    return governance_live(seeded["folder"], log_root=str(seeded["lr"]), now=_NOW)


def test_op_is_read_only_classified():
    # The op_mutation gate fails closed to "write"; a read-only projection must
    # be registered as a read or it would raise a confirm-card and be gated.
    assert is_read("governance_live") is True
    assert mutates("governance_live") is False


def test_board_shape(seeded):
    b = _board(seeded)
    assert b["ok"] is True
    assert set(b) == {"ok", "summary", "sessions", "leases", "chain",
                      "certificates", "reconciliation"}
    assert set(b["summary"]) == {
        "sessions_open", "admitted", "run_leases_held", "escalations",
        "unauthorised_effects"}
    assert isinstance(b["sessions"], list)
    assert isinstance(b["leases"], list)
    assert isinstance(b["chain"], list)


def test_session_admission_honesty(seeded):
    b = _board(seeded)
    by_sid = {s["sid"]: s for s in b["sessions"]}
    assert by_sid["session:AAA"]["admitted"] is True
    assert "capability" in by_sid["session:AAA"]        # admitted → capability
    assert by_sid["session:BBB"]["admitted"] is False
    assert "capability" not in by_sid["session:BBB"]    # expired → omitted
    # No lane registered → fail-closed refused, never a permissive blank.
    assert by_sid["session:AAA"]["verdict"] == "refused"
    assert by_sid["session:AAA"]["escalation"] is False
    assert b["summary"]["sessions_open"] == 2
    assert b["summary"]["admitted"] == 1


def test_revoked_session_is_not_admitted(seeded, monkeypatch, tmp_path):
    rev = tmp_path / "revoked-nonces"
    rev.write_text("AAA\n", encoding="utf-8")
    monkeypatch.setenv("RVND_CAPABILITY_REVOCATIONS", str(rev))
    b = _board(seeded)
    aaa = next(s for s in b["sessions"] if s["sid"] == "session:AAA")
    assert aaa["admitted"] is False          # unexpired BUT revoked → not admitted
    assert "capability" not in aaa


def test_lease_projection_is_position_zero_holder(seeded):
    # enqueue enforces ≤1 active run per (folder, workflow) — a concurrent
    # duplicate is REFUSED, not queued — so the projected run is position 0.
    b = _board(seeded)
    assert len(b["leases"]) == 1
    lease = b["leases"][0]
    assert lease["workflow"] == "operate"
    assert lease["position"] == 0
    assert lease["holder"] is None          # pending, not yet leased to a worker
    assert lease["ttl_s"] is None
    assert b["summary"]["run_leases_held"] == 0


def test_chain_is_contiguous_newest_first_with_verifiable_linkage(seeded):
    b = _board(seeded)
    chain = b["chain"]
    seqs = [c["seq"] for c in chain]
    assert seqs == sorted(seqs, reverse=True)          # strictly newest-first
    assert len(set(seqs)) == len(seqs)                 # no duplicate indices
    for node in chain:
        assert set(node) == {"seq", "actor", "event", "extra", "hash", "prev_hash"}
    assert all(c["hash"] for c in chain)               # non-empty content hashes
    # Contiguous replay tail (no filtering between adjacent entries) — the
    # precondition for the linkage check below.
    for older, newer in zip(chain[1:], chain[:-1]):
        assert newer["seq"] == older["seq"] + 1
    # Invariant 6 — real chain linearity, now verifiable because hash is
    # exposed: each entry's prev_hash IS the content hash of the entry
    # immediately older than it.
    for older, newer in zip(chain[1:], chain[:-1]):
        assert newer["prev_hash"] == older["hash"]


def test_reachable_through_the_workspace_workflow_facade(seeded, monkeypatch):
    """Route evidence: the op dispatches through the workspace_workflow facade
    (its public op route), not only as a direct function call. The facade reads
    the log root from WORKSPACE_L0_LOG_ROOT, so bind it to the seeded root."""
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(seeded["lr"]))
    from workspaces.mcp_server import workspace_workflow
    b = workspace_workflow(op="governance_live",
                           params={"folder_context": seeded["folder"]})
    assert b["ok"] is True
    assert set(b["summary"]) == {
        "sessions_open", "admitted", "run_leases_held", "escalations",
        "unauthorised_effects"}
    assert b["summary"]["sessions_open"] == 2


def test_projection_mutates_nothing(seeded):
    """The load-bearing invariant: a governance projection must never append to
    the chain, touch the registry, or move the queue."""
    folder, lr = seeded["folder"], seeded["lr"]
    chain_before = MutationLog(folder, log_root=lr).count()
    registry_before = _registry.load_registry(log_root=lr)
    queue_before = [e.to_dict() for e in _queue.list_queue(log_root=lr)]

    _board(seeded)
    _board(seeded)  # twice — a second read must not accrete either

    assert MutationLog(folder, log_root=lr).count() == chain_before
    assert _registry.load_registry(log_root=lr) == registry_before
    assert [e.to_dict() for e in _queue.list_queue(log_root=lr)] == queue_before
