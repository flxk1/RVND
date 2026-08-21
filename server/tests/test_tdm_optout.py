# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""TDM / AI-training opt-out (concept § 1.7 music × AI, design § 3).

The folder policy can assert an Art.-4-DSM-shaped reservation; the egress
gate refuses training-scoped egress for that folder, before PII checks."""
from __future__ import annotations

import os

import pytest

from rvnd.mcp_impl import lock_egress_check
from rvnd.mutation_log import MutationLog
from rvnd.policy import FolderPolicy, load_policy, set_ai_training_optout

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    folder = tmp_path / "catalogue"
    folder.mkdir()
    return folder


def test_policy_field_round_trips():
    p = FolderPolicy(ai_training_optout=True)
    d = p.to_dict()
    assert d["ai_training_optout"] is True
    assert FolderPolicy.from_dict(d).ai_training_optout is True
    # Default stays off and emits no key (legacy files stay clean).
    assert "ai_training_optout" not in FolderPolicy().to_dict()
    assert FolderPolicy.from_dict({}).ai_training_optout is False


def test_setter_persists_and_audits(ws, tmp_path):
    set_ai_training_optout(ws, True, actor="test",
                           log_root=tmp_path / "logs")
    assert load_policy(ws).ai_training_optout is True
    log = MutationLog(ws, log_root=tmp_path / "logs")
    assert any((e.extra or {}).get("policy_change") == "ai_training_optout"
               and (e.extra or {}).get("enabled") is True
               for e in log.replay())


def test_training_egress_refused_when_optout(ws, tmp_path):
    set_ai_training_optout(ws, True, actor="test",
                           log_root=tmp_path / "logs")
    res = lock_egress_check(
        tool="export_corpus",
        arguments={"text": "anything"},
        task_scope=["training"],
        folder_context=str(ws),
    )
    assert res["action"] == "refuse"
    assert "TDM opt-out" in res["reason"]


def test_non_training_egress_unaffected_by_optout(ws, tmp_path):
    set_ai_training_optout(ws, True, actor="test",
                           log_root=tmp_path / "logs")
    res = lock_egress_check(
        tool="send_summary",
        arguments={"text": "plain business text"},
        task_scope=["summary"],
        folder_context=str(ws),
    )
    assert res["action"] != "refuse" or "TDM" not in str(res.get("reason", ""))


def test_training_egress_allowed_without_optout(ws):
    res = lock_egress_check(
        tool="export_corpus",
        arguments={"text": "anything"},
        task_scope=["training"],
        folder_context=str(ws),
    )
    assert "TDM" not in str(res.get("reason", ""))


def test_no_folder_context_means_no_tdm_check():
    res = lock_egress_check(
        tool="export_corpus",
        arguments={"text": "anything"},
        task_scope=["training"],
    )
    assert "TDM" not in str(res.get("reason", ""))


def test_assertion_cascades_to_subfolders(ws, tmp_path):
    """Catalogue asserts -> every release/track folder beneath is covered
    (strictest-wins, § 1.5 monotonicity). Siblings stay independent."""
    set_ai_training_optout(ws, True, actor="test",
                           log_root=tmp_path / "logs")
    track = ws / "release-01" / "track-03"
    track.mkdir(parents=True)
    res = lock_egress_check(tool="export_corpus", arguments={"text": "x"},
                            task_scope=["training"],
                            folder_context=str(track))
    assert res["action"] == "refuse" and "TDM" in res["reason"]

    sibling = ws.parent / "other-project"
    sibling.mkdir()
    res2 = lock_egress_check(tool="export_corpus", arguments={"text": "x"},
                             task_scope=["training"],
                             folder_context=str(sibling))
    assert "TDM" not in str(res2.get("reason", ""))


def test_subfolder_cannot_silently_unreserve(ws, tmp_path):
    set_ai_training_optout(ws, True, actor="test",
                           log_root=tmp_path / "logs")
    sub = ws / "release-02"
    sub.mkdir()
    # Explicitly setting False on the sub-folder does not defeat the
    # ancestor's assertion — withdrawal happens at the asserting level.
    set_ai_training_optout(sub, False, actor="test",
                           log_root=tmp_path / "logs")
    res = lock_egress_check(tool="export_corpus", arguments={"text": "x"},
                            task_scope=["training"],
                            folder_context=str(sub))
    assert res["action"] == "refuse"


def test_tdm_declare_writes_file_asserts_and_audits(ws, tmp_path):
    from rvnd.policy import TDM_DECLARATION_FILENAME, tdm_declare

    res = tdm_declare(ws, actor="test", log_root=tmp_path / "logs")
    assert res["ok"] and res["asserted_now"] is True
    decl = ws / TDM_DECLARATION_FILENAME
    assert decl.is_file()
    body = decl.read_text(encoding="utf-8")
    assert "status: reserved" in body and "2019/790" in body
    assert load_policy(ws).ai_training_optout is True
    log = MutationLog(ws, log_root=tmp_path / "logs")
    assert any((e.extra or {}).get("policy_change") == "tdm_declaration"
               for e in log.replay())
    # Idempotent-ish: second declare rewrites the file, asserted_now False.
    assert tdm_declare(ws, actor="test",
                       log_root=tmp_path / "logs")["asserted_now"] is False


def test_workspace_policy_facade_routes_tdm_ops(ws):
    from rvnd.mcp_server import workspace_policy

    r = workspace_policy("tdm_optout", {"folder_context": str(ws),
                                   "enabled": True, "actor": "test"})
    assert r["ok"] and r["ai_training_optout"] is True
    r2 = workspace_policy("tdm_declare", {"folder_context": str(ws),
                                     "actor": "test"})
    assert r2["ok"] and r2["declaration"].endswith("ai-training.txt")
    ops = {o["op"] for o in workspace_policy("help")["ops"]}
    assert {"tdm_optout", "tdm_declare"} <= ops
