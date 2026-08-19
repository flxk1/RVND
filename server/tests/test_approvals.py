# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Approval semantics (§ 1.5) — written before the logic.

The TASKS verification lines: timeout → DENY (silence is never consent),
absence → DELEGATE (a logged grant, never an ambient power). Everything is
chain events on the folder's signed log (no new store, like parties.py);
``resolve_approval`` is a pure projection of (chain, now).

The control-form algebra gives the requirements their meaning:
pre_approval = at least one counting approval; two_approvers = two DISTINCT
approvers (the same hand twice is one approver); competent_approver = the
approver holds the routed competence (or a logged delegation from someone
who does). Deny is immediate and absorbing. The requester's own hand never
counts. Suspended/killed parties cannot move an approval.
"""
from __future__ import annotations

import os

import pytest

from rvnd.parties import register_party, set_party_status

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

T0 = 1_900_000_000.0          # fixed epoch for determinism


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    lr = str(tmp_path / "logs")
    for pid, comp in (("anna", ["data-protection"]),
                      ("ben", ["data-protection"]),
                      ("carl", ["finance"])):
        register_party(str(ws), pid, "human", name=pid.title(),
                       competences=comp, log_root=lr)
    register_party(str(ws), "agent-x", "agent", owner="anna",
                   purpose="ingest", grade="L2", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _request(env, form="single_approver", competence="data-protection",
             timeout=3600.0, rid="req-1", requester="agent-x"):
    from rvnd.approvals import request_approval
    return request_approval(env["ws"], rid, form=form, competence=competence,
                            requester=requester, timeout_seconds=timeout,
                            now=T0, log_root=env["lr"])


def _decide(env, rid, decision, actor, now=T0 + 10):
    from rvnd.approvals import decide_approval
    return decide_approval(env["ws"], rid, decision, actor=actor, now=now,
                           log_root=env["lr"])


def _resolve(env, rid, now):
    from rvnd.approvals import resolve_approval
    return resolve_approval(env["ws"], rid, now=now, log_root=env["lr"])


# --- the verification lines ---------------------------------------------------

def test_timeout_means_deny_not_consent(env):
    _request(env, timeout=3600.0)
    assert _resolve(env, "req-1", now=T0 + 60)["state"] == "pending"
    r = _resolve(env, "req-1", now=T0 + 3601)
    assert r["state"] == "denied" and r["reason"] == "timeout"


def test_absence_delegates_via_logged_grant(env):
    """Anna is away; she DELEGATES data-protection to Carl (a logged grant on
    the chain). Ben is also away. Carl's approval now counts."""
    from rvnd.approvals import delegate_competence
    set_party_status(env["ws"], "ben", "suspended", actor="ben",
                     log_root=env["lr"])
    d = delegate_competence(env["ws"], "data-protection", from_party="anna",
                            to_party="carl", actor="anna", now=T0,
                            log_root=env["lr"])
    assert d["ok"] is True
    _request(env, form="expert_review")
    _decide(env, "req-1", "approve", actor="carl")
    assert _resolve(env, "req-1", now=T0 + 60)["state"] == "granted"


def test_delegation_requires_holding_the_competence(env):
    from rvnd.approvals import delegate_competence
    with pytest.raises(ValueError):
        delegate_competence(env["ws"], "data-protection", from_party="carl",
                            to_party="anna", actor="carl", now=T0,
                            log_root=env["lr"])


def test_without_delegation_noncompetent_approval_does_not_count(env):
    _request(env, form="expert_review")
    _decide(env, "req-1", "approve", actor="carl")    # finance, not delegated
    assert _resolve(env, "req-1", now=T0 + 60)["state"] == "pending"


# --- four-eyes: two DISTINCT approvers -----------------------------------------

def test_four_eyes_needs_two_distinct_hands(env):
    _request(env, form="four_eyes")
    _decide(env, "req-1", "approve", actor="anna")
    assert _resolve(env, "req-1", now=T0 + 60)["state"] == "pending"
    _decide(env, "req-1", "approve", actor="anna", now=T0 + 20)  # same hand
    assert _resolve(env, "req-1", now=T0 + 60)["state"] == "pending"
    _decide(env, "req-1", "approve", actor="ben", now=T0 + 30)
    assert _resolve(env, "req-1", now=T0 + 60)["state"] == "granted"


# --- deny + the hands that never count ------------------------------------------

def test_deny_is_immediate_and_absorbing(env):
    _request(env, form="four_eyes")
    _decide(env, "req-1", "approve", actor="anna")
    _decide(env, "req-1", "deny", actor="ben", now=T0 + 20)
    _decide(env, "req-1", "approve", actor="ben", now=T0 + 30)
    r = _resolve(env, "req-1", now=T0 + 60)
    assert r["state"] == "denied" and r["reason"] == "denied-by-ben"


def test_requesters_own_hand_never_counts(env):
    _request(env, requester="anna")
    _decide(env, "req-1", "approve", actor="anna")
    assert _resolve(env, "req-1", now=T0 + 60)["state"] == "pending"
    _decide(env, "req-1", "approve", actor="ben", now=T0 + 20)
    assert _resolve(env, "req-1", now=T0 + 60)["state"] == "granted"


def test_suspended_approver_does_not_count(env):
    _request(env)
    _decide(env, "req-1", "approve", actor="anna")
    set_party_status(env["ws"], "anna", "suspended", actor="ben",
                     log_root=env["lr"])
    assert _resolve(env, "req-1", now=T0 + 60)["state"] == "pending"


def test_agent_approval_never_counts(env):
    _request(env)
    _decide(env, "req-1", "approve", actor="agent-x")
    assert _resolve(env, "req-1", now=T0 + 60)["state"] == "pending"


# --- form edge cases -------------------------------------------------------------

def test_auto_form_granted_immediately_block_never(env):
    _request(env, form="auto", rid="r-auto")
    assert _resolve(env, "r-auto", now=T0 + 1)["state"] == "granted"
    _request(env, form="block", rid="r-block")
    r = _resolve(env, "r-block", now=T0 + 1)
    assert r["state"] == "denied" and r["reason"] == "blocked"


def test_unknown_form_or_request_refused(env):
    from rvnd.approvals import resolve_approval
    with pytest.raises(ValueError):
        _request(env, form="notarized", rid="r-x")
    with pytest.raises(ValueError):
        resolve_approval(env["ws"], "ghost", now=T0, log_root=env["lr"])


def test_grant_after_timeout_stays_denied(env):
    """A late approval cannot resurrect a timed-out request: deny-by-silence
    is evaluated at the deadline, not at the approval's arrival."""
    _request(env, timeout=60.0)
    _decide(env, "req-1", "approve", actor="anna", now=T0 + 120)
    r = _resolve(env, "req-1", now=T0 + 130)
    assert r["state"] == "denied" and r["reason"] == "timeout"


def test_resolution_is_deterministic_projection(env):
    _request(env, form="four_eyes")
    _decide(env, "req-1", "approve", actor="anna")
    _decide(env, "req-1", "approve", actor="ben", now=T0 + 20)
    assert _resolve(env, "req-1", now=T0 + 60) == \
        _resolve(env, "req-1", now=T0 + 60)
