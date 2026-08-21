# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Decision projections: the reference cards, the conformance profile, and
the decide-from-chat response endpoint.

Claims under test (written before the logic):
  J1  the card payload is a gated egress with its fields declared — a Lock
      refusal yields no card and says the deep-link flow remains
  J2  both reference cards (Teams Adaptive Card, Slack Block Kit) pass the
      conformance profile against their surface
  J3  a non-conformant projection is refused with the failing clause named:
      reordered options, a pre-selected option, an emphasised action, and a
      missing required free-text rationale each name their clause
  J4  Teams round-trip: card → Action.Submit body → /decision/respond →
      recorded with auth_rung=channel-link; the pending entry closes
  J5  Slack round-trip: card → interactivity payload → /decision/respond →
      recorded
  J6  the endpoint refuses a tokenless or unrecognisable body and an empty
      rationale in words — a platform identity alone never writes

Run: python -m pytest server/tests/test_decision_projections.py -q
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "app"))
import serve  # noqa: E402

import rvnd.decisions.projections as DP
import rvnd.mcp_server as S
from rvnd.decisions.queue import DecisionQueue

SURFACE = {
    "query": "Erase the record while invoices sit in the retention window?",
    "esc_reason": "GDPR Art. 17(1) erase vs § 147(3) AO keep-ten-years",
    "options": [{"id": "erase", "label": "Erase", "conclusion": "erase now",
                 "supporting": [], "consequences": []},
                {"id": "split", "label": "Split", "conclusion": "split records",
                 "supporting": [], "consequences": []}],
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    folder = str(tmp_path / "ws"); (tmp_path / "ws").mkdir()
    did = S.workspace_dispatch("decision_open", {
        "folder_context": folder, "surface": SURFACE, "raised_by": "crm-bot",
        "auto_notify": False})["decision_id"]
    tok = S.workspace_dispatch("decision_link_mint", {
        "folder_context": folder, "decision_id": did,
        "party_id": "dana"})["token"]
    entry = DecisionQueue(folder).get(did)
    return {"folder": folder, "did": did, "tok": tok, "entry": entry}


def gated_payload(env):
    out = DP.card_payload(env["folder"], env["entry"], env["tok"])
    assert out["ok"], out
    return out["payload"]


def test_card_egress_is_gated(env, monkeypatch):                 # J1
    assert gated_payload(env)["options"][0]["id"] == "erase"
    monkeypatch.setattr(DP, "_gate_card",
                        lambda folder, payload: {"action": "refuse",
                                                 "reason": "policy"})
    out = DP.card_payload(env["folder"], env["entry"], env["tok"])
    assert out["ok"] is False and "deep-link" in out["error"]


def test_reference_cards_conform(env):                           # J2
    payload = gated_payload(env)
    for card in (DP.teams_card(payload), DP.slack_blocks(payload)):
        v = DP.verify_projection(SURFACE, card)
        assert v["ok"], v["failures"]


def test_nonconformance_names_the_clause(env):                   # J3
    payload = gated_payload(env)
    reordered = DP.teams_card({**payload,
                               "options": list(reversed(payload["options"]))})
    v = DP.verify_projection(SURFACE, reordered)
    assert not v["ok"] and any("server-order" in f for f in v["failures"])

    presel = DP.teams_card(payload)
    next(b for b in presel["body"]
         if b["type"] == "Input.ChoiceSet")["value"] = "split"
    v = DP.verify_projection(SURFACE, presel)
    assert any("no-preselection" in f for f in v["failures"])

    pushy = DP.teams_card(payload)
    pushy["actions"][0]["style"] = "positive"
    v = DP.verify_projection(SURFACE, pushy)
    assert any("no-recommendation" in f for f in v["failures"])

    lax = DP.teams_card(payload)
    next(b for b in lax["body"]
         if b["type"] == "Input.Text")["isRequired"] = False
    v = DP.verify_projection(SURFACE, lax)
    assert any("rationale-required" in f for f in v["failures"])


def _post(port, path, body: bytes, content_type: str):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                 headers={"Content-Type": content_type})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


@pytest.fixture()
def bridge():
    srv = serve.make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def test_teams_roundtrip_records(env, bridge):                   # J4
    payload = gated_payload(env)
    card = DP.teams_card(payload)
    submit = {"data": card["actions"][0]["data"],
              "chosen_option_id": "split",
              "rationale": "the split follows both duties"}
    out = _post(bridge, "/decision/respond",
                json.dumps(submit).encode(), "application/json")
    assert out["ok"] is True and out["actor"] == "dana"
    assert out["auth_rung"] == "channel-link"
    assert DecisionQueue(env["folder"]).items[env["did"]]["state"] == "decided"


def test_slack_roundtrip_records(env, bridge):                   # J5
    payload = gated_payload(env)
    card = DP.slack_blocks(payload)
    button = card["blocks"][-1]["elements"][0]
    slack_payload = {
        "actions": [{"action_id": "record", "value": button["value"]}],
        "state": {"values": {
            "choice": {"chosen_option_id":
                       {"selected_option": {"value": "erase"}}},
            "rationale": {"rationale": {"value": "erasure holds — retention"
                                                 " already satisfied"}}}}}
    out = _post(bridge, "/decision/respond",
                urlencode({"payload": json.dumps(slack_payload)}).encode(),
                "application/x-www-form-urlencoded")
    assert out["ok"] is True and out["chosen_option_id"] == "erase"


def test_endpoint_refuses_tokenless_and_empty_rationale(env, bridge):  # J6
    out = _post(bridge, "/decision/respond", b"not json", "application/json")
    assert out["ok"] is False and "action-link token" in out["error"]
    payload = gated_payload(env)
    submit = {"data": DP.teams_card(payload)["actions"][0]["data"],
              "chosen_option_id": "split", "rationale": ""}
    out = _post(bridge, "/decision/respond",
                json.dumps(submit).encode(), "application/json")
    assert out["ok"] is False and "rationale" in out["error"]
