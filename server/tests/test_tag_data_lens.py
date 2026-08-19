# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tag data-lens — the activation's tags are assembled at run time from
connector-DERIVED ∪ user-AUTHORED ∪ per-activation, so a `tags contains <t>` guard
fires on the LIVE path WITHOUT the caller passing tags (the gap Option 2 left).
Tags are neutral lineage facts; the guard that acts on them is authored.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from rvnd import mcp_server as M


def _connector(ws, cid, tags, use_cases):
    return M.workspace_workflow("connector_register", {"folder_context": ws,
        "connector_id": cid, "role": "ingress", "channel": "api",
        "use_cases": use_cases, "tags": tags, "actor": "x"})


@pytest.fixture
def ws(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "w"; f.mkdir()
    M.workspace_workspace("add", {"folder_context": str(f)})
    M.workspace_policy("party_register", {"folder_context": str(f), "party_id": "bot", "kind": "agent", "actor": "x"})
    M.workspace_workflow("patch_apply", {"folder_context": str(f), "actor": "x", "netlist":
        "actor bot\nhuman dpo role data-protection\ngate transfer risk low grant bot\n"
        "cord bot -> transfer\ncord transfer -> master\n"
        "reserve transfer by data-protection when tags contains non_eu\n"})
    return str(f)


def _disp(ws, issue=None):
    r = M.workspace_workflow("operate", {"folder_context": ws, "use_case_id": "transfer",
        "agent_id": "bot", "issues": [issue or {"issue_id": "i1", "issue_type": "transfer",
        "completeness": "high"}], "now_epoch": 1_750_000_000})
    return (r.get("steps") or [{}])[0].get("disposition")


def test_connector_derived_tag_fires_the_guard(ws):
    """A connector linked to the use case STAMPS its tag → the guard fires at run time,
    no tags passed by the caller."""
    _connector(ws, "eu-feed", ["non_eu"], ["transfer"])
    assert _disp(ws) == "reserved"


def test_no_connector_tag_means_guard_stays_quiet(ws):
    """Without the tag from anywhere, the guarded reserve does not fire."""
    assert _disp(ws) != "reserved"
    _connector(ws, "eu-feed", ["eu"], ["transfer"])
    assert _disp(ws) != "reserved"


def test_user_authored_use_case_tag_fires_the_guard(ws):
    """The user-authored overlay (uc.tags) also fires the guard — and a re-applied
    patch CARRIES the tags forward (sticky), so the reserve and the tag coexist."""
    M.workspace_workflow("use_case_register", {"folder_context": ws, "use_case_id": "transfer",
        "name": "transfer", "fingerprint": {}, "risk": "low", "allowed_agents": ["bot"],
        "tags": ["non_eu"], "actor": "x"})
    # re-apply the reserve; _loom_apply carries the just-authored tags forward
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\nhuman dpo role data-protection\ngate transfer risk low grant bot\n"
        "cord bot -> transfer\ncord transfer -> master\n"
        "reserve transfer by data-protection when tags contains non_eu\n"})
    assert _disp(ws) == "reserved"


def test_connector_tag_only_applies_to_its_linked_use_cases(ws):
    """A connector's tags reach only the use cases it links — no blanket leak."""
    _connector(ws, "other", ["non_eu"], ["something_else"])
    assert _disp(ws) != "reserved"


def test_connector_read_failure_is_journalled_not_swallowed(ws, monkeypatch):
    """Loop fix: a connector-store read failure is RECORDED on the chain (so an
    auditor can tell 'no connectors' from 'read failed' — non-repudiation), and the
    run proceeds with the tags it has; a programming bug would NOT be masked."""
    from rvnd import operations as ops
    from rvnd.mutation_log import MutationLog
    monkeypatch.setattr(ops, "list_connectors",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("simulated store failure")))
    _disp(ws)  # must not raise
    warns = [e.extra for e in MutationLog(ws, log_root=os.environ["WORKSPACE_L0_LOG_ROOT"]).replay()
             if (e.extra or {}).get("kind") == "RunWarning"]
    assert any(w.get("reason") == "connector-tag read failed" for w in warns), \
        "a connector read failure left no audit record"
