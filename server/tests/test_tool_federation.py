# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governance bus (B′) first slice — federate a third-party tool's verdict:
neutral mapping, signed/attributed record, strictest-wins join with disagreement
visible, and a kill switch. Rvnd records + joins; it never calls the tool.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rvnd import connectors as C
from rvnd import tool_federation as TF
from rvnd.verdict import Verdict, from_risk_tier
from rvnd.mutation_log import MutationLog


@pytest.fixture
def ws(tmp_path: Path):
    lr = str(tmp_path / "logs")
    f = tmp_path / "w"; f.mkdir()
    # a generic compute tool, registered as an oversight connector linked to a use case
    C.register_connector(str(f), connector_id="checker-A", role="oversight",
                         channel="api", use_cases=["score"], log_root=lr)
    return str(f), lr


def test_neutral_risk_tier_mapping():
    assert from_risk_tier("pass") == Verdict.PERMIT
    assert from_risk_tier("high") == Verdict.HOLD
    assert from_risk_tier("fail") == Verdict.DENY
    assert from_risk_tier("") == Verdict.PERMIT          # no finding = no constraint
    assert from_risk_tier("wat") == Verdict.DENY         # unknown tier → fail-safe DENY


def test_record_is_signed_and_attributed(ws):
    f, lr = ws
    r = TF.record_tool_verdict(f, connector_id="checker-A", raw_tier="fail",
                               input_ref="candidate#42 features…", log_root=lr)
    assert r["verdict"] == "deny" and r["audit_id"]
    assert r["input_digest"].startswith("sha256:")       # replay: digest, not re-run
    v = MutationLog(Path(f), log_root=Path(lr)).verify_chain()
    assert v.ok and v.signature_failures == []           # tamper-evident


def test_strictest_wins_join(ws):
    f, lr = ws
    TF.record_tool_verdict(f, connector_id="checker-A", raw_tier="fail", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)
    assert d["decision"] == "deny"                       # a lone tool DENY denies
    assert d["disagreement"] is True                     # local permit vs tool deny — recorded
    assert d["sources"][0]["connector_id"] == "checker-A"


def test_kill_switch_drops_the_tools_verdict(ws):
    f, lr = ws
    TF.record_tool_verdict(f, connector_id="checker-A", raw_tier="fail", log_root=lr)
    assert TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)["decision"] == "deny"
    TF.revoke_tool(f, connector_id="checker-A", reason="false positives", log_root=lr)
    after = TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)
    assert after["decision"] == "permit"                 # revoked tool no longer constrains
    assert "checker-A" in after["revoked"]


def test_tool_only_affects_its_linked_use_cases(ws):
    f, lr = ws
    TF.record_tool_verdict(f, connector_id="checker-A", raw_tier="fail", log_root=lr)
    # a DIFFERENT use case the tool is not linked to is unaffected
    d = TF.federated_decision(f, use_case_id="unrelated", local=Verdict.PERMIT, log_root=lr)
    assert d["decision"] == "permit" and d["sources"] == []


