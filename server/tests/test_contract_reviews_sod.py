# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Separation-of-duty in the named-signer engine (contract_reviews).

Two holes found by adversarial probing and fixed: a requester could approve their OWN
review (§1.5 forbids this), and a duplicated roster name ("bob","bob") was satisfied by a
single signature. Mirrors the §1.5 rule: the requester's own hand never counts; the roster
is the set of DISTINCT required identities. A reject still absorbs (incl. the requester's).
"""
from __future__ import annotations

import os

import pytest

from rvnd.contracts.reviews import (
    list_contract_approvals, record_contract_approval, request_contract_approval)
from rvnd.parties import register_party

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    w = tmp_path / "org"; w.mkdir(); lr = str(tmp_path / "log")
    for p in ("alice", "bob"):
        register_party(str(w), p, "human", competences=["legal"], log_root=lr)
    return {"w": str(w), "lr": lr}


def _overall_via_list(ws, aid):
    rows = list_contract_approvals(ws["w"], log_root=ws["lr"])
    rows = rows if isinstance(rows, list) else rows.get("approvals", [])
    return next((a.get("overall_state") for a in rows if a.get("approval_id") == aid), None)


def test_requester_cannot_self_approve(ws):
    r = request_contract_approval(ws["w"], contract_id="c1", signers=["alice"],
                                  requested_by="alice", log_root=ws["lr"])
    res = record_contract_approval(ws["w"], approval_id=r["approval_id"], signer="alice",
                                   decision="approved", actor="alice", log_root=ws["lr"])
    assert res["overall_state"] != "approved"            # record path
    assert _overall_via_list(ws, r["approval_id"]) != "approved"   # list path (the UI's)


def test_requester_reject_still_absorbs(ws):
    r = request_contract_approval(ws["w"], contract_id="c2", signers=["alice", "bob"],
                                  requested_by="alice", log_root=ws["lr"])
    res = record_contract_approval(ws["w"], approval_id=r["approval_id"], signer="alice",
                                   decision="rejected", actor="alice", log_root=ws["lr"])
    assert res["overall_state"] == "rejected"            # rejecting your own request is fine


def test_duplicate_roster_is_distinct(ws):
    # ["bob","bob"] must not read as two hands; collapsed to one distinct required signer.
    r = request_contract_approval(ws["w"], contract_id="c3", signers=["bob", "bob"],
                                  requested_by="system", log_root=ws["lr"])
    res = record_contract_approval(ws["w"], approval_id=r["approval_id"], signer="bob",
                                   decision="approved", actor="bob", log_root=ws["lr"])
    assert res["overall_state"] == "approved"            # honestly 1-of-1 now, not 1-of-2


def test_non_requester_signer_approves_normally(ws):
    r = request_contract_approval(ws["w"], contract_id="c4", signers=["bob"],
                                  requested_by="alice", log_root=ws["lr"])
    res = record_contract_approval(ws["w"], approval_id=r["approval_id"], signer="bob",
                                   decision="approved", actor="bob", log_root=ws["lr"])
    assert res["overall_state"] == "approved"            # control: normal flow unbroken
