# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""n-person co-decision panels: seats record independently and sealed; a met
rule closes once; a split never averages.

Claims under test (written before the logic):
  P1  a panel spec is validated at open (seats >= 2, known rule, m bounds)
  P2  seat leases are independent: two parties hold seats concurrently; a
      third is refused when all seats are taken; the raiser is refused a seat
  P3  sealed until resolution: no projection (pending row, claim response,
      seat-record response) carries another seat's choice or rationale; the
      chain pre-resolution says who recorded plus a commitment, never what
  P4  m_concordant resolves the moment an option holds m records: ONE closing
      chain event references every seat's now-unsealed choice; the entry
      closes; the closing record's actor names the panel
  P5  unanimous splits early on the first discordant record; any_m splits on
      discord at quorum
  P6  a split escalates up the declared ladder with the seat records attached
      to the chain and a fresh panel opens; without a ladder it re-opens
  P7  a seat records once — re-recording is refused; a seat's action link is
      spent by its own successful seat write

Run: python -m pytest server/tests/test_decision_panels.py -q
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import rvnd.decisions.queue as DQ
import rvnd.mcp_server as S
from rvnd.mcp_impl import (decision_claim, decision_open,
                                 decision_pending, decision_record)

SURFACE = {
    "query": "Erase the record while invoices sit in the retention window?",
    "options": [{"id": "erase", "label": "Erase", "conclusion": "erase",
                 "supporting": [], "consequences": []},
                {"id": "split", "label": "Split", "conclusion": "split",
                 "supporting": [], "consequences": []}],
}


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    return str(tmp_path / "ws")


def opened(folder, panel, **kw):
    return decision_open(folder, SURFACE, "crm-bot", auto_notify=False,
                         panel=panel, **kw)


def chain_kinds(folder):
    from rvnd.mutation_log import MutationLog
    return [(e.extra or {}) for e in
            MutationLog(Path(folder),
                        log_root=Path(os.environ["WORKSPACE_L0_LOG_ROOT"])).replay()
            if (e.extra or {}).get("kind", "").startswith("decision.")]


def test_panel_spec_validated(folder):                           # P1
    assert "2 seats" in opened(folder, {"seats": 1, "rule": "unanimous"})["error"]
    assert "rule" in opened(folder, {"seats": 3, "rule": "vibes"})["error"]
    assert "m between" in opened(folder, {"seats": 3, "rule": "any_m", "m": 9})["error"]
    assert opened(folder, {"seats": 3, "rule": "m_concordant", "m": 2})["ok"]


def test_seat_leases_independent(folder):                        # P2
    did = opened(folder, {"seats": 2, "rule": "unanimous"})["decision_id"]
    a = decision_claim(folder, decision_id=did, actor="dana")
    b = decision_claim(folder, decision_id=did, actor="jonas")
    assert a["ok"] and b["ok"] and a.get("seat") and b.get("seat")
    c = decision_claim(folder, decision_id=did, actor="mia")
    assert c["ok"] is False and "seats are taken" in c["error"]
    r = decision_claim(folder, decision_id=did, actor="crm-bot")
    assert r["ok"] is False and "separation of duties" in r["error"]


def test_sealed_until_resolution(folder):                        # P3
    did = opened(folder, {"seats": 3, "rule": "m_concordant", "m": 2})["decision_id"]
    out = decision_record(folder, chosen_option_id="erase",
                          rationale="my private grounds", actor="dana",
                          decision_id=did)
    assert out["ok"] and out.get("sealed") is True
    assert "erase" not in json.dumps(out.get("panel"))
    row = next(r for r in decision_pending(folder)["pending"]
               if r["decision_id"] == did)
    blob = json.dumps(row)
    assert "my private grounds" not in blob and row["panel"]["recorded"] == 1
    claim = decision_claim(folder, decision_id=did, actor="jonas")
    assert "my private grounds" not in json.dumps(claim)
    pre = [e for e in chain_kinds(folder)
           if e["kind"] == "decision.seat_recorded"]
    assert pre and "commitment" in pre[0] and \
        "my private grounds" not in json.dumps(pre)