def test_per_channel_floor_governs_individually(tmp_path):
    """Each channel carries its own policy floor, honored even with NO tool verdict."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    # channel A always needs a person on 'score'; channel B is unconstrained on 'deploy'
    C.register_connector(f, connector_id="chan-A", role="oversight", channel="jira",
                         use_cases=["score"], floor="hold", log_root=lr)
    C.register_connector(f, connector_id="chan-B", role="ingress", channel="api",
                         use_cases=["deploy"], floor="permit", log_root=lr)
    # 'score' inherits chan-A's hold floor (no tool verdict needed)
    dscore = TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)
    assert dscore["decision"] == "hold"
    s0 = dscore["sources"][0]
    assert s0["connector_id"] == "chan-A" and s0["verdict"] == "hold" and s0["floor"] == "hold"
    assert s0["tool_verdict"] is None
    # 'deploy' is untouched by chan-A (channels kept apart); chan-B's permit floor is no-op
    ddeploy = TF.federated_decision(f, use_case_id="deploy", local=Verdict.PERMIT, log_root=lr)
    assert ddeploy["decision"] == "permit" and ddeploy["sources"] == []


def test_floor_and_tool_verdict_compose_strictest(tmp_path):
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="chan-A", role="oversight", channel="jira",
                         use_cases=["score"], floor="hold", log_root=lr)
    # a 'fail' tool verdict (deny) is stricter than the channel's hold floor → deny wins
    TF.record_tool_verdict(f, connector_id="chan-A", raw_tier="fail", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)
    assert d["decision"] == "deny"
    assert d["sources"][0]["floor"] == "hold" and d["sources"][0]["tool_verdict"] == "deny"


def test_killing_a_channel_drops_its_floor_too(tmp_path):
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="chan-A", role="oversight", channel="jira",
                         use_cases=["score"], floor="deny", log_root=lr)
    assert TF.federated_decision(f, use_case_id="score", log_root=lr)["decision"] == "deny"
    TF.revoke_tool(f, connector_id="chan-A", log_root=lr)
    assert TF.federated_decision(f, use_case_id="score", log_root=lr)["decision"] == "permit"


def test_corrupt_stored_verdict_fails_closed_not_crash(tmp_path):
    """Loop fix: a corrupt/tampered verdict read back from the log must coerce to DENY
    (fail-safe), never crash federated_decision or fail-open to permit."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="chan-A", role="oversight", channel="api",
                         use_cases=["score"], log_root=lr)
    TF.record_tool_verdict(f, connector_id="chan-A", raw_tier="pass", log_root=lr)
    # tamper: append a tool-verdict with a corrupt verdict value
    from rvnd.mutation_log import MutationLog, LogEvent
    MutationLog(Path(f), log_root=Path(lr)).append(LogEvent(
        event="system", folder_path=f, pair_id="chan-A", channel="system", actor="x",
        extra={"kind": "tool-verdict", "connector_id": "chan-A", "verdict": "not_a_verdict",
               "raw_output": "??", "input_digest": "sha256:x"}))
    d = TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)
    assert d["decision"] == "deny"   # corrupt latest verdict → DENY, no crash


def test_invalid_local_via_op_fails_closed(tmp_path, monkeypatch):
    """Loop fix: an invalid `local` to the federated_decision op must not crash the
    MCP call — it fails safe to DENY."""
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    from rvnd import mcp_server as M
    f = tmp_path / "w"; f.mkdir()
    M.workspace_workspace("add", {"folder_context": str(f)})
    d = M.workspace_workflow("federated_decision", {"folder_context": str(f),
                                               "use_case_id": "score", "local": "garbage"})
    assert d["decision"] == "deny"   # invalid local → fail-safe DENY, not a crash


def test_revoked_tool_verdict_stays_visible(tmp_path):
    """Loop fix: a revoked tool is dropped from the JOIN but its verdict stays visible
    in revoked_sources (disagreement recorded, never hidden)."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="chan-A", role="oversight", channel="api",
                         use_cases=["score"], log_root=lr)
    TF.record_tool_verdict(f, connector_id="chan-A", raw_tier="fail", log_root=lr)
    TF.revoke_tool(f, connector_id="chan-A", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)
    assert d["decision"] == "permit"                          # dropped from the join
    assert d["revoked_sources"] == [{"connector_id": "chan-A", "group": "", "group_floor": "permit",
                                     "verdict": "deny", "raw_output": "fail"}]


def test_group_floor_governs_all_its_channels(tmp_path):
    """An MCP client = a group. The group floor governs ALL its channels collectively,
    even ones with no floor of their own (the group-bus)."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    # two channels from the same client 'n8n', neither with its own floor
    C.register_connector(f, connector_id="n8n-jira", role="oversight", channel="jira",
                         use_cases=["score"], group="n8n", log_root=lr)
    C.register_connector(f, connector_id="n8n-github", role="ingress", channel="api",
                         use_cases=["score"], group="n8n", log_root=lr)
    assert TF.federated_decision(f, use_case_id="score", log_root=lr)["decision"] == "permit"
    # the client's group-bus is set to 'hold' → both its channels now hold
    TF.set_group_floor(f, group_id="n8n", floor="hold", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)
    assert d["decision"] == "hold"
    assert {s["connector_id"] for s in d["sources"]} == {"n8n-jira", "n8n-github"}
    assert all(s["group"] == "n8n" and s["group_floor"] == "hold" for s in d["sources"])


