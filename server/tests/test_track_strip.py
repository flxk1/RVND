# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The track channel-strip projection — one track's governance assembled
read-only from existing projections.

Covers: addressing (exactly one of party/connector, unknown ids fail closed);
the party strip (identity, ladder + law lock, channel join with dangling
bindings visible, use cases, the routed m-of-n approvals meter, the per-actor
verdict tally); the connector strip (floor, drivers, reservations, the egress
cable on egress tracks only, the per-pair verdict tally); and that no secret
material ever appears in a strip.
"""
from __future__ import annotations

import json

import pytest

from workspaces import approvals, connectors, parties, use_case
from workspaces.mutation_log import LogEvent, MutationLog
from workspaces.track_strip import track_strip

NOW = 1_700_000_000.0


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace with two lanes, two channels, a governed use case carrying a
    policy and an ingest-authored law reservation, and one pending two-hand
    approval."""
    monkeypatch.setenv("STRIP_TOK", "s3cr3t-strip")
    f = str(tmp_path / "ws")
    lr = str(tmp_path / "log")

    parties.register_party(f, "scout", "agent", owner="ops", purpose="triage",
                           grade="L2", competences=["legal"],
                           channels=["out-llm", "ghost-chan"], log_root=lr)
    parties.register_party(f, "ann", "human", role="counsel",
                           competences=["legal"], log_root=lr)
    parties.register_party(f, "intern", "human", competences=[], log_root=lr)

    connectors.register_connector(f, connector_id="out-llm", role="egress",
                                  channel="api", floor="hold",
                                  credential_ref="env:STRIP_TOK",
                                  use_cases=["uc-1"], log_root=lr)
    connectors.register_connector(f, connector_id="feed", role="ingress",
                                  channel="email", use_cases=["uc-1"],
                                  log_root=lr)

    use_case.register_use_case(
        f, use_case_id="uc-1", name="Triage inbound", fingerprint={},
        risk="medium", allowed_agents=["scout"], actor="ann",
        policy_reservations={"generated_content": {
            "reserved_to": "moderator", "act_type": "approve"}},
        log_root=lr)
    # A law-basis reserved act arrives via ingest, not the authoring API (which
    # re-derives legal acts and carries only policy ones) — author the chain
    # record the way an ingest path does: a re-versioned UseCaseRegistered
    # event whose reserved_acts include the law act.
    rec = use_case.get_use_case(f, "uc-1", log_root=lr)
    law_act = {"trigger": "automated_decision", "basis_kind": "law",
               "reserved_to": "data-protection", "act_type": "review",
               "source": "GDPR Art. 22"}
    MutationLog(f, log_root=lr).append(LogEvent(
        event="system", folder_path=f, pair_id="use_case:uc-1", actor="ingest",
        extra={**rec, "kind": "UseCaseRegistered",
               "reserved_acts": list(rec["reserved_acts"]) + [law_act]}))

    approvals.request_approval(f, "req-1", form="four_eyes", competence="legal",
                               quorum=2, competences=["legal"],
                               requester="scout", now=NOW, log_root=lr)

    log = MutationLog(f, log_root=lr)
    log.append(LogEvent(event="system", folder_path=f, pair_id="run:1",
                        actor="scout", extra={"verdict": "permit"}))
    log.append(LogEvent(event="system", folder_path=f, pair_id="run:1",
                        actor="scout", extra={"gate_verdict": "hold"}))
    log.append(LogEvent(event="system", folder_path=f,
                        pair_id="connector:out-llm", actor="user",
                        extra={"verdict": "permit"}))
    return f, lr


# ---- addressing ---------------------------------------------------------------

def test_exactly_one_address_required(ws):
    f, lr = ws
    assert track_strip(f, log_root=lr)["ok"] is False
    both = track_strip(f, party_id="scout", connector_id="out-llm", log_root=lr)
    assert both["ok"] is False


def test_unknown_ids_fail_closed(ws):
    f, lr = ws
    assert track_strip(f, party_id="ghost", log_root=lr)["ok"] is False
    assert track_strip(f, connector_id="ghost", log_root=lr)["ok"] is False


# ---- the party strip ------------------------------------------------------------

