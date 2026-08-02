# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Egress board — the per-track "which of my tracks can act outside?" projection
(per-track-binding concept, UI step). Read-only; joins the egress connectors with
their cable state (credential_resolver.describe), fail-closed and secret-free.
"""
from __future__ import annotations

import json

from workspaces import connectors
from workspaces.connectors import egress_board


def _seed(f, lr):
    connectors.register_connector(f, connector_id="mail-in", role="ingress",
                                  channel="email", log_root=lr)
    connectors.register_connector(f, connector_id="legal-ovs", role="oversight",
                                  channel="message", log_root=lr)
    connectors.register_connector(f, connector_id="jira-out", role="egress",
                                  channel="ticket", floor="hold",
                                  credential_ref="env:JIRA_TOKEN", log_root=lr)
    connectors.register_connector(f, connector_id="mail-out", role="egress",
                                  channel="email", log_root=lr)


def test_board_rows_are_egress_only(tmp_path):
    f = str(tmp_path); lr = str(tmp_path / "l"); _seed(f, lr)
    b = egress_board(f, log_root=lr)
    assert [t["connector_id"] for t in b["tracks"]] == ["jira-out", "mail-out"]
    assert b["summary"]["tracks"] == 2


def test_cable_states_resolved_fail_closed(tmp_path, monkeypatch):
    f = str(tmp_path); lr = str(tmp_path / "l"); _seed(f, lr)
    # ref set but env missing -> unplugged; no ref -> no_cable
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    by_id = {t["connector_id"]: t for t in egress_board(f, log_root=lr)["tracks"]}
    assert by_id["jira-out"]["credential"]["status"] == "unplugged"
    assert by_id["mail-out"]["credential"]["status"] == "no_cable"
    # env present -> armed; the headline count follows
    monkeypatch.setenv("JIRA_TOKEN", "tok-123")
    b = egress_board(f, log_root=lr)
    by_id = {t["connector_id"]: t for t in b["tracks"]}
    assert by_id["jira-out"]["credential"]["status"] == "armed"
    assert b["summary"]["can_act_outside"] == 1
    assert b["summary"]["armed"] == 1 and b["summary"]["no_cable"] == 1


def test_board_never_carries_the_secret(tmp_path, monkeypatch):
    f = str(tmp_path); lr = str(tmp_path / "l"); _seed(f, lr)
    monkeypatch.setenv("JIRA_TOKEN", "SUPER-SECRET-VALUE")
    assert "SUPER-SECRET-VALUE" not in json.dumps(egress_board(f, log_root=lr))


def test_mode_is_honestly_attested_pre_broker(tmp_path, monkeypatch):
    """No broker holds a connector track's plug yet (step 4b unbuilt) — the board
    must say `attested` (witnessed, not preventable), never claim `enforced`."""
    f = str(tmp_path); lr = str(tmp_path / "l"); _seed(f, lr)
    monkeypatch.setenv("JIRA_TOKEN", "tok")
    assert all(t["mode"] == "attested" for t in egress_board(f, log_root=lr)["tracks"])


def test_floor_and_use_cases_ride_along(tmp_path):
    f = str(tmp_path); lr = str(tmp_path / "l"); _seed(f, lr)
    by_id = {t["connector_id"]: t for t in egress_board(f, log_root=lr)["tracks"]}
    assert by_id["jira-out"]["floor"] == "hold"
    assert by_id["mail-out"]["floor"] == "permit"


def test_facade_op_and_credential_ref_passthrough(tmp_path, monkeypatch):
    """workspace_workflow: connector_register accepts credential_ref; egress_board
    is reachable as an op."""
    import workspaces.mcp_server as M
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    f = str(tmp_path / "ws"); (tmp_path / "ws").mkdir()
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    M.workspace_workflow("connector_register", {
        "folder_context": f, "connector_id": "out", "role": "egress",
        "channel": "api", "credential_ref": "env:X_TOK", "actor": "t"})
    monkeypatch.setenv("X_TOK", "v")
    b = M.workspace_workflow("egress_board", {"folder_context": f})
    assert b["summary"]["can_act_outside"] == 1
    assert b["tracks"][0]["credential"]["credential_ref"] == "env:X_TOK"


def test_facade_op_attests_llm_broker_from_probe(tmp_path, monkeypatch):
    """The egress_board op feeds the live broker probe into the board: a bound
    broker sets llm_broker.bound_here True, every probe failure resolves to
    False (fail-safe)."""
    import workspaces.mcp_server as M
    import workspaces.lock as L
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = str(tmp_path / "ws"); (tmp_path / "ws").mkdir()
    M.workspace_workflow("connector_register", {
        "folder_context": f, "connector_id": "out", "role": "egress",
        "channel": "api", "actor": "t"})
    monkeypatch.setattr(L, "probe_broker",
                        lambda folder_context, **k: {"reachable": True, "bound_here": True})
    assert M.workspace_workflow("egress_board", {"folder_context": f})["llm_broker"]["bound_here"] is True
    monkeypatch.setattr(L, "probe_broker",
                        lambda folder_context, **k: {"reachable": False, "bound_here": False})
    assert M.workspace_workflow("egress_board", {"folder_context": f})["llm_broker"]["bound_here"] is False


def test_destination_class_validated_and_projected(tmp_path):
    """destination_class is a closed vocabulary, egress-only, undeclared by
    default; the board carries it per track."""
    import pytest
    f = str(tmp_path); lr = str(tmp_path / "l")
    connectors.register_connector(f, connector_id="llm-out", role="egress",
                                  channel="api", destination_class="llm",
                                  log_root=lr)
    connectors.register_connector(f, connector_id="mail-out", role="egress",
                                  channel="email", log_root=lr)
    with pytest.raises(ValueError, match="destination_class must be one of"):
        connectors.register_connector(f, connector_id="bad", role="egress",
                                      channel="api", destination_class="cloud",
                                      log_root=lr)
    with pytest.raises(ValueError, match="only valid on an egress"):
        connectors.register_connector(f, connector_id="in", role="ingress",
                                      channel="email", destination_class="llm",
                                      log_root=lr)
    by_id = {t["connector_id"]: t for t in egress_board(f, log_root=lr)["tracks"]}
    assert by_id["llm-out"]["destination_class"] == "llm"
    assert by_id["mail-out"]["destination_class"] == "undeclared"


def test_declared_llm_track_enforced_only_with_bound_broker(tmp_path):
    """The per-track enforced word: a declared llm track reads enforced while a
    bound broker gates LLM calls; without the broker, and on every other class,
    the mode stays attested."""
    f = str(tmp_path); lr = str(tmp_path / "l")
    connectors.register_connector(f, connector_id="llm-out", role="egress",
                                  channel="api", destination_class="llm",
                                  log_root=lr)
    connectors.register_connector(f, connector_id="jira-out", role="egress",
                                  channel="ticket", destination_class="tool_api",
                                  log_root=lr)
    connectors.register_connector(f, connector_id="mail-out", role="egress",
                                  channel="email", log_root=lr)
    bound = {t["connector_id"]: t["mode"] for t in egress_board(
        f, log_root=lr, llm_broker={"reachable": True, "bound_here": True})["tracks"]}
    assert bound == {"llm-out": "enforced", "jira-out": "attested",
                     "mail-out": "attested"}
    unbound = {t["connector_id"]: t["mode"] for t in egress_board(
        f, log_root=lr)["tracks"]}
    assert set(unbound.values()) == {"attested"}
