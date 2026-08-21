# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The coverage lens: the governance patch projected as a Kind x Risk grid.

Claims under test (written before the logic):
  C1  the grid's rows are the kinds present (issue_type, or "unclassified"),
      cols are the risk bands low..critical; cells match rows x cols
  C2  a cell is the engine's strictest-wins verdict for its band, with the
      source use cases attached and the right letter; a reserved high-risk act
      is "reserved", not a finding
  C3  finding parity: the use cases in finding cells are exactly those the
      auto_high_risk query reports — the lens is that query as a shape (the
      engine caps high-risk runs to a person, so a well-run patch has none)
  C4  the finding rule and gaps_only, driven over a controlled graph where an
      authored-permissive high-risk cell is auto: it is flagged, gaps_only
      keeps its row and drops the clean one
  C5  an unknown preset is refused with the valid list
  C6  the facade routes coverage_matrix and preset='list'; the tool count is
      unchanged (a new op on an existing facade, not a new tool)
  C7  the tags facet narrows the grid to use cases carrying the tag
  C8  an empty folder projects as empty, no rows

Run: python -m pytest server/tests/test_matrix_coverage.py -q
"""
from __future__ import annotations

import os

import pytest

from rvnd import parties as pt
from rvnd.governance_graph import governance_query
from rvnd.matrix_coverage import coverage_matrix
from rvnd.operations import operate
from rvnd.use_case import register_use_case

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"; ws.mkdir()
    lr = str(tmp_path / "logs")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", lr)
    pt.register_party(str(ws), "bot7", "agent", name="bot7", actor="alex", log_root=lr)

    # high-risk send, wired + run clean -> auto: the finding the lens must show
    register_use_case(str(ws), use_case_id="uc-send-hi", name="uc-send-hi",
                      fingerprint={"issue_type": "external_send"}, risk="high",
                      allowed_agents=["bot7"], actor="alex",
                      tags=["marketing"], log_root=lr)
    operate(str(ws), use_case_id="uc-send-hi", agent_id="bot7",
            issues=[{"issue_id": "i1", "issue_type": "external_send",
                     "completeness": "high"}], now_epoch=1000, log_root=lr)
    # high-risk decisioning, reserved by authored policy -> reserved, NOT a finding
    register_use_case(str(ws), use_case_id="uc-decide-hi", name="uc-decide-hi",
                      fingerprint={"issue_type": "decisioning"}, risk="high",
                      allowed_agents=["bot7"], actor="alex",
                      policy_reservations={"uc-decide-hi": {
                          "reserved_to": "data-protection", "act_type": "review",
                          "source": "company policy"}}, log_root=lr)
    # low-risk send, run clean
    register_use_case(str(ws), use_case_id="uc-send-lo", name="uc-send-lo",
                      fingerprint={"issue_type": "external_send"}, risk="low",
                      allowed_agents=["bot7"], actor="alex", log_root=lr)
    operate(str(ws), use_case_id="uc-send-lo", agent_id="bot7",
            issues=[{"issue_id": "i2", "issue_type": "external_send",
                     "completeness": "high"}], now_epoch=1001, log_root=lr)
    # unclassified (no issue_type), medium, no agent
    register_use_case(str(ws), use_case_id="uc-orphan", name="uc-orphan",
                      fingerprint={}, risk="medium", allowed_agents=[],
                      actor="alex", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _m(env, **kw):
    return coverage_matrix(env["ws"], log_root=env["lr"], **kw)


def _cell(matrix, kind, risk):
    ri, ci = matrix["rows"].index(kind), matrix["cols"].index(risk)
    return matrix["cells"][ri][ci]


def test_grid_shape(env):                                        # C1
    m = _m(env)
    assert set(m["rows"]) >= {"external_send", "decisioning", "unclassified"}
    assert m["cols"][:4] == ["low", "medium", "high", "critical"]
    assert len(m["cells"]) == len(m["rows"])
    assert all(len(row) == len(m["cols"]) for row in m["cells"])
    assert m["empty"] is False and m["editable"] is False


def test_cell_is_engine_verdict(env):                            # C2
    reserved = _cell(_m(env), "decisioning", "high")
    assert reserved["verdict"] == "reserved" and reserved["letter"] == "r"
    assert reserved["finding"] is False and reserved["count"] == 1
    assert reserved["refs"][0]["id"] == "uc:uc-decide-hi"


def test_task_oversight_lens(env):                               # oversight lens
    m = _m(env, preset="task_oversight")
    assert m["col_axis"] == "oversight"
    assert m["cols"] == ["severed", "human decision", "human-in-the-loop",
                         "on-the-loop", "autonomous"]

    def cell(task, mode):
        ri, ci = m["rows"].index(task), m["cols"].index(mode)
        return m["cells"][ri][ci]

    # a reserved act -> human decision, carrying its overseer role
    hd = cell("uc-decide-hi", "human decision")
    assert hd["count"] == 1 and "data-protection" in hd["why"]
    # a high-risk act (capped L2), no reservation -> human-in-the-loop
    assert cell("uc-send-hi", "human-in-the-loop")["count"] == 1
    # a low-risk act -> autonomous, and THAT is the finding (runs with no human)
    auto = cell("uc-send-lo", "autonomous")
    assert auto["count"] == 1 and auto["finding"] is True
    assert m["findings"] >= 1
    # one-hot: a task sits in exactly its mode column, nowhere else
    assert cell("uc-send-lo", "human-in-the-loop")["count"] == 0


def test_finding_parity_with_query(env):                         # C3
    m = _m(env)
    finding_labels = {ref["id"].replace("uc:", "")
                      for row in m["cells"] for c in row
                      if c["finding"] for ref in c["refs"]}
    q = governance_query(env["ws"], "auto_high_risk", log_root=env["lr"])
    query_labels = {r["use_case"] for r in q["rows"]}
    assert finding_labels == query_labels    # the lens is the query, as a shape


# A controlled patch where policy has been authored permissive: a high-risk use
# case whose egress verdict is auto (the state the run-time engine caps away, so
# it can only arrive by an authored grant). This drives the finding + gaps_only
# path deterministically without fighting the risk cap.
_PERMISSIVE_G = {
    "nodes": [
        {"id": "uc:send", "kind": "use_case", "label": "send", "risk": "high",
         "issue_type": "external_send", "reserved": []},
        {"id": "uc:decide", "kind": "use_case", "label": "decide", "risk": "high",
         "issue_type": "decisioning", "reserved": ["review"]},
    ],
    "edges": [
        {"from": "uc:send", "to": "master", "kind": "egress", "verdict": "auto"},
        {"from": "uc:decide", "to": "master", "kind": "egress", "verdict": "unfired"},
    ],
}


def test_finding_and_gaps_only(monkeypatch):                     # C4
    import rvnd.matrix_coverage as MC
    monkeypatch.setattr(MC, "governance_graph", lambda *a, **k: _PERMISSIVE_G)
    m = MC.coverage_matrix("/x")
    send = _cell(m, "external_send", "high")
    assert send["verdict"] == "auto" and send["letter"] == "a"
    assert send["finding"] is True and "person is not in the loop" in send["why"]
    decide = _cell(m, "decisioning", "high")
    assert decide["verdict"] == "reserved" and decide["finding"] is False
    assert m["findings"] == 1

    gapped = MC.coverage_matrix("/x", gaps_only=True)
    assert gapped["rows"] == ["external_send"]      # keeps the finding row only


def test_unknown_preset_refused(env):
    out = coverage_matrix(env["ws"], "nope", log_root=env["lr"])       # C5
    assert "error" in out and "kind_risk" in out["valid"]


def test_facade_routes(env):                                     # C6
    from rvnd import mcp_server as M
    assert len(M._DECLARED_TOOLS) == 24
    r = M.workspace_workflow(op="coverage_matrix",
                             params={"folder_context": env["ws"]})
    assert r["preset"] == "kind_risk" and "cells" in r
    listed = M.workspace_workflow(op="coverage_matrix",
                                  params={"folder_context": env["ws"],
                                          "preset": "list"})
    assert any(p["preset"] == "kind_risk" for p in listed["presets"])


def test_tags_facet_narrows(env):                                # C7
    m = _m(env, tags=["marketing"])
    ucs = {ref["id"] for row in m["cells"] for c in row for ref in c["refs"]}
    assert ucs == {"uc:uc-send-hi"}          # only the marketing-tagged one


def test_empty_folder(tmp_path, monkeypatch):                    # C8
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "empty"; ws.mkdir()
    m = coverage_matrix(str(ws), log_root=str(tmp_path / "logs"))
    assert m["empty"] is True and m["rows"] == []


# ── Task × Role preset: reserved acts vs the competent roster ────────────────
# Claims:
#   T1  rows are the reserved tasks, cols the competences their reservations
#       name; a reservation with a competent active human reads "covered"
#   T2  a reservation to a competence no one holds is a fail-closed finding
#       ("gap"); a task not reserved to a column reads "none"
#   T3  gaps_only keeps only the uncovered rows; the preset is listed
@pytest.fixture()
def roles(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org2"; ws.mkdir()
    lr = str(tmp_path / "logs2")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", lr)
    pt.register_party(str(ws), "dana", "human", name="dana",
                      competences=["data-protection"], actor="alex", log_root=lr)
    register_use_case(str(ws), use_case_id="uc-erase", name="uc-erase",
                      fingerprint={"issue_type": "erasure"}, risk="high",
                      allowed_agents=[], actor="alex",
                      policy_reservations={"uc-erase": {
                          "reserved_to": "data-protection", "act_type": "review",
                          "source": "policy"}}, log_root=lr)
    register_use_case(str(ws), use_case_id="uc-pay", name="uc-pay",
                      fingerprint={"issue_type": "payment"}, risk="high",
                      allowed_agents=[], actor="alex",
                      policy_reservations={"uc-pay": {
                          "reserved_to": "finance", "act_type": "approve",
                          "source": "policy"}}, log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _tr(roles, **kw):
    return coverage_matrix(roles["ws"], "task_role", log_root=roles["lr"], **kw)


def _rcell(m, task, role):
    return m["cells"][m["rows"].index(task)][m["cols"].index(role)]


def test_task_role_shape_and_cover(roles):                       # T1
    m = _tr(roles)
    assert set(m["cols"]) == {"data-protection", "finance"}
    assert set(m["rows"]) == {"uc-erase", "uc-pay"}
    covered = _rcell(m, "uc-erase", "data-protection")
    assert covered["verdict"] == "covered" and covered["finding"] is False
    assert covered["count"] == 1


def test_task_role_gap_is_a_finding(roles):                      # T2
    m = _tr(roles)
    gap = _rcell(m, "uc-pay", "finance")
    assert gap["verdict"] == "gap" and gap["letter"] == "!"
    assert gap["finding"] is True and gap["count"] == 0
    assert "no active party" in gap["why"]
    assert _rcell(m, "uc-erase", "finance")["verdict"] == "none"
    assert m["findings"] == 1


def test_task_role_gaps_only_and_listed(roles):                  # T3
    m = _tr(roles, gaps_only=True)
    assert m["rows"] == ["uc-pay"]
    from rvnd.matrix_coverage import presets
    assert any(p["preset"] == "task_role" for p in presets())


# ── Task × Agent preset: the authority grants as an editable (tighten-only) grid
# Claims:
#   A1  rows are the use cases, cols the agents; a granted cell carries the
#       task's verdict plus the ids the revoke op needs and is marked editable;
#       an ungranted cell reads none
#   A2  authority_revoke drops exactly one agent's authority, carries the
#       reserved acts forward, and refuses an agent with nothing to revoke
#   A3  the facade routes authority_revoke; the revoked cell reads none after
def test_task_agent_grid_and_revoke(roles):                      # A1 A2 A3
    from rvnd import mcp_server as M
    from rvnd.use_case import get_use_case, revoke_agent
    pt.register_party(roles["ws"], "bot9", "agent", name="bot9",
                      actor="alex", log_root=roles["lr"])
    register_use_case(roles["ws"], use_case_id="uc-send", name="uc-send",
                      fingerprint={"issue_type": "external_send"}, risk="low",
                      allowed_agents=["bot9"], actor="alex",
                      policy_reservations={"uc-send": {
                          "reserved_to": "data-protection", "act_type": "review",
                          "source": "policy"}}, log_root=roles["lr"])
    m = coverage_matrix(roles["ws"], "task_agent", log_root=roles["lr"])
    assert m["editable"] is True and "bot9" in m["cols"]
    cell = m["cells"][m["rows"].index("uc-send")][m["cols"].index("bot9")]
    assert cell["editable"] is True and cell["verdict"] != "none"
    assert cell["use_case_id"] == "uc-send" and cell["agent_id"] == "bot9"
    other = m["cells"][m["rows"].index("uc-erase")][m["cols"].index("bot9")]
    assert other["verdict"] == "none"

    out = M.workspace_workflow("authority_revoke", {
        "folder_context": roles["ws"], "use_case_id": "uc-send",
        "agent_id": "bot9", "actor": "alex"})
    assert out["ok"] is True and out["allowed_agents"] == []
    rec = get_use_case(roles["ws"], "uc-send", log_root=roles["lr"])
    assert rec["allowed_agents"] == []
    assert rec["reserved_acts"], "the reservation must ride the re-version"
    m2 = coverage_matrix(roles["ws"], "task_agent", log_root=roles["lr"])
    cell2 = m2["cells"][m2["rows"].index("uc-send")][m2["cols"].index("bot9")]
    assert cell2["verdict"] == "none"
    again = revoke_agent(roles["ws"], "uc-send", "bot9", actor="alex",
                         log_root=roles["lr"])
    assert again["ok"] is False and "nothing to revoke" in again["error"]
