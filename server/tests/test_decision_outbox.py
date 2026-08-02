# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Decision outbox, escalation ladder and write re-confirm.

Claims under test (written before the logic):
  N1  notify mints a personal link per holder (A's token never equals B's),
      delivers to each registered channel, skips the raiser, and records
      every per-channel result on the entry and the chain
  N2  the delivered message is minimised — title + deep link only; the
      question and options never leave
  N3  a Lock-refused egress sends nothing and is recorded as a refusal
  N4  per-channel failure honesty: one dead channel records ok=False while
      the other holder's delivery records ok=True
  N5  the declared ladder widens an unclaimed decision after its window —
      competence changes, the basis says why, renotify_due flags — and a
      claimed decision never escalates
  N6  write_reconfirm: recording through a link without the code refuses;
      the code arrives via the holder's own channel, verifies once, and the
      write passes with it
  N7  the facade routes decision_notify and decision_reconfirm_request
  N8  wrong guesses are recorded on the chain; at the miss cap the live
      code voids — even the right code is then refused, and only a fresh
      mint helps
  N9  a fresh mint supersedes the previous unused code, so at most one
      code per party is guessable at any time
  N10 the confirmation code leaves through the Lock's egress gate — a
      refusal sends nothing

Run: python -m pytest server/tests/test_decision_outbox.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import workspaces.decisions.outbox as OB
import workspaces.decisions.queue as DQ
import workspaces.mcp_server as S
from workspaces.mcp_impl import (decision_claim, decision_open,
                                 decision_pending,
                                 decision_reconfirm_request, decision_record)
from workspaces.parties import register_party