def test_channel_can_be_stricter_than_its_group(tmp_path):
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="n8n-a", role="oversight", channel="jira",
                         use_cases=["score"], group="n8n", floor="deny", log_root=lr)
    TF.set_group_floor(f, group_id="n8n", floor="hold", log_root=lr)
    # channel deny is stricter than the group's hold → deny wins for that channel
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    assert d["decision"] == "deny"


def test_group_kill_switch_mutes_the_whole_client(tmp_path):
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="n8n-a", role="oversight", channel="jira",
                         use_cases=["score"], group="n8n", floor="deny", log_root=lr)
    C.register_connector(f, connector_id="n8n-b", role="ingress", channel="api",
                         use_cases=["score"], group="n8n", floor="hold", log_root=lr)
    assert TF.federated_decision(f, use_case_id="score", log_root=lr)["decision"] == "deny"
    TF.revoke_group(f, group_id="n8n", reason="client offboarded", log_root=lr)
    after = TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)
    assert after["decision"] == "permit" and after["sources"] == []   # whole client muted


def test_group_revoked_channels_stay_visible_even_without_a_verdict(tmp_path):
    """Loop fix: a channel muted by a group revocation is recorded in revoked_sources
    even if it never reported a verdict (full membership reconstructable)."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="n8n-a", role="oversight", channel="jira",
                         use_cases=["score"], group="n8n", floor="hold", log_root=lr)  # no tool verdict
    TF.revoke_group(f, group_id="n8n", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    assert d["sources"] == []
    assert d["revoked_sources"] == [{"connector_id": "n8n-a", "group": "n8n",
                                     "group_floor": "permit", "verdict": None, "raw_output": None}]


def test_empty_group_id_is_rejected_and_ignored(tmp_path):
    """Loop fix: revoke_group rejects an empty group_id (would mute ungrouped channels);
    a tampered empty-group policy/revoke in the log never governs ungrouped channels."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    with pytest.raises(ValueError):
        TF.revoke_group(f, group_id="", log_root=lr)
    # tamper: inject an empty-group-id policy directly; an ungrouped channel must ignore it
    from rvnd.mutation_log import MutationLog, LogEvent
    MutationLog(Path(f), log_root=Path(lr)).append(LogEvent(
        event="system", folder_path=f, pair_id="group:", channel="system", actor="x",
        extra={"kind": "group-policy", "group_id": "", "floor": "deny"}))
    C.register_connector(f, connector_id="loose", role="ingress", channel="api",
                         use_cases=["score"], log_root=lr)   # group="" (ungrouped)
    d = TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)
    assert d["decision"] == "permit"   # phantom empty-group policy ignored


def test_invalid_floor_is_rejected(tmp_path):
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    with pytest.raises(ValueError):
        C.register_connector(f, connector_id="x", role="ingress", channel="api",
                             floor="maybe", log_root=lr)


@pytest.fixture
def split(tmp_path: Path):
    """Two federated tools linked to 'score' that disagree: hold vs deny."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="checker-A", role="oversight", channel="api",
                         use_cases=["score"], log_root=lr)
    C.register_connector(f, connector_id="checker-B", role="oversight", channel="api",
                         use_cases=["score"], log_root=lr)
    TF.record_tool_verdict(f, connector_id="checker-A", raw_tier="fail", log_root=lr)
    TF.record_tool_verdict(f, connector_id="checker-B", raw_tier="high", log_root=lr)
    return f, lr


def test_override_refused_without_disagreement(tmp_path):
    """No split, nothing to resolve: an override on a unanimous join is refused."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="checker-A", role="oversight", channel="api",
                         use_cases=["score"], log_root=lr)
    with pytest.raises(ValueError, match="without disagreement"):
        TF.record_federation_override(f, use_case_id="score", verdict="permit",
                                      actor="alice", reason="unblock", log_root=lr)


