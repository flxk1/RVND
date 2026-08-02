# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace contract projection: governance is the DNA, present at every level."""
import os
from pathlib import Path

from workspaces import cli
from workspaces.workspace_contract import describe_workspace, WorkspaceContract
from workspaces.cross_workspace import cross_workspace_read

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def _ingest(folder: Path, name: str, text: str, lr: Path) -> None:
    (folder / "Inbox").mkdir(parents=True, exist_ok=True)
    f = folder / "Inbox" / name
    f.write_text(text)
    cli.main(["--log-root", str(lr), "ingest", str(f), "--folder", str(folder)])


def test_workspace_projects_to_contract_with_governance_dna(tmp_path):
    lr = tmp_path / "log"
    parent = tmp_path / "proj"
    child = tmp_path / "proj" / "sub"          # nested → a child workspace
    src = tmp_path / "law"
    _ingest(parent, "a.txt", "parent doc", lr)
    _ingest(child, "b.txt", "child doc", lr)
    _ingest(src, "c.txt", "law text", lr)
    cross_workspace_read(parent, [src], role="source", autonomy_grade="L2", log_root=lr)

    c = describe_workspace(parent, depth=1, log_root=lr)
    assert isinstance(c, WorkspaceContract)
    assert c.name == "proj"

    # governance is the DNA — present, with the four tools, and every edge gated
    g = c.governance
    assert g["tools"] == ["gate", "lock", "oversight", "grounder", "audit-chain"]
    assert g["oversight"] and "lock_enabled" in g
    assert g["edges_total"] >= 1
    assert g["ungoverned_edges"] == 0          # no edge composed without a verdict
    # chain integrity always verifies; signing is env-dependent (needs the host
    # key under ~/.workspace/keys — present in a real install, absent here)
    assert g["chain_ok"] in (True, None)
    assert isinstance(g["signed"], bool)

    # the edge from the governed cross-workspace link carries its verdict
    assert any(e.get("verdict") for e in c.edges)

    # fractal: the child workspace is the same contract shape, governed too
    assert len(c.children) == 1
    kid = c.children[0]
    assert isinstance(kid, WorkspaceContract) and kid.name == "sub"
    assert kid.governance["tools"] == ["gate", "lock", "oversight", "grounder", "audit-chain"]


def test_invertible_hierarchy_child_sees_parent(tmp_path):
    lr = tmp_path / "log"
    parent = tmp_path / "proj"
    child = tmp_path / "proj" / "sub"
    _ingest(parent, "a.txt", "p", lr)
    _ingest(child, "b.txt", "c", lr)

    kid = describe_workspace(child, depth=0, log_root=lr)
    assert any(p["folder"] == str(parent.resolve()) for p in kid.parent_perspectives)
