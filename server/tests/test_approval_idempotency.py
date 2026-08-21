# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Approval idempotency + action_summary.

Workflow engines deliver at-least-once: n8n retries, Wait-nodes re-poll,
users wire retry-on-error around the approval step. `request_approval`
must be replay-safe — same (contract_id, requested_by, idempotency_key)
returns the EXISTING approval instead of minting a duplicate the human
could mis-sign.

All calls pin log_root to tmp_path: the default ~/.workspace/log is shared
global state that other suite tests may seal or make unwritable.
"""
from __future__ import annotations

from rvnd.contracts.reviews import (
    list_contract_approvals,
    record_contract_approval,
    request_contract_approval,
)


def _root(folder):
    return folder / "_log"


def _req(folder, **kw):
    base = dict(contract_id="c-1", signers=["alex"], requested_by="n8n",
                log_root=_root(folder))
    base.update(kw)
    return request_contract_approval(folder, **base)


def test_same_key_returns_existing_approval(tmp_path):
    a = _req(tmp_path, idempotency_key="k1", action_summary="post to slack")
    b = _req(tmp_path, idempotency_key="k1", action_summary="post to slack")
    assert a["approval_id"] == b["approval_id"]
    assert a["deduplicated"] is False
    assert b["deduplicated"] is True
    assert len(list_contract_approvals(tmp_path, log_root=_root(tmp_path))) == 1


def test_different_key_mints_new_approval(tmp_path):
    a = _req(tmp_path, idempotency_key="k1")
    b = _req(tmp_path, idempotency_key="k2")
    assert a["approval_id"] != b["approval_id"]
    assert len(list_contract_approvals(tmp_path, log_root=_root(tmp_path))) == 2


def test_empty_key_preserves_legacy_behaviour(tmp_path):
    a = _req(tmp_path)
    b = _req(tmp_path)
    assert a["approval_id"] != b["approval_id"]


def test_dedupe_scoped_by_contract_and_requester(tmp_path):
    a = _req(tmp_path, idempotency_key="k1", contract_id="c-1")
    b = _req(tmp_path, idempotency_key="k1", contract_id="c-2")
    c = _req(tmp_path, idempotency_key="k1", requested_by="langdock")
    assert len({a["approval_id"], b["approval_id"], c["approval_id"]}) == 3


def test_dedupe_returns_current_state_after_signoff(tmp_path):
    a = _req(tmp_path, idempotency_key="k1")
    record_contract_approval(
        tmp_path, approval_id=a["approval_id"], signer="alex",
        decision="approved", log_root=_root(tmp_path))
    b = _req(tmp_path, idempotency_key="k1")
    assert b["deduplicated"] is True
    assert b["approval_id"] == a["approval_id"]


def test_action_summary_persisted_and_listed(tmp_path):
    _req(tmp_path, idempotency_key="k1", action_summary="send report to client")
    rows = list_contract_approvals(tmp_path, log_root=_root(tmp_path))
    assert rows[0]["action_summary"] == "send report to client"


def test_idempotency_through_gateway_facade(tmp_path, monkeypatch):
    """The gateway path (what n8n actually calls) must dedupe too."""
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(_root(tmp_path)))
    from rvnd import gateway as gw
    p = {"folder_context": str(tmp_path), "contract_id": "g4",
         "signers": ["alex"], "requested_by": "n8n",
         "action_summary": "post summary", "idempotency_key": "flow-123"}
    a = gw.workspace_contract("request_approval", p)
    b = gw.workspace_contract("request_approval", p)
    assert a["ok"] and b["ok"]
    assert a["approval_id"] == b["approval_id"]
    assert b["deduplicated"] is True
    # receipts still attach on the deduplicated reply
    assert b["gateway_meta"]["gateway_version"]