def test_concordant_resolution_closes_once(folder):              # P4
    did = opened(folder, {"seats": 3, "rule": "m_concordant", "m": 2})["decision_id"]
    decision_record(folder, chosen_option_id="split", rationale="duty A",
                    actor="dana", decision_id=did)
    out = decision_record(folder, chosen_option_id="split", rationale="duty B",
                          actor="jonas", decision_id=did)
    assert out["ok"] is True and out["chosen_option_id"] == "split"
    assert out["actor"] == "panel(dana,jonas)"
    assert DQ.DecisionQueue(folder).items[did]["state"] == "decided"
    events = chain_kinds(folder)
    resolved = [e for e in events if e["kind"] == "decision.panel_resolved"]
    assert len(resolved) == 1
    seat_ids = resolved[0]["seat_audit_ids"]
    choices = [e for e in events if e["kind"] == "decision.seat_choice"]
    assert len(choices) == 2 == len(seat_ids)
    assert {c["rationale"] for c in choices} == {"duty A", "duty B"}


def test_unanimous_and_quorum_split_early(folder):               # P5
    did = opened(folder, {"seats": 3, "rule": "unanimous"})["decision_id"]
    decision_record(folder, chosen_option_id="erase", rationale="r",
                    actor="dana", decision_id=did)
    out = decision_record(folder, chosen_option_id="split", rationale="r",
                          actor="jonas", decision_id=did)
    assert out.get("split") is True, "one discordant record makes unanimity impossible"
    did2 = opened(folder, {"seats": 4, "rule": "any_m", "m": 2})["decision_id"]
    decision_record(folder, chosen_option_id="erase", rationale="r",
                    actor="dana", decision_id=did2)
    out2 = decision_record(folder, chosen_option_id="split", rationale="r",
                           actor="jonas", decision_id=did2)
    assert out2.get("split") is True, "discord at quorum splits under any_m"


def test_split_escalates_with_records_attached(folder):          # P6
    did = opened(folder, {"seats": 2, "rule": "unanimous"},
                 competence="data-protection",
                 escalate_to="management")["decision_id"]
    decision_record(folder, chosen_option_id="erase", rationale="ground A",
                    actor="dana", decision_id=did)
    out = decision_record(folder, chosen_option_id="split", rationale="ground B",
                          actor="jonas", decision_id=did)
    assert out["split"] is True and out["escalated"] is True
    entry = DQ.DecisionQueue(folder).items[did]
    assert entry["state"] == "open" and entry["competence"] == "management"
    assert entry["panel"]["seat_records"] == [], "a fresh panel opens"
    assert entry["panel_history"][0]["counts"] == {"erase": 1, "split": 1}
    split_ev = [e for e in chain_kinds(folder)
                if e["kind"] == "decision.panel_split"]
    assert {r["rationale"] for r in split_ev[0]["seat_records"]} == \
        {"ground A", "ground B"}
    # without a ladder: split re-opens in place
    did2 = opened(folder, {"seats": 2, "rule": "unanimous"})["decision_id"]
    decision_record(folder, chosen_option_id="erase", rationale="r",
                    actor="dana", decision_id=did2)
    out2 = decision_record(folder, chosen_option_id="split", rationale="r",
                           actor="jonas", decision_id=did2)
    assert out2["split"] is True and out2["escalated"] is False
    assert DQ.DecisionQueue(folder).items[did2]["state"] == "open"


def test_seat_records_once_and_link_spent(folder):               # P7
    did = opened(folder, {"seats": 3, "rule": "m_concordant", "m": 3})["decision_id"]
    decision_record(folder, chosen_option_id="erase", rationale="r",
                    actor="dana", decision_id=did)
    again = decision_record(folder, chosen_option_id="split", rationale="r2",
                            actor="dana", decision_id=did)
    assert again["ok"] is False and "decides once" in again["error"]
    tok = S.workspace_dispatch("decision_link_mint", {
        "folder_context": folder, "decision_id": did,
        "party_id": "jonas"})["token"]
    out = decision_record(folder, chosen_option_id="erase", rationale="r",
                          link_token=tok)
    assert out["ok"] and out.get("sealed") is True
    reuse = decision_record(folder, chosen_option_id="erase", rationale="r",
                            link_token=tok)
    assert reuse["ok"] is False, "a seat's link is spent by its seat write"
