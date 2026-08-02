# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Commercial-capacity evidence is local, signed and identity-aware."""
from __future__ import annotations

import json
import uuid

from workspaces.cli import main
from workspaces.licence_usage import capacity_report
from workspaces.mutation_log import MutationLog
from workspaces.parties import list_parties, register_party, set_party_status
from workspaces.use_case import register_use_case
from workspaces.workspace_registry import add_known_workspace


def _case(folder: str, use_case_id: str, agents: list[str], log_root: str) -> None:
    register_use_case(
        folder, use_case_id=use_case_id, name=use_case_id,
        fingerprint={}, risk="low", allowed_agents=agents,
        actor="operator", log_root=log_root)


def test_agent_uid_is_stable_on_reregistration(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    folder = str(tmp_path / "workspace")
    (tmp_path / "workspace").mkdir()
    log_root = str(tmp_path / "logs")

    first = register_party(folder, "bot", "agent", log_root=log_root)
    second = register_party(folder, "bot", "agent", grade="L2",
                            log_root=log_root)

    assert first["agent_uid"] == second["agent_uid"]
    assert str(uuid.UUID(first["agent_uid"])) == first["agent_uid"]
    party = list_parties(folder, kind="agent", log_root=log_root)["parties"][0]
    assert party["agent_uid"] == first["agent_uid"]


def test_capacity_report_peak_status_and_cross_workspace_dedup(
        tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root_path = tmp_path / "logs"
    log_root = str(log_root_path)
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    add_known_workspace(a, log_root=log_root_path)
    add_known_workspace(b, log_root=log_root_path)

    shared_uid = str(uuid.uuid4())
    register_party(str(a), "shared-a", "agent", agent_uid=shared_uid,
                   log_root=log_root)
    register_party(str(b), "shared-b", "agent", agent_uid=shared_uid,
                   log_root=log_root)
    second = register_party(str(a), "second", "agent", log_root=log_root)
    _case(str(a), "case-a", ["shared-a", "second"], log_root)
    _case(str(b), "case-b", ["shared-b"], log_root)
    set_party_status(str(a), "second", "suspended", log_root=log_root)

    report = capacity_report(log_root=log_root_path, licensed_capacity=2)

    assert report["verified"] is True
    assert report["identity_basis"] == "agent_uid"
    assert report["peak_enabled_agents"] == 2
    assert report["current_enabled_agents"] == 1
    assert report["within_capacity"] is True
    assert len(report["peak_agent_refs"]) == 2
    assert all(shared_uid not in ref and second["agent_uid"] not in ref
               for ref in report["peak_agent_refs"])


def test_deleting_a_chain_cannot_report_a_verified_zero(tmp_path, monkeypatch):
    # Capacity evidence is contractual, so under-reporting must not pass as verified:
    # removing a workspace's event log has to invalidate the report rather than
    # quietly drop that workspace's agents from the count.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root_path = tmp_path / "logs"
    log_root = str(log_root_path)
    folder = tmp_path / "workspace"
    folder.mkdir()
    add_known_workspace(folder, log_root=log_root_path)
    for name in ("bot-a", "bot-b", "bot-c"):
        register_party(str(folder), name, "agent", log_root=log_root)
    _case(str(folder), "case", ["bot-a", "bot-b", "bot-c"], log_root)

    before = capacity_report(log_root=log_root_path, licensed_capacity=2)
    assert before["verified"] is True
    assert before["peak_enabled_agents"] == 3
    assert before["within_capacity"] is False

    MutationLog(str(folder), log_root=log_root_path).log_file.unlink()

    after = capacity_report(log_root=log_root_path, licensed_capacity=2)
    assert after["verified"] is False
    assert after["incomplete"] is True


def test_cli_json_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root_path = tmp_path / "logs"
    folder = tmp_path / "workspace"
    folder.mkdir()
    add_known_workspace(folder, log_root=log_root_path)
    register_party(str(folder), "bot", "agent", log_root=str(log_root_path))
    _case(str(folder), "case", ["bot"], str(log_root_path))

    code = main(["--log-root", str(log_root_path), "licence", "usage",
                 "--capacity", "1", "--json"])
    report = json.loads(capsys.readouterr().out)

    assert code == 0
    assert report["current_enabled_agents"] == 1
    assert report["peak_enabled_agents"] == 1
    assert report["within_capacity"] is True
