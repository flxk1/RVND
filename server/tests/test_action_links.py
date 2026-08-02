# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Action links — the registered channel is the credential (identity rung 1).

A link token authenticates its holder as one party for one open decision:
signed with the workspace keypair, single-use, short-lived, dead when a
competing claim takes the card. The record carries the rung, so the chain
states how strongly the actor was known.

Claims under test (written before the logic):
  L1  mint returns the token exactly once and stores only its hash; no link
      is minted for the raiser or for a missing/closed decision
  L2  a valid token claims and records as the bound party, and both events
      carry auth_rung="channel-link"
  L3  single-use: the token is spent by the successful write — a failed write
      (empty rationale) does NOT burn it; reuse after success is refused
  L4  expiry refuses in words; a tampered or foreign-signed token refuses
  L5  a competing claim invalidates other parties' unspent links
  L6  rung-0 regression: ops without tokens behave exactly as before and
      carry no auth_rung
  L7  the facade routes decision_link_mint and link_token on claim/record

Run: python -m pytest server/tests/test_action_links.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import workspaces.decisions.queue as DQ
import workspaces.mcp_server as S
from workspaces.mcp_impl import (decision_claim, decision_link_mint,
                                 decision_open, decision_record)

SURFACE = {
    "query": "Erase the record while invoices sit in the retention window?",
    "options": [
        {"id": "erase", "label": "Erase everything now", "conclusion": "erase",
         "supporting": [], "consequences": []},
        {"id": "split", "label": "Split the records", "conclusion": "split",
         "supporting": [], "consequences": []},
    ],
}


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    return str(tmp_path)


def opened(folder) -> str:
    return decision_open(folder, SURFACE, "crm-bot")["decision_id"]


def test_mint_returns_token_once_stores_hash(folder):            # L1
    did = opened(folder)
    out = decision_link_mint(folder, did, "dana")
    assert out["ok"] and out["token"] and out["audit_id"]
    entry = DQ.DecisionQueue(folder).items[did]
    assert entry["links"][0]["hash"] != out["token"]
    assert out["token"] not in json.dumps(entry)
    assert decision_link_mint(folder, did, "crm-bot")["ok"] is False   # raiser
    assert decision_link_mint(folder, "dec-none", "dana")["ok"] is False


def test_token_claims_and_records_with_rung(folder):             # L2
    did = opened(folder)
    tok = decision_link_mint(folder, did, "dana")["token"]
    c = decision_claim(folder, link_token=tok)
    assert c["ok"] and c["claimed_by"] == "dana"
    out = decision_record(folder, chosen_option_id="split",
                          rationale="both duties held", link_token=tok)
    assert out["ok"] and out["actor"] == "dana"
    assert out["auth_rung"] == "channel-link"
    assert out["decision_id"] == did


def test_single_use_spent_only_by_success(folder):               # L3
    did = opened(folder)
    tok = decision_link_mint(folder, did, "dana")["token"]
    failed = decision_record(folder, chosen_option_id="split",
                             rationale="", link_token=tok)
    assert failed["ok"] is False, "empty rationale must refuse"
    ok = decision_record(folder, chosen_option_id="split",
                         rationale="fine", link_token=tok)
    assert ok["ok"] is True, "a failed write must not burn the link"
    # decision now closed — any reuse refuses (closed decision wins the wording)
    again = decision_record(folder, chosen_option_id="erase",
                            rationale="again", link_token=tok)
    assert again["ok"] is False


def test_expiry_and_tampering_refuse(folder):                    # L4
    did = opened(folder)
    tok = decision_link_mint(folder, did, "dana", ttl_s=60)["token"]
    q = DQ.DecisionQueue(folder)
    q.items[did]["links"][0]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    q._flush()
    out = decision_claim(folder, link_token=tok)
    assert out["ok"] is False and "expired" in out["error"]
    body_hex, sig = tok.split(".", 1)
    forged = bytes.fromhex(body_hex).replace(b"dana", b"mall").hex() + "." + sig
    assert decision_claim(folder, link_token=forged)["ok"] is False


def test_competing_claim_invalidates_foreign_links(folder):      # L5
    did = opened(folder)
    tok_d = decision_link_mint(folder, did, "dana")["token"]
    tok_j = decision_link_mint(folder, did, "jonas")["token"]
    assert decision_claim(folder, link_token=tok_d)["ok"]
    out = decision_claim(folder, link_token=tok_j)
    assert out["ok"] is False and "claimed by" in out["error"]
    entry = DQ.DecisionQueue(folder).items[did]
    jonas_rec = next(r for r in entry["links"] if r["party_id"] == "jonas")
    assert jonas_rec["invalidated"] == "claimed by dana"


def test_rung0_regression_no_rung_without_token(folder):         # L6
    did = opened(folder)
    assert decision_claim(folder, decision_id=did, actor="dana")["ok"]
    out = decision_record(folder, chosen_option_id="split",
                          rationale="fine", actor="dana", decision_id=did)
    assert out["ok"] is True and "auth_rung" not in out


def test_facade_routes(folder):                                  # L7
    did = S.workspace_dispatch("decision_open", {"folder_context": folder,
        "surface": SURFACE, "raised_by": "crm-bot"})["decision_id"]
    tok = S.workspace_dispatch("decision_link_mint", {"folder_context": folder,
        "decision_id": did, "party_id": "dana"})["token"]
    c = S.workspace_dispatch("decision_claim", {"folder_context": folder,
        "link_token": tok})
    assert c["ok"] and c["claimed_by"] == "dana"
    ops = {o["op"] for o in S.workspace_dispatch("help")["ops"]}
    assert "decision_link_mint" in ops
