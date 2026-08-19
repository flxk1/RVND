# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""governance_graph patch projection.

The tests cover node/edge projection, egress verdicts, autonomy ceilings,
reservation provenance, policy-vs-law attribution, unfired cords, and facade
reachability.
"""
from __future__ import annotations

import os
import pytest

from rvnd import parties as pt
from rvnd.governance_graph import governance_graph
from rvnd.use_case import register_use_case
from rvnd.operations import operate

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"; ws.mkdir()
    lr = str(tmp_path / "logs")
    pt.register_party(str(ws), "bot-7", "agent", name="Drafter", actor="alex", log_root=lr)
    pt.register_party(str(ws), "alice", "human", name="Alice", actor="alex", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _fp(itype):
    return {"issue_type": itype, "profile": "legal-de"}


def test_c1_egress_boundaries_grouped_by_destination_class(env):
    """C1: egress connectors are surfaced as N boundaries grouped by
    destination-class, each carrying its floor + group-bus; the single master
    stays (additive — nothing that reads it breaks)."""
    from rvnd.connectors import register_connector
    ws, lr = env["ws"], env["lr"]
    register_connector(ws, connector_id="llm-out", role="egress", channel="api",
                       use_cases=["u"], floor="hold", group="company",
                       destination_class="llm", actor="alex", log_root=lr)
    register_connector(ws, connector_id="mail-out", role="egress", channel="email",
                       use_cases=["u"], floor="permit", group="company",
                       destination_class="message", actor="alex", log_root=lr)
    g = governance_graph(ws, log_root=lr)
    assert {b["destination_class"] for b in g["egress_boundaries"]} == {"llm", "message"}
    assert g["summary"]["egress_boundaries"] == 2
    bnodes = {n["id"]: n for n in g["nodes"] if n.get("is_boundary")}
    assert bnodes["conn:llm-out"]["floor"] == "hold"
    assert bnodes["conn:llm-out"]["group"] == "company"
    assert bnodes["conn:mail-out"]["destination_class"] == "message"
    assert any(n["id"] == "master" for n in g["nodes"])  # additive; master stays


def test_oversight_mode_projected_per_use_case(env):
    """The projection folds the server-decided reservation + composed ceiling +
    prohibited into ONE renderable oversight mode per use-case — pure synthesis,
    no model call (the projection contract forbids one). The console badge / Matrix
    read this field; they compose nothing themselves."""
    ws, lr = env["ws"], env["lr"]
    # clean low-risk act -> autonomous (L4, no human bound)
    register_use_case(ws, use_case_id="uc-auto", name="Auto", fingerprint=_fp("liability_cap"),
                      risk="low", allowed_agents=["bot-7"], actor="alex", log_root=lr)
    # a reserved act -> a named role must act (human decision), REVIEW dial, capped L2
    register_use_case(ws, use_case_id="uc-rev", name="Review", fingerprint=_fp("automated_decision"),
                      risk="high", allowed_agents=["bot-7"], actor="alex", log_root=lr,
                      policy_reservations={"uc-rev": {
                          "reserved_to": "data-protection", "act_type": "review",
                          "source": "policy clause 7.1"}})
    # a prohibited act -> severed (L0)
    register_use_case(ws, use_case_id="uc-no", name="Severed", fingerprint=_fp("liability_cap"),
                      risk="low", allowed_agents=["bot-7"], actor="alex", prohibited=True, log_root=lr)
    ucn = {n["id"]: n for n in governance_graph(ws, log_root=lr)["nodes"] if n["kind"] == "use_case"}
    assert ucn["uc:uc-auto"]["oversight"]["mode"] == "autonomous"
    assert ucn["uc:uc-auto"]["oversight"]["overseers"] == []
    rev = ucn["uc:uc-rev"]["oversight"]
    assert rev["mode"] == "human decision"
    assert rev["level"] == "REVIEW"
    assert rev["overseers"] == ["data-protection"]
    assert rev["grade_ceiling"] == 2
    assert ucn["uc:uc-no"]["oversight"]["mode"] == "severed"
    assert ucn["uc:uc-no"]["oversight"]["grade_ceiling"] == 0


def test_nodes_edges_verdicts(env):
    ws, lr = env["ws"], env["lr"]
    register_use_case(ws, use_case_id="uc-draft", name="Draft", fingerprint=_fp("liability_cap"),
                      risk="low", allowed_agents=["bot-7"], actor="alex",
                      prior_approvals=25, override_window_seconds=120, log_root=lr)
    # Reservations are authored or ingested, not inferred from a legal enum.
    # uc-decide is reserved by an AUTHORED policy reservation (was the
    # automated_decision enum). The 'reserved, never auto' verdict is identical.
    register_use_case(ws, use_case_id="uc-decide", name="Decide", fingerprint=_fp("automated_decision"),
                      risk="high", allowed_agents=["bot-7"], actor="alex",
                      override_window_seconds=120,
                      policy_reservations={"uc-decide": {
                          "reserved_to": "data-protection", "act_type": "review",
                          "source": "company policy — automated decision review"}},
                      log_root=lr)
    operate(ws, use_case_id="uc-draft", agent_id="bot-7",
            issues=[{"issue_id": "i1", "issue_type": "liability_cap", "completeness": "high"}],
            now_epoch=1000, log_root=lr)
    operate(ws, use_case_id="uc-decide", agent_id="bot-7",
            issues=[{"issue_id": "i2", "issue_type": "automated_decision", "completeness": "high"}],
            now_epoch=1000, log_root=lr)

    g = governance_graph(ws, log_root=lr)
    kinds = {}
    for n in g["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    assert kinds["agent"] == 1 and kinds["human"] == 1
    assert kinds["use_case"] == 2 and kinds["master"] == 1

    auth = [e for e in g["edges"] if e["kind"] == "authority"]
    assert len(auth) == 2
    assert all(e["from"] == "party:bot-7" for e in auth)

    # Every use_case node carries the SERVER-composed autonomy ceiling so the
    # UI renders it instead of recomputing a risk→cap map client-side.
    ucn = {n["id"]: n for n in g["nodes"] if n["kind"] == "use_case"}
    assert ucn["uc:uc-draft"]["grade_ceiling"] == 4             # low risk -> L4
    assert ucn["uc:uc-decide"]["grade_ceiling"] == 2            # high risk -> L2

    egress = {e["from"]: e["verdict"] for e in g["edges"] if e["kind"] == "egress"}
    assert egress["uc:uc-draft"] == "auto"
    assert egress["uc:uc-decide"] == "reserved"
    assert g["verdicts"]["uc:uc-decide"]["verdict"] == "reserved"
    assert g["summary"]["reserved_use_cases"] == 1


def test_reservations_carry_attributed_provenance(env):
    """The projection must carry WHERE a reservation comes from, so the client can
    attribute it (the user's policy / an ingested reference) instead of printing
    "by law" as Rvnd's own finding. Rvnd does not assert statutes — it carries a
    curated source string verbatim. The flat `reserved` stays for count/boolean
    consumers.

    Reservations are authored or ingested, not inferred from a legal enum.
    The reservation is AUTHORED here (basis_kind 'policy'); the valuable property —
    every reservation NAMES its basis + source so the UI never defaults to "by law"
    — is asserted exactly as before, now flowing through from the authored act."""
    ws, lr = env["ws"], env["lr"]
    register_use_case(ws, use_case_id="uc-decide", name="Decide",
                      fingerprint=_fp("automated_decision"), risk="high",
                      allowed_agents=["bot-7"], actor="alex",
                      policy_reservations={"uc-decide": {
                          "reserved_to": "data-protection", "act_type": "review",
                          "source": "your policy clause 7.1 (GDPR Art. 22 review)"}},
                      log_root=lr)
    g = governance_graph(ws, log_root=lr)
    node = next(n for n in g["nodes"] if n["id"] == "uc:uc-decide")

    # flat form preserved (backward-compatible)
    assert node["reserved"] == ["review"]
    # attributed form is additive and complete
    assert node["reservations"], "a reserved use_case must project its provenance"
    res = node["reservations"][0]
    assert res["basis_kind"] == "policy"        # authored, not invented; basis explicit
    assert res["act_type"] == "review"
    assert res["reserved_to"] == "data-protection"  # who must act, from the authored act
    assert "GDPR Art. 22" in res["source"]      # the curated citation flows through verbatim
    # every reservation must name its basis — the UI can never default to "by law"
    assert all(r.get("basis_kind") for r in node["reservations"])


def test_policy_reservation_projects_as_policy_not_law(env):
    """A company-chosen reservation must project basis_kind 'policy' (NOT law), so
    no surface can attribute it to law. The register row carries the distinct
    basis(es) — here ['policy'] — never a flat reserved_by_law boolean."""
    from rvnd.governance_graph import governance_register
    ws, lr = env["ws"], env["lr"]
    register_use_case(
        ws, use_case_id="uc-mod", name="Moderate", fingerprint=_fp("liability_cap"),
        risk="medium", allowed_agents=["bot-7"], actor="alex", log_root=lr,
        policy_reservations={"generated_content": {
            "reserved_to": "moderator", "act_type": "review",
            "source": "your policy clause 4.2"}})
    g = governance_graph(ws, log_root=lr)
    node = next(n for n in g["nodes"] if n["id"] == "uc:uc-mod")
    pol = [r for r in node["reservations"] if r["basis_kind"] == "policy"]
    assert pol, "a policy reservation must project basis_kind 'policy'"
    assert pol[0]["source"] == "your policy clause 4.2"   # the user's own clause, attributed
    assert all(r["basis_kind"] != "law" for r in node["reservations"])  # never upgraded to law

    reg = {r["id"]: r for r in governance_register(ws, log_root=lr)["rows"]}
    row = reg["uc:uc-mod"]
    assert row["reserved"] is True and row["reserved_bases"] == ["policy"]


def test_unfired_egress(env):
    ws, lr = env["ws"], env["lr"]
    register_use_case(ws, use_case_id="uc-x", name="X", fingerprint=_fp("liability_cap"),
                      risk="low", allowed_agents=["bot-7"], actor="alex", log_root=lr)
    g = governance_graph(ws, log_root=lr)
    egress = [e for e in g["edges"] if e["kind"] == "egress"]
    assert len(egress) == 1 and egress[0]["verdict"] == "unfired"


def test_facade_ops_reachable_and_surface_intact(env):
    from rvnd import mcp_server as M
    assert len(M._DECLARED_TOOLS) == 24
    ops = {o["op"] for o in M.workspace_workflow(op="help")["ops"]}
    assert {"use_case_register", "use_case_list", "use_case_get",
            "governance_graph"} <= ops
    g = M.workspace_workflow(op="governance_graph", params={"folder_context": env["ws"]})
    assert "nodes" in g and "edges" in g and "verdicts" in g