def test_override_needs_actor_and_reason(split):
    f, lr = split
    with pytest.raises(ValueError, match="named actor"):
        TF.record_federation_override(f, use_case_id="score", verdict="hold",
                                      actor="", reason="why", log_root=lr)
    with pytest.raises(ValueError, match="reason"):
        TF.record_federation_override(f, use_case_id="score", verdict="hold",
                                      actor="alice", reason="  ", log_root=lr)


def test_override_verdict_must_be_a_tool_emitted_reading(split):
    """The human picks among the words the TOOLS emitted (hold|deny here) — a
    verdict no tool emitted (permit) is refused, never recorded."""
    f, lr = split
    with pytest.raises(ValueError, match="not among the tool-emitted readings"):
        TF.record_federation_override(f, use_case_id="score", verdict="permit",
                                      actor="alice", reason="ship it", log_root=lr)


def test_override_refused_on_floor_only_disagreement(tmp_path):
    """A floors-vs-local split has NO tool-emitted words: it is authored policy,
    resolved by editing the floor, never by overriding."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="chan-A", role="oversight", channel="jira",
                         use_cases=["score"], floor="hold", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    assert d["disagreement"] is True             # public flag semantics unchanged
    with pytest.raises(ValueError, match="editing the floor"):
        TF.record_federation_override(f, use_case_id="score", verdict="hold",
                                      actor="alice", reason="unblock", log_root=lr)


def test_override_refused_on_single_emitted_word(tmp_path):
    """Unanimous tools (both deny) disagree only with the internal local-permit
    default — one emitted word is no tool split, so a no-op 'deny' override
    is refused instead of recorded."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    for cid in ("checker-A", "checker-B"):
        C.register_connector(f, connector_id=cid, role="oversight", channel="api",
                             use_cases=["score"], log_root=lr)
        TF.record_tool_verdict(f, connector_id=cid, raw_tier="fail", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    assert d["disagreement"] is True             # deny tools vs local permit — unchanged
    with pytest.raises(ValueError, match="no tool split"):
        TF.record_federation_override(f, use_case_id="score", verdict="deny",
                                      actor="alice", reason="agree", log_root=lr)


def test_override_applies_additively_and_carries_the_spread(split):
    f, lr = split
    r = TF.record_federation_override(f, use_case_id="score", verdict="hold",
                                      actor="alice", reason="A is a known false positive",
                                      log_root=lr)
    assert r["ok"] and r["audit_id"] and r["spread_digest"].startswith("sha256:")
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    # the join's existing keys keep their exact meaning
    assert d["decision"] == "deny" and d["disagreement"] is True
    assert {s["connector_id"] for s in d["sources"]} == {"checker-A", "checker-B"}
    # the override is additive and effective while it stands
    assert d["override"] == {"verdict": "hold", "actor": "alice",
                             "reason": "A is a known false positive",
                             "audit_id": r["audit_id"],
                             "spread_digest": r["spread_digest"],
                             "superseded": False}
    assert d["effective_decision"] == "hold"
    v = MutationLog(Path(f), log_root=Path(lr)).verify_chain()
    assert v.ok and v.signature_failures == []           # signed like every record


def test_override_superseded_by_newer_tool_verdict(split):
    """A newer reading from any linked source supersedes the override fail-closed:
    it stops applying but stays visible."""
    f, lr = split
    TF.record_federation_override(f, use_case_id="score", verdict="hold",
                                  actor="alice", reason="resolved", log_root=lr)
    TF.record_tool_verdict(f, connector_id="checker-B", raw_tier="fail", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    assert d["override"]["superseded"] is True
    assert d["override"]["verdict"] == "hold"            # visible, not applying
    assert d["effective_decision"] == d["decision"] == "deny"


def test_override_superseded_by_tool_revocation(split):
    """The kill switch beats an older loosening: revoking the linked source whose
    reading the override picked supersedes the override — revoke_tool promises
    the revoked reading is dropped from every future join, and the override
    must not carry it onward."""
    f, lr = split
    TF.record_federation_override(f, use_case_id="score", verdict="hold",
                                  actor="alice", reason="B is right", log_root=lr)
    TF.revoke_tool(f, connector_id="checker-B", log_root=lr)   # the hold-sayer dies
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    assert d["override"]["superseded"] is True           # visible, not applying
    assert d["effective_decision"] == d["decision"] == "deny"  # A's deny governs again


def test_override_superseded_by_group_revocation(tmp_path):
    """Killing a linked source's GROUP after the override supersedes it too —
    the group kill switch is the same ground truth as the channel one."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="checker-A", role="oversight", channel="api",
                         use_cases=["score"], log_root=lr)
    C.register_connector(f, connector_id="checker-B", role="oversight", channel="api",
                         use_cases=["score"], group="n8n", log_root=lr)
    TF.record_tool_verdict(f, connector_id="checker-A", raw_tier="fail", log_root=lr)
    TF.record_tool_verdict(f, connector_id="checker-B", raw_tier="high", log_root=lr)
    TF.record_federation_override(f, use_case_id="score", verdict="hold",
                                  actor="alice", reason="B is right", log_root=lr)
    TF.revoke_group(f, group_id="n8n", reason="client offboarded", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    assert d["override"]["superseded"] is True
    assert d["effective_decision"] == d["decision"] == "deny"


def test_override_superseded_by_group_floor_change(tmp_path):
    """A group-floor change for a linked source's group after the override
    supersedes it (the newest tightening beats the older loosening); a floor
    change on an UNRELATED group does not."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="checker-A", role="oversight", channel="api",
                         use_cases=["score"], log_root=lr)
    C.register_connector(f, connector_id="checker-B", role="oversight", channel="api",
                         use_cases=["score"], group="n8n", log_root=lr)
    TF.record_tool_verdict(f, connector_id="checker-A", raw_tier="fail", log_root=lr)
    TF.record_tool_verdict(f, connector_id="checker-B", raw_tier="high", log_root=lr)
    TF.record_federation_override(f, use_case_id="score", verdict="hold",
                                  actor="alice", reason="B is right", log_root=lr)
    TF.set_group_floor(f, group_id="elsewhere", floor="deny", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    assert d["override"]["superseded"] is False          # unlinked group — override stands
    assert d["effective_decision"] == "hold"
    TF.set_group_floor(f, group_id="n8n", floor="deny", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    assert d["override"]["superseded"] is True           # linked group's floor moved
    assert d["effective_decision"] == d["decision"] == "deny"


def test_no_override_yields_null_and_effective_equals_decision(ws):
    f, lr = ws
    TF.record_tool_verdict(f, connector_id="checker-A", raw_tier="fail", log_root=lr)
    d = TF.federated_decision(f, use_case_id="score", log_root=lr)
    assert d["override"] is None
    assert d["effective_decision"] == d["decision"]


def test_override_reachable_via_mcp_op(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    from rvnd import mcp_server as M
    f = tmp_path / "w"; f.mkdir()
    M.workspace_workspace("add", {"folder_context": str(f)})
    for cid, tier in (("tool-a", "fail"), ("tool-b", "high")):
        M.workspace_workflow("connector_register", {"folder_context": str(f), "connector_id": cid,
                                               "role": "oversight", "channel": "api", "use_cases": ["score"]})
        M.workspace_workflow("tool_verdict", {"folder_context": str(f), "connector_id": cid, "raw_tier": tier})
    # a refused override answers with its wording, not a stack trace
    bad = M.workspace_workflow("federation_override", {"folder_context": str(f), "use_case_id": "score",
                                                  "verdict": "permit", "actor": "alice", "reason": "r"})
    assert bad["ok"] is False and "not among the tool-emitted readings" in bad["error"]
    ok = M.workspace_workflow("federation_override", {"folder_context": str(f), "use_case_id": "score",
                                                 "verdict": "hold", "actor": "alice", "reason": "resolved"})
    assert ok["ok"] is True
    d = M.workspace_workflow("federated_decision", {"folder_context": str(f), "use_case_id": "score"})
    assert d["decision"] == "deny" and d["effective_decision"] == "hold"


def test_reachable_via_mcp_op(tmp_path, monkeypatch):
    """The bus is wired to a real entrypoint: workspace_workflow ops record + join."""
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    from rvnd import mcp_server as M
    f = tmp_path / "w"; f.mkdir()
    M.workspace_workspace("add", {"folder_context": str(f)})
    M.workspace_workflow("connector_register", {"folder_context": str(f), "connector_id": "tool-x",
                                           "role": "oversight", "channel": "api", "use_cases": ["score"]})
    M.workspace_workflow("tool_verdict", {"folder_context": str(f), "connector_id": "tool-x", "raw_tier": "fail"})
    d = M.workspace_workflow("federated_decision", {"folder_context": str(f), "use_case_id": "score", "local": "permit"})
    assert d["decision"] == "deny" and d["disagreement"] is True
    M.workspace_workflow("tool_revoke", {"folder_context": str(f), "connector_id": "tool-x"})
    assert M.workspace_workflow("federated_decision", {"folder_context": str(f), "use_case_id": "score"})["decision"] == "permit"


def test_tool_ref_is_validated_fail_closed_and_listed(tmp_path):
    """tool_ref binds a connector to a host-invocable tool; a malformed ref is
    rejected, never stored as a silent no-op. A stored ref surfaces in
    list_connectors."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    with pytest.raises(ValueError, match="tool_name"):
        C.register_connector(f, connector_id="x", role="oversight", channel="api",
                             tool_ref="not-a-dict", log_root=lr)
    with pytest.raises(ValueError, match="tool_name"):
        C.register_connector(f, connector_id="x", role="oversight", channel="api",
                             tool_ref={"arg_mapping": {}}, log_root=lr)
    with pytest.raises(ValueError, match="arg_mapping"):
        C.register_connector(f, connector_id="x", role="oversight", channel="api",
                             tool_ref={"tool_name": "scan", "arg_mapping": "nope"}, log_root=lr)
    C.register_connector(f, connector_id="checker-A", role="oversight", channel="api",
                         use_cases=["score"],
                         tool_ref={"tool_name": "scan", "arg_mapping": {"input_ref": "text"}},
                         log_root=lr)
    rows = C.list_connectors(f, log_root=lr)
    assert rows[0]["tool_ref"] == {"tool_name": "scan", "arg_mapping": {"input_ref": "text"}}


def test_tool_ref_arg_mapping_keys_and_values_validated(tmp_path):
    """arg_mapping renames the plannable inputs only; an unknown key would never
    be applied by tool_call_plan and a non-string value can never be a tool-arg
    name — both are refused in words, never stored as a silent no-op."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    with pytest.raises(ValueError, match="not a plannable input"):
        C.register_connector(f, connector_id="x", role="oversight", channel="api",
                             tool_ref={"tool_name": "scan",
                                       "arg_mapping": {"payload": "text"}}, log_root=lr)
    with pytest.raises(ValueError, match="non-empty string"):
        C.register_connector(f, connector_id="x", role="oversight", channel="api",
                             tool_ref={"tool_name": "scan",
                                       "arg_mapping": {"input_ref": 7}}, log_root=lr)
    with pytest.raises(ValueError, match="non-empty string"):
        C.register_connector(f, connector_id="x", role="oversight", channel="api",
                             tool_ref={"tool_name": "scan",
                                       "arg_mapping": {"input_ref": "  "}}, log_root=lr)


def test_tool_call_plan_happy_path(tmp_path):
    """The plan is the descriptor the HOST invokes with: arg_mapping applied,
    provenance carried, return path named. Read-only — nothing is logged."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="checker-A", role="oversight", channel="api",
                         use_cases=["score"], group="g1",
                         tool_ref={"tool_name": "scan", "arg_mapping": {"input_ref": "text"}},
                         log_root=lr)
    before = sum(1 for _ in MutationLog(Path(f), log_root=Path(lr)).replay())
    plan = TF.tool_call_plan(f, connector_id="checker-A", input_ref="candidate#42", log_root=lr)
    assert plan["kind"] == "mcp_tool_call_descriptor"
    assert plan["tool_name"] == "scan"
    assert plan["args"] == {"text": "candidate#42"}       # arg_mapping applied
    assert plan["input_digest"].startswith("sha256:")
    assert plan["provenance"] == {"connector_id": "checker-A", "group": "g1",
                                  "folder_context": f}
    assert plan["map_result_via"] == "tool_verdict"
    after = sum(1 for _ in MutationLog(Path(f), log_root=Path(lr)).replay())
    assert after == before                                # read-only projection


def test_tool_call_plan_without_mapping_passes_input_ref(tmp_path):
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="checker-A", role="oversight", channel="api",
                         tool_ref={"tool_name": "scan"}, log_root=lr)
    plan = TF.tool_call_plan(f, connector_id="checker-A", input_ref="x", log_root=lr)
    assert plan["args"] == {"input_ref": "x"}


def test_tool_call_plan_refuses_fail_closed(tmp_path):
    """Unknown, unbound, revoked, or group-killed connectors are never planned."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    with pytest.raises(ValueError, match="unknown connector"):
        TF.tool_call_plan(f, connector_id="ghost", log_root=lr)
    C.register_connector(f, connector_id="bare", role="oversight", channel="api", log_root=lr)
    with pytest.raises(ValueError, match="no tool_ref binding"):
        TF.tool_call_plan(f, connector_id="bare", log_root=lr)
    C.register_connector(f, connector_id="dead", role="oversight", channel="api",
                         tool_ref={"tool_name": "scan"}, log_root=lr)
    TF.revoke_tool(f, connector_id="dead", log_root=lr)
    with pytest.raises(ValueError, match="revoked"):
        TF.tool_call_plan(f, connector_id="dead", log_root=lr)
    C.register_connector(f, connector_id="grouped", role="oversight", channel="api",
                         group="n8n", tool_ref={"tool_name": "scan"}, log_root=lr)
    TF.revoke_group(f, group_id="n8n", log_root=lr)
    with pytest.raises(ValueError, match="group"):
        TF.tool_call_plan(f, connector_id="grouped", log_root=lr)


def test_round_trip_plan_then_verdict(tmp_path):
    """The pull-model loop: plan -> the host invokes -> the answer comes back
    through record_tool_verdict and joins. The plan's input digest matches the
    recorded verdict's, so the pair is linkable in replay."""
    lr = str(tmp_path / "logs"); f = str(tmp_path / "w"); Path(f).mkdir()
    C.register_connector(f, connector_id="checker-A", role="oversight", channel="api",
                         use_cases=["score"],
                         tool_ref={"tool_name": "scan", "arg_mapping": {"input_ref": "text"}},
                         log_root=lr)
    plan = TF.tool_call_plan(f, connector_id="checker-A", input_ref="candidate#42", log_root=lr)
    # the host runs plan["tool_name"](plan["args"]) and reports the tool's word back
    r = TF.record_tool_verdict(f, connector_id="checker-A", raw_tier="fail",
                               input_ref="candidate#42", log_root=lr)
    assert r["input_digest"] == plan["input_digest"]
    d = TF.federated_decision(f, use_case_id="score", local=Verdict.PERMIT, log_root=lr)
    assert d["decision"] == "deny"
    assert d["sources"][0]["input_digest"] == plan["input_digest"]


def test_tool_call_plan_reachable_via_mcp_op(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    from rvnd import mcp_server as M
    f = tmp_path / "w"; f.mkdir()
    M.workspace_workspace("add", {"folder_context": str(f)})
    M.workspace_workflow("connector_register", {
        "folder_context": str(f), "connector_id": "tool-x", "role": "oversight",
        "channel": "api", "use_cases": ["score"],
        "tool_ref": {"tool_name": "scan", "arg_mapping": {"input_ref": "text"}}})
    plan = M.workspace_workflow("tool_call_plan", {"folder_context": str(f),
                                              "connector_id": "tool-x", "input_ref": "c#1"})
    assert plan["kind"] == "mcp_tool_call_descriptor" and plan["args"] == {"text": "c#1"}
    # a refused plan answers with its wording, not a stack trace
    bad = M.workspace_workflow("tool_call_plan", {"folder_context": str(f), "connector_id": "ghost"})
    assert bad["ok"] is False and "unknown connector" in bad["error"]
    lst = M.workspace_workflow("connector_list", {"folder_context": str(f)})
    assert lst["connectors"][0]["tool_ref"]["tool_name"] == "scan"
