# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The decision dossier — one pending decision's local context, read-only.

Claims under test (written before the logic):
  D1  full assembly: item routing state, the stored surface with banded
      grounding, the raiser's attributed runs, standing (roster record +
      verdict tally + overrides), and recourse (ladder + obligations/redress/
      reserved acts on the raiser's use cases), each join labelled attributed
  D2  no raw grounding score anywhere in the payload — bands only
  D3  unknown and closed ids refuse in words (fail-closed)
  D4  a read with no queue upkeep due appends nothing to the chain
      (lazy lease expiry / ladder escalation may record, as on any queue read)
  D5  degraded records stay visible as such: no runs → empty + labelled;
      an unregistered raiser → standing flagged unregistered, never invented
  D6  panel seats stay sealed pre-resolution: counts + commitments only,
      never a seat's choice or rationale
  D7  the workspace_dispatch facade routes the op and help documents it

Run: python -m pytest server/tests/test_decision_dossier.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from rvnd.decisions.dossier import decision_dossier
from rvnd.decisions.queue import DecisionQueue
from rvnd.mutation_log import MutationLog
from rvnd.operations import operate
from rvnd.parties import register_party
from rvnd.review_card import record_override
from rvnd.use_case import register_use_case

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

SURFACE = {
    "query": "Erase K.'s record while invoices sit in the retention window?",
    "esc_reason": "GDPR Art. 17(1) erase vs § 147(3) AO keep-ten-years",
    "single_reading_warning": False,
    "options": [
        {"id": "erase", "label": "Erase everything now", "conclusion": "erase",
         "supporting": [{"pinpoint": "Art. 17(1) GDPR", "text": "erasure"}],
         "grounding": 0.95, "consequences": []},
        {"id": "split", "label": "Split the records", "conclusion": "split",
         "supporting": [], "grounding": 0.4, "consequences": []},
    ],
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A folder with a registered raiser, a governed use case carrying
    obligations + redress + a policy reservation, one journalled run, and one
    override recorded by the raiser."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    f = str(tmp_path / "ws")
    lr = str(tmp_path / "log")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", lr)
    register_party(f, "crm-bot", "agent", owner="ops", purpose="crm triage",
                   grade="L2", competences=[], log_root=lr)
    register_party(f, "dpo", "human", name="Dana",
                   competences=["data-protection"], log_root=lr)
    register_use_case(
        f, use_case_id="uc-crm", name="CRM erasure triage",
        fingerprint={"issue_type": "data_processing", "profile": "legal-de",
                     "rooms": []},
        risk="low", allowed_agents=["crm-bot"], actor="dpo",
        prior_approvals=20,
        obligations=[{"duty": "notify the data subject"}],
        redress=[{"route": "DPO review", "window_days": 30}],
        policy_reservations={"generated_content": {
            "reserved_to": "moderator", "act_type": "approve"}},
        log_root=lr)
    operate(f, use_case_id="uc-crm", agent_id="crm-bot",
            issues=[{"issue_id": "i1", "issue_type": "formatting_fix",
                     "completeness": "high"}],
            now_epoch=1000, log_root=lr)
    record_override(f, card={"node_id": "n1", "stage": "triage"},
                    actor="crm-bot", field="disposition", new_value="hold",
                    rationale="keep a human on erasure calls",
                    old_value="auto", log_root=lr)
    return {"f": f, "lr": lr}


def _open(env, **kw):
    q = DecisionQueue(env["f"], log_root=env["lr"])
    out = q.open(dict(SURFACE), raised_by="crm-bot",
                 competence="data-protection", **kw)
    assert out["ok"], out
    return out["decision_id"]


def test_full_assembly(env):                                     # D1
    did = _open(env, escalate_to="board", escalate_after_s=3600,
                write_reconfirm=True, priority="high")
    out = decision_dossier(env["f"], decision_id=did, log_root=env["lr"])
    assert out["ok"] is True
    d = out["dossier"]
    assert d["decision_id"] == did

    item = d["item"]
    assert item["state"] == "open" and item["priority"] == "high"
    assert item["assignment_basis"] == "competence data-protection"
    assert item["claimed_by"] is None and item["overdue"] is False

    basis = d["basis"]
    assert basis["query"] == SURFACE["query"]
    assert basis["esc_reason"] == SURFACE["esc_reason"]
    bands = {o["id"]: o["grounding_band"] for o in basis["options"]}
    assert bands == {"erase": "firm", "split": "thin"}
    counts = {o["id"]: o["supporting_count"] for o in basis["options"]}
    assert counts == {"erase": 1, "split": 0}

    runs = d["runs"]
    assert "attributed" in runs["join"] and runs["total"] == 1
    assert runs["rows"][0]["use_case_id"] == "uc-crm"
    assert runs["rows"][0]["final"] in ("complete", "awaiting-human")
    assert runs["rows"][0]["steps"][0]["issue_id"] == "i1"

    standing = d["standing"]
    assert standing["party"]["registered"] is True
    assert standing["party"]["party_kind"] == "agent"
    assert standing["party"]["grade"] == "L2"
    assert standing["overrides"]["count"] == 1
    assert standing["overrides"]["recent"][0]["stage"] == "triage"
    assert standing["meter"]["events"] > 0

    rec = d["recourse"]
    assert rec["escalate_to"] == "board" and rec["write_reconfirm"] is True
    assert "attributed" in rec["use_cases"]["join"]
    row = rec["use_cases"]["rows"][0]
    assert row["use_case_id"] == "uc-crm"
    assert row["obligations"] == [{"duty": "notify the data subject"}]
    assert row["redress"][0]["route"] == "DPO review"
    assert any(a.get("reserved_to") == "moderator"
               for a in row["reserved_acts"])


def test_no_raw_grounding_leaves(env):                           # D2
    did = _open(env)
    dossier = decision_dossier(env["f"], decision_id=did,
                               log_root=env["lr"])

    def mappings(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from mappings(child)
        elif isinstance(value, list):
            for child in value:
                yield from mappings(child)

    rows = list(mappings(dossier))
    assert all("grounding" not in row for row in rows)
    assert any("grounding_band" in row for row in rows)


def test_unknown_and_closed_ids_refuse(env):                     # D3
    out = decision_dossier(env["f"], decision_id="dec-nope",
                           log_root=env["lr"])
    assert out["ok"] is False and "dec-nope" in out["error"]
    did = _open(env)
    q = DecisionQueue(env["f"], log_root=env["lr"])
    q.close(did, "dpo")
    closed = decision_dossier(env["f"], decision_id=did, log_root=env["lr"])
    assert closed["ok"] is False


def test_read_appends_nothing_to_the_chain(env):                 # D4
    did = _open(env)
    log = MutationLog(env["f"], log_root=env["lr"])
    before = sum(1 for _ in log.replay())
    assert decision_dossier(env["f"], decision_id=did,
                            log_root=env["lr"])["ok"]
    after = sum(1 for _ in MutationLog(env["f"],
                                       log_root=env["lr"]).replay())
    assert after == before


def test_degraded_records_stay_visible(tmp_path, monkeypatch):   # D5
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    f, lr = str(tmp_path / "bare"), str(tmp_path / "log")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", lr)
    did = DecisionQueue(f, log_root=lr).open(
        dict(SURFACE), raised_by="ghost-bot")["decision_id"]
    out = decision_dossier(f, decision_id=did, log_root=lr)
    assert out["ok"] is True
    d = out["dossier"]
    assert d["runs"]["rows"] == [] and d["runs"]["total"] == 0
    assert "attributed" in d["runs"]["join"]
    assert d["standing"]["party"] == {"party_id": "ghost-bot",
                                      "registered": False}
    assert d["recourse"]["use_cases"]["rows"] == []


def test_panel_seats_stay_sealed(env):                           # D6
    did = _open(env, panel={"seats": 2, "rule": "m_concordant", "m": 2})
    q = DecisionQueue(env["f"], log_root=env["lr"])
    assert q.claim(did, "dpo")["ok"]
    rec = q.record_seat(did, "dpo", "erase",
                        "seat rationale that must stay sealed")
    assert rec["ok"] and not rec.get("resolved")
    out = decision_dossier(env["f"], decision_id=did, log_root=env["lr"])
    assert out["ok"] is True
    panel = out["dossier"]["item"]["panel"]
    assert panel["seats"] == 2 and panel["recorded"] == 1
    assert panel["recorded_by"] == ["dpo"]
    assert len(panel["commitments"]) == 1
    blob = json.dumps(out)
    assert "seat rationale that must stay sealed" not in blob
    assert "chosen_option_id" not in blob and "seat_records" not in blob


def test_facade_op_decision_dossier(env, monkeypatch):           # D7
    """workspace_dispatch: decision_dossier is reachable as an op and keeps
    the fail-closed contract for unknown ids."""
    M = pytest.importorskip("rvnd.mcp_server")
    did = _open(env)
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", env["lr"])
    out = M.workspace_dispatch("decision_dossier",
                               {"folder_context": env["f"],
                                "decision_id": did})
    assert out["ok"] is True and out["dossier"]["decision_id"] == did
    assert M.workspace_dispatch("decision_dossier",
                                {"folder_context": env["f"],
                                 "decision_id": "dec-nope"})["ok"] is False
    help_ops = {o["op"] for o in M.workspace_dispatch("help")["ops"]}
    assert "decision_dossier" in help_ops