SURFACE = {
    "query": "Erase the record while invoices sit in the retention window?",
    "options": [{"id": "erase", "label": "Erase", "conclusion": "erase",
                 "supporting": [], "consequences": []},
                {"id": "split", "label": "Split", "conclusion": "split",
                 "supporting": [], "consequences": []}],
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    folder = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    log = str(tmp_path / "log")
    register_party(folder, party_id="dana", kind="human",
                   competences=["data-protection"],
                   channels=["email:dana\x40corp.example"],
                   actor="alex", log_root=log)
    register_party(folder, party_id="jonas", kind="human",
                   competences=["data-protection"],
                   channels=["slack:https://hooks.example/j"],
                   actor="alex", log_root=log)
    sent = []

    def fake_sender(address, message):
        sent.append({"address": address, "message": dict(message)})
        return {"ok": True, "detail": "sent"}
    senders = {"email": fake_sender, "slack": fake_sender}
    return {"folder": folder, "log": log, "sent": sent, "senders": senders}


def opened(env, **kw):
    return decision_open(env["folder"], SURFACE, "crm-bot",
                         competence="data-protection", auto_notify=False,
                         **kw)["decision_id"]


def test_per_holder_links_and_recorded_results(env):             # N1
    did = opened(env)
    out = OB.notify(env["folder"], did, log_root=env["log"],
                    senders=env["senders"])
    assert out["ok"] and out["holders"] == 2
    assert all(r["ok"] for r in out["sent"])
    tokens = {s["message"]["deep_link"] for s in env["sent"]}
    assert len(tokens) == 2, "each holder gets their own link"
    assert not any("crm-bot" in json.dumps(s) for s in env["sent"])
    row = decision_pending(env["folder"])["pending"][0]
    assert row["notified_ok"] == 2


def test_message_is_minimised(env):                              # N2
    did = opened(env)
    OB.notify(env["folder"], did, log_root=env["log"], senders=env["senders"])
    blob = json.dumps(env["sent"])
    assert "Erase the record" not in blob and "split" not in blob
    assert "A decision waits" in blob and "token=" in blob


def test_lock_refusal_sends_nothing(env, monkeypatch):           # N3
    did = opened(env)
    monkeypatch.setattr(DQ.DecisionQueue, "notification",
                        lambda self, e: {"payload": None, "egress": "refused",
                                         "egress_detail": "policy"})
    out = OB.notify(env["folder"], did, log_root=env["log"],
                    senders=env["senders"])
    assert out["ok"] is False and "nothing" in out["error"]
    assert env["sent"] == []


def test_per_channel_failure_honesty(env):                       # N4
    did = opened(env)

    def dead(address, message):
        raise_detail = {"ok": False, "detail": "connection refused"}
        return raise_detail
    out = OB.notify(env["folder"], did, log_root=env["log"],
                    senders={"email": dead, "slack": env["senders"]["slack"]})
    by_kind = {r["channel"]: r["ok"] for r in out["sent"]}
    assert by_kind == {"email": False, "slack": True}


def test_ladder_widens_unclaimed_only(env):                      # N5
    did = opened(env, escalate_to="management", escalate_after_s=60)
    q = DQ.DecisionQueue(env["folder"])
    q.items[did]["opened_at"] = (datetime.now(timezone.utc)
                                 - timedelta(seconds=120)).isoformat()
    q._flush()
    row = decision_pending(env["folder"])["pending"][0]
    assert row["competence"] == "management"
    assert "escalated from data-protection" in row["assignment_basis"]
    assert row["renotify_due"] is True
    # a claimed decision never escalates
    did2 = opened(env, escalate_to="management", escalate_after_s=60)
    decision_claim(env["folder"], decision_id=did2, actor="dana")
    q2 = DQ.DecisionQueue(env["folder"])
    q2.items[did2]["opened_at"] = (datetime.now(timezone.utc)
                                   - timedelta(seconds=120)).isoformat()
    q2._flush()
    rows = {r["decision_id"]: r for r in decision_pending(env["folder"])["pending"]}
    assert rows[did2]["competence"] == "data-protection"


def test_write_reconfirm_guards_the_link_write(env, monkeypatch):  # N6
    did = opened(env, write_reconfirm=True)
    tok = S.workspace_dispatch("decision_link_mint", {
        "folder_context": env["folder"], "decision_id": did,
        "party_id": "dana"})["token"]
    bare = decision_record(env["folder"], chosen_option_id="split",
                           rationale="fine", link_token=tok)
    assert bare["ok"] is False and "confirmation code" in bare["error"]
    monkeypatch.setattr(OB, "SENDERS", {"email": env["senders"]["email"]})
    req = decision_reconfirm_request(env["folder"], tok)
    assert req["ok"] and req["sent"][0]["ok"]
    code = env["sent"][-1]["message"]["deep_link"].split("code: ")[1]
    ok = decision_record(env["folder"], chosen_option_id="split",
                         rationale="fine", link_token=tok,
                         reconfirm_code=code)
    assert ok["ok"] is True and ok["auth_rung"] == "channel-link"
    reuse = DQ.DecisionQueue(env["folder"]).verify_reconfirm(did, "dana", code)
    assert reuse["ok"] is False, "the confirmation code is single-use"


def test_facade_routes(env, monkeypatch):                        # N7
    did = opened(env)
    monkeypatch.setattr(OB, "SENDERS", env["senders"])
    out = S.workspace_dispatch("decision_notify", {
        "folder_context": env["folder"], "decision_id": did})
    assert out["ok"] and out["holders"] == 2
    ops = {o["op"] for o in S.workspace_dispatch("help")["ops"]}
    assert {"decision_notify", "decision_reconfirm_request"} <= ops


def test_miss_cap_voids_the_code(env):                           # N8
    did = opened(env, write_reconfirm=True)
    q = DQ.DecisionQueue(env["folder"], log_root=env["log"])
    code = q.mint_reconfirm(did, "dana")["code"]
    wrong = "000000" if code != "000000" else "000001"
    out = {}
    for _ in range(DQ.DecisionQueue.RECONFIRM_MISS_CAP):
        out = q.verify_reconfirm(did, "dana", wrong)
        assert out["ok"] is False
    assert "voided" in out["error"], "the cap voids the live code"
    right = q.verify_reconfirm(did, "dana", code)
    assert right["ok"] is False and "voided" in right["error"], \
        "a voided code is dead even when finally guessed"
    fresh = q.mint_reconfirm(did, "dana")["code"]
    assert q.verify_reconfirm(did, "dana", fresh)["ok"] is True
    from pathlib import Path as P

    from workspaces.mutation_log import MutationLog
    kinds = [(e.extra or {}).get("kind") for e in
             MutationLog(P(env["folder"]), log_root=P(env["log"])).replay()]
    assert "decision.reconfirm_failed" in kinds


def test_fresh_mint_supersedes(env):                             # N9
    did = opened(env, write_reconfirm=True)
    q = DQ.DecisionQueue(env["folder"], log_root=env["log"])
    old = q.mint_reconfirm(did, "dana")["code"]
    new = q.mint_reconfirm(did, "dana")["code"]
    stale = q.verify_reconfirm(did, "dana", old, consume=False)
    if old != new:                    # equal codes: a 1e-6 collision, skip
        assert stale["ok"] is False and "newer" in stale["error"]
    assert q.verify_reconfirm(did, "dana", new)["ok"] is True


def test_reconfirm_code_egress_is_gated(env, monkeypatch):       # N10
    did = opened(env, write_reconfirm=True)
    tok = S.workspace_dispatch("decision_link_mint", {
        "folder_context": env["folder"], "decision_id": did,
        "party_id": "dana"})["token"]
    import workspaces.mcp_impl as MI
    monkeypatch.setattr(OB, "SENDERS", {"email": env["senders"]["email"]})
    monkeypatch.setattr(MI, "lock_egress_check",
                        lambda **kw: {"action": "refuse", "reason": "policy"})
    out = decision_reconfirm_request(env["folder"], tok)
    assert out["ok"] is False and "nothing was sent" in out["error"]
    assert env["sent"] == []
