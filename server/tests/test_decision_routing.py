# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Decision routing: pending decisions find their competent human through
recorded steps — competence from the escalation, holders via the resolver,
dispatch by claim-lease. The raiser never decides their own escalation.

Claims under test (written before the logic):
  R1  open persists the entry with its assignment basis and returns only the
      minimised notification (title + id + deep link — never the question or
      an option); opening without options or a raising actor is refused
  R2  pending lists open decisions with claim state; for_party excludes the
      raiser and, when a competence is set, non-holders
  R3  the two-reviewer race: the first claim leases, the second is refused BY
      NAME while the lease holds; release widens it back; only the claimant
      may release
  R4  an expired lease releases automatically and the expiry is recorded
  R5  separation of duties: the raiser can neither claim nor record; a
      foreign live claim blocks recording
  R6  recording with decision_id uses the STORED surface, closes the entry
      (gone from pending) and stamps the choice's audit id on the closure
  R7  the facade routes all four ops and help documents them

Run: python -m pytest server/tests/test_decision_routing.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import workspaces.decisions.queue as DQ
import workspaces.mcp_server as S
from workspaces.mcp_impl import (decision_claim, decision_open,
                                 decision_pending, decision_record,
                                 decision_release)
from workspaces.parties import register_party

SURFACE = {
    "query": "Erase K.'s record while invoices sit in the retention window?",
    "esc_reason": "GDPR Art. 17(1) erase vs § 147(3) AO keep-ten-years",
    "options": [
        {"id": "erase", "label": "Erase everything now",
         "conclusion": "erase", "supporting": [], "consequences": []},
        {"id": "split", "label": "Split the records",
         "conclusion": "split", "supporting": [], "consequences": []},
    ],
}


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    return str(tmp_path)


def test_open_records_basis_and_minimises_notification(folder):  # R1
    out = decision_open(folder, SURFACE, "crm-bot", competence="data-protection")
    assert out["ok"] is True and out["decision_id"].startswith("dec-")
    note = out["notification"]
    assert note["egress"] == "permitted", \
        "the minimised payload must pass the gate — its fields are declared"
    if note["payload"] is not None:
        blob = json.dumps(note["payload"])
        assert "deep_link" in note["payload"] and "decision_id" in note["payload"]
        assert "erase" not in blob and "17(1)" not in blob, \
            "the notification must never carry the question or an option"
    assert decision_open(folder, {"options": []}, "crm-bot")["ok"] is False
    assert decision_open(folder, SURFACE, "")["ok"] is False


def test_pending_routes_by_party(folder):                        # R2
    log = folder + "/log"
    register_party(folder, party_id="dpo", kind="human", name="Dana",
                   competences=["data-protection"], actor="alex", log_root=log)
    register_party(folder, party_id="dev", kind="human", name="Devin",
                   competences=["engineering"], actor="alex", log_root=log)
    did = decision_open(folder, SURFACE, "crm-bot",
                        competence="data-protection")["decision_id"]
    everyone = decision_pending(folder)
    assert [e["decision_id"] for e in everyone["pending"]] == [did]
    assert everyone["pending"][0]["assignment_basis"] == "competence data-protection"
    assert decision_pending(folder, for_party="dpo")["pending"], \
        "the competence holder must see it"
    assert not decision_pending(folder, for_party="dev")["pending"], \
        "a non-holder must not"
    assert not decision_pending(folder, for_party="crm-bot")["pending"], \
        "the raiser must not (separation of duties)"


def test_claim_race_and_release(folder):                         # R3
    did = decision_open(folder, SURFACE, "crm-bot")["decision_id"]
    first = decision_claim(folder, did, "dana")
    assert first["ok"] is True and first["claimed_by"] == "dana"
    assert first["surface"]["query"] == SURFACE["query"]
    second = decision_claim(folder, did, "devin")
    assert second["ok"] is False and "'dana'" in second["error"]
    assert decision_release(folder, did, "devin")["ok"] is False
    assert decision_release(folder, did, "dana")["ok"] is True
    again = decision_claim(folder, did, "devin")
    assert again["ok"] is True and again["claimed_by"] == "devin"


def test_expired_lease_releases(folder):                         # R4
    did = decision_open(folder, SURFACE, "crm-bot", claim_ttl_s=60)["decision_id"]
    decision_claim(folder, did, "dana")
    q = DQ.DecisionQueue(folder)
    q.items[did]["claim_expires_at"] = (datetime.now(timezone.utc)
                                        - timedelta(seconds=1)).isoformat()
    q._flush()
    row = decision_pending(folder)["pending"][0]
    assert row["claimed_by"] is None, "an expired lease must widen the decision"


def test_separation_of_duties_holds(folder):                     # R5
    did = decision_open(folder, SURFACE, "crm-bot")["decision_id"]
    assert decision_claim(folder, did, "crm-bot")["ok"] is False
    raiser = decision_record(folder, chosen_option_id="split",
                             rationale="fine", actor="crm-bot", decision_id=did)
    assert raiser["ok"] is False and "separation of duties" in raiser["error"]
    decision_claim(folder, did, "dana")
    foreign = decision_record(folder, chosen_option_id="split",
                              rationale="fine", actor="devin", decision_id=did)
    assert foreign["ok"] is False and "'dana'" in foreign["error"]


def test_record_closes_the_pending_entry(folder):                # R6
    did = decision_open(folder, SURFACE, "crm-bot")["decision_id"]
    decision_claim(folder, did, "dana")
    out = decision_record(folder, chosen_option_id="split",
                          rationale="Art. 17(3)(b) carves out the retention duty",
                          actor="dana", considered=["split"], decision_id=did)
    assert out["ok"] is True and out["decision_id"] == did
    assert out["chosen_label"] == "Split the records", \
        "the STORED surface must be the one recorded against"
    assert decision_pending(folder)["pending"] == []
    entry = DQ.DecisionQueue(folder).items[did]
    assert entry["state"] == "decided" and entry["decided_by"] == "dana"


def test_facade_routes_and_documents(folder):                    # R7
    did = S.workspace_dispatch("decision_open", {
        "folder_context": folder, "surface": SURFACE,
        "raised_by": "crm-bot"})["decision_id"]
    assert S.workspace_dispatch("decision_claim", {
        "folder_context": folder, "decision_id": did, "actor": "dana"})["ok"]
    assert S.workspace_dispatch("decision_pending", {
        "folder_context": folder})["pending"][0]["claimed_by"] == "dana"
    assert S.workspace_dispatch("decision_release", {
        "folder_context": folder, "decision_id": did, "actor": "dana"})["ok"]
    ops = {o["op"] for o in S.workspace_dispatch("help")["ops"]}
    assert {"decision_open", "decision_pending",
            "decision_claim", "decision_release"} <= ops
