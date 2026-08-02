# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
from pathlib import Path

from workspaces.adapters.solver.reasoning import compose_paths
from workspaces.adapters.solver.versum import dimensioned_edges
from workspaces.ingest.versum import ingest_into_versum


def test_policy_ingest_reaches_versum_and_solver(tmp_path: Path):
    source = tmp_path / "policy.txt"
    source.write_text(
        "Controller must notify. notify must retain.",
        encoding="utf-8",
    )

    result = ingest_into_versum(str(source), str(tmp_path))
    edges = dimensioned_edges(str(tmp_path))

    assert result["ok"] is True
    assert result["write"]["status"] == "inserted"
    assert len(edges) == 2
    assert edges[0].predicate == "O"
    assert compose_paths(edges, start="Controller")


def test_governance_policy_reaches_versum(tmp_path: Path):
    source = tmp_path / "policy.txt"
    source.write_text(
        "Loan approvals must be reviewed by a risk officer.",
        encoding="utf-8",
    )

    result = ingest_into_versum(str(source), str(tmp_path))

    assert result["ok"] is True
    assert result["ingester"] == "policy"
    assert result["write"]["status"] == "inserted"


def test_workspace_containment_is_required(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "outside.txt"
    source.write_text("Controller must notify.", encoding="utf-8")

    try:
        ingest_into_versum(str(source), str(workspace))
    except PermissionError as exc:
        assert "outside the workspace" in str(exc)
    else:
        raise AssertionError("outside source was admitted")