def test_party_strip_identity_and_ladder(ws):
    f, lr = ws
    out = track_strip(f, party_id="scout", now=NOW, log_root=lr)
    assert out["ok"] and out["kind"] == "party"
    s = out["strip"]
    assert s["party_kind"] == "agent" and s["status"] == "active"
    assert s["ladder"]["grade"] == "L2"
    # the law reservation locks the ladder (tighten-only)
    assert s["ladder"]["locked"] is True
    assert s["ladder"]["locks"][0]["trigger"] == "automated_decision"
    assert s["ladder"]["locks"][0]["source"].startswith("GDPR")
    # the policy reservation is listed but does not lock
    triggers = {a["trigger"]: a["basis_kind"] for a in s["reservations"]}
    assert triggers["generated_content"] == "policy"


def test_party_strip_channel_join_shows_dangling(ws):
    f, lr = ws
    s = track_strip(f, party_id="scout", now=NOW, log_root=lr)["strip"]
    by_id = {c["connector_id"]: c for c in s["channels"]}
    assert by_id["out-llm"]["registered"] is True
    assert by_id["out-llm"]["role"] == "egress"
    assert by_id["out-llm"]["floor"] == "hold"
    # a soft binding to an unregistered connector is visible, not hidden
    assert by_id["ghost-chan"] == {"connector_id": "ghost-chan",
                                   "registered": False}


def test_party_strip_use_cases(ws):
    f, lr = ws
    s = track_strip(f, party_id="scout", now=NOW, log_root=lr)["strip"]
    assert [u["use_case_id"] for u in s["use_cases"]] == ["uc-1"]
    assert s["use_cases"][0]["risk"] == "medium"


def test_party_strip_approvals_meter_routed_by_competence(ws):
    f, lr = ws
    s = track_strip(f, party_id="scout", now=NOW, log_root=lr)["strip"]
    assert s["approvals"]["count"] == 1
    item = s["approvals"]["pending"][0]
    assert (item["signed"], item["required"]) == (0, 2)
    # a lane holding none of the routed competences sees an empty inbox
    intern = track_strip(f, party_id="intern", now=NOW, log_root=lr)["strip"]
    assert intern["approvals"]["count"] == 0


def test_party_strip_approvals_meter_live_progress(ws):
    f, lr = ws
    approvals.decide_approval(f, "req-1", "approve", actor="ann", now=NOW + 60,
                              log_root=lr)
    s = track_strip(f, party_id="scout", now=NOW + 120, log_root=lr)["strip"]
    item = s["approvals"]["pending"][0]
    assert (item["signed"], item["required"]) == (1, 2)   # "1 of 2 signed"


def test_party_strip_meter_counts_own_verdicts_only(ws):
    f, lr = ws
    s = track_strip(f, party_id="scout", now=NOW, log_root=lr)["strip"]
    assert s["meter"]["verdicts"] == {"permit": 1, "hold": 1}
    ann = track_strip(f, party_id="ann", now=NOW, log_root=lr)["strip"]
    assert ann["meter"]["verdicts"] == {}


# ---- the connector strip --------------------------------------------------------

def test_connector_strip_egress_cable(ws):
    f, lr = ws
    out = track_strip(f, connector_id="out-llm", log_root=lr)
    assert out["ok"] and out["kind"] == "connector"
    s = out["strip"]
    assert s["floor"] == "hold" and s["role"] == "egress"
    cred = s["egress"]["credential"]
    assert cred["status"] == "armed" and cred["credential_ref"] == "env:STRIP_TOK"
    assert [p["party_id"] for p in s["parties"]] == ["scout"]
    assert s["meter"]["verdicts"] == {"permit": 1}
    trig = {a["trigger"] for a in s["reservations"]}
    assert trig == {"automated_decision", "generated_content"}


def test_connector_strip_non_egress_has_no_cable(ws):
    f, lr = ws
    s = track_strip(f, connector_id="feed", log_root=lr)["strip"]
    assert "egress" not in s
    assert s["role"] == "ingress" and s["channel"] == "email"


def test_strip_never_carries_the_secret(ws):
    f, lr = ws
    for kw in ({"party_id": "scout"}, {"connector_id": "out-llm"}):
        blob = json.dumps(track_strip(f, now=NOW, log_root=lr, **kw))
        assert "s3cr3t-strip" not in blob


# ---- the facade op --------------------------------------------------------------

def test_facade_op_track_strip(ws, monkeypatch):
    """workspace_workflow: track_strip is reachable as an op (the inspector's
    data path) and keeps the exactly-one-address fail-closed contract."""
    import workspaces.mcp_server as M
    f, lr = ws
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", lr)
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    out = M.workspace_workflow("track_strip", {"folder_context": f,
                                               "party_id": "scout"})
    assert out["ok"] and out["strip"]["ladder"]["grade"] == "L2"
    assert M.workspace_workflow("track_strip",
                                {"folder_context": f})["ok"] is False
