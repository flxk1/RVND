# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Deterministic workspace router ("cells-lite") — concept routing across workspaces,
no model/embedding, wall-respecting."""

from __future__ import annotations

from rvnd import workspace_router, seal, workspace_lock, workspace_registry
from rvnd.memory import WorkspaceMemory


def _seed(folder, log_root, summaries):
    mem = WorkspaceMemory(folder, log_root=log_root, actor="t")
    for i, sm in enumerate(summaries):
        pid = f"sha256:{folder.name}{i}"
        mem.remember({
            "id": pid,
            "problem": {"id": f"p{folder.name}{i}", "scope": "s", "type": "rule", "summary": sm},
            "solution": {"id": pid, "problem_id": f"p{folder.name}{i}", "body": sm,
                         "authority_tier": 1, "confidence": 1.0, "body_format": "prose"},
        })


def test_router_ranks_by_concept(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    logr = tmp_path / "log"
    fin = tmp_path / "Finance"; fin.mkdir()
    law = tmp_path / "Legal"; law.mkdir()
    _seed(fin, logr, ["invoice vat payment for client", "royalty statement payout"])
    _seed(law, logr, ["ai act annex high-risk obligations", "gdpr dpia controller"])

    r = workspace_router.route("prepare an invoice with vat", [str(fin), str(law)], log_root=logr)
    assert r and r[0]["folder"] == str(fin)

    r2 = workspace_router.route("hiring agent high-risk under the ai act", [str(fin), str(law)], log_root=logr)
    assert r2 and r2[0]["folder"] == str(law)

    # zero-overlap query drops everything
    assert workspace_router.route("zzzqqq nonsense", [str(fin), str(law)], log_root=logr) == []


def test_router_sealed_locked_is_label_only(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    logr = tmp_path / "log"
    fin = tmp_path / "Finance"; fin.mkdir()
    _seed(fin, logr, ["invoice vat payment"])
    seal.seal_folder(fin, passphrase="pw", log_root=logr)
    workspace_lock.lock(fin, log_root=logr)

    sig, label_only = workspace_router.signature_for_workspace(str(fin), log_root=logr, label="Finance")
    assert label_only is True and "finance" in sig          # name token still routes
    assert "invoice" not in sig                              # sealed content not leaked

    r = workspace_router.route("finance", [str(fin)], log_root=logr)
    assert r and r[0]["label_only"] is True

    # unlock → content now contributes
    workspace_lock.unlock(fin, passphrase="pw", log_root=logr)
    sig2, lo2 = workspace_router.signature_for_workspace(str(fin), log_root=logr, label="Finance")
    assert lo2 is False and "invoice" in sig2
    workspace_lock.lock(fin, log_root=logr)


def test_route_to_workspace_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    from rvnd import mcp_server as M
    rtc = getattr(M.route_to_workspace, "fn", M.route_to_workspace)
    logr = tmp_path / "log"
    fin = tmp_path / "Finance"; fin.mkdir()
    law = tmp_path / "Legal"; law.mkdir()
    _seed(fin, logr, ["invoice vat payment"])
    _seed(law, logr, ["ai act annex high-risk"])
    workspace_registry.add_known_workspace(str(fin), label="Finance", log_root=logr)
    workspace_registry.add_known_workspace(str(law), label="Legal", log_root=logr)

    out = rtc("invoice vat")
    assert out["ok"] and out["count"] >= 1
    assert out["candidates"][0]["folder"] == str(fin)
