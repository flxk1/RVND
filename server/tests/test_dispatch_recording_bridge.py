# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Recording bridge for the problem-solution graph: dispatched skills —
including adapter-ingested Cowork/MCP-client skills — record cases too, but
their evidence outcomes require an explicit HUMAN closure.

Design decision (register row answered 2026-06-12): the human closure for a
non-walker solver is a named actor + a written rationale, checked against
the party registry — an actor registered as an AGENT (or a suspended/killed
party) can never close a case into evidence. Mirrors approvals doctrine:
agent approvals never count. Fail-closed: refusal appends nothing.

Invariants (written BEFORE the logic):
  B1  human-ratified dispatch lands as a solves-edge with rationale + receipt
  B2  evidence outcome without actor or rationale → refused, nothing appended
  B3  actor registered as AGENT → refused (no self-ratifying skills)
  B4  suspended party → refused (kill switch reaches case closures)
  B5  'open' from anyone (incl. agents) records honestly, yields no edge
  B6  explicit fingerprint is carried and retrievable
"""
from __future__ import annotations

import os

import pytest

from rvnd.case_index import record_dispatch_case, retrieve, solves_edges
from rvnd.parties import register_party, set_party_status

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    lr = str(tmp_path / "logs")
    register_party(str(ws), "alex", "human", name="Alex", log_root=lr)
    register_party(str(ws), "runner-1", "agent", owner="alex",
                   purpose="dispatch", grade="L1", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def test_human_ratified_dispatch_becomes_evidence(env):          # B1
    audit_id = record_dispatch_case(
        env["ws"], solver="skill:contract-key-terms",
        question="extract the splits", outcome="ratified", actor="alex",
        rationale="output checked against the signed split sheet",
        log_root=env["lr"])
    edges = solves_edges(env["ws"], log_root=env["lr"])
    assert len(edges) == 1
    assert edges[0]["solver"] == "skill:contract-key-terms"
    assert edges[0]["receipt"] == audit_id


def test_evidence_needs_actor_and_rationale(env):                # B2
    with pytest.raises(ValueError):
        record_dispatch_case(env["ws"], solver="skill:x", outcome="ratified",
                             actor="alex", rationale="", log_root=env["lr"])
    with pytest.raises(ValueError):
        record_dispatch_case(env["ws"], solver="skill:x", outcome="decided",
                             actor="", rationale="why", log_root=env["lr"])
    assert solves_edges(env["ws"], log_root=env["lr"]) == []


def test_agent_actor_cannot_close_into_evidence(env):            # B3
    with pytest.raises(ValueError):
        record_dispatch_case(env["ws"], solver="skill:x", outcome="ratified",
                             actor="runner-1", rationale="looks right to me",
                             log_root=env["lr"])
    assert solves_edges(env["ws"], log_root=env["lr"]) == []


def test_suspended_party_cannot_close(env):                      # B4
    set_party_status(env["ws"], "alex", "suspended", actor="alex",
                     log_root=env["lr"])
    with pytest.raises(ValueError):
        record_dispatch_case(env["ws"], solver="skill:x", outcome="ratified",
                             actor="alex", rationale="r", log_root=env["lr"])
    assert solves_edges(env["ws"], log_root=env["lr"]) == []


def test_open_from_agent_records_without_edge(env):              # B5
    record_dispatch_case(env["ws"], solver="skill:x", outcome="open",
                         actor="runner-1", log_root=env["lr"])
    assert solves_edges(env["ws"], log_root=env["lr"]) == []


def test_fingerprint_carried_and_retrievable(env):               # B6
    record_dispatch_case(
        env["ws"], solver="skill:dpa-check", outcome="decided", actor="alex",
        rationale="chose reading 2 after review",
        fingerprint={"profile": "legal-de", "rooms": ["Art. 28(3)"]},
        log_root=env["lr"])
    hits = retrieve(env["ws"], {"rooms": ["Art. 28(3)"]}, log_root=env["lr"])
    assert hits and hits[0]["solver"] == "skill:dpa-check"
    miss = retrieve(env["ws"], {"rooms": ["Art. 99"]}, log_root=env["lr"])
    assert miss == []
