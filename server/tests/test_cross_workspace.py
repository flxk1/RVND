# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governed cross-workspace read: gate decides, target chain records, refusal holds."""
import os
from pathlib import Path

from rvnd import cli
from rvnd.cross_workspace import cross_workspace_read, ROLE_SOURCE, ROLE_COMPANION
from rvnd.action_gate import StandingApproval, Verdict
from rvnd.seal_binding import replay
from rvnd.mcp_serving import clear_request_principal, set_request_principal
from rvnd.parties import register_party

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def _ingest(folder: Path, name: str, text: str, lr: Path) -> None:
    (folder / "Inbox").mkdir(parents=True, exist_ok=True)
    f = folder / "Inbox" / name
    f.write_text(text)
    cli.main(["--log-root", str(lr), "ingest", str(f), "--folder", str(folder)])


def test_cross_workspace_read_gates_and_records(tmp_path):
    lr = tmp_path / "log"
    src1, src2, target = tmp_path / "law", tmp_path / "docs", tmp_path / "companion"
    _ingest(src1, "act.txt", "High-risk AI systems must allow human oversight.", lr)
    _ingest(src2, "deploy.txt", "We deploy an automated screening agent.", lr)

    res = cross_workspace_read(target, [src1, src2], role=ROLE_SOURCE,
                          autonomy_grade="L2", log_root=lr)

    assert len(res["links"]) == 2
    for link in res["links"]:
        # personal-data footprint, L2, no standing approval → flagged → CONDITIONAL
        assert link["verdict"] == Verdict.CONDITIONAL.value
        assert link["pair_ids"], "source pairs should be assembled"
        assert link["audit_id"], "the crossing should be recorded on the target chain"

    recorded = [e for e in replay(str(target), log_root=lr)
                if (e.extra or {}).get("kind") == "cross-workspace-read"]
    assert len(recorded) == 2


def test_under_grade_is_refused(tmp_path):
    lr = tmp_path / "log"
    src, target = tmp_path / "law", tmp_path / "companion"
    _ingest(src, "act.txt", "text", lr)

    # L1 is below the personal-data minimum grade (2) → NO-GO, no read.
    res = cross_workspace_read(target, [src], autonomy_grade="L1", log_root=lr)
    link = res["links"][0]
    assert link["verdict"] == Verdict.NO_GO.value
    assert link["pair_ids"] == []
    assert link["audit_id"] is None


def test_standing_approval_makes_it_go(tmp_path):
    lr = tmp_path / "log"
    src, target = tmp_path / "law", tmp_path / "companion"
    _ingest(src, "act.txt", "text", lr)

    sa = StandingApproval(agent="companion", action_class="cross-workspace-read",
                          obligation_pair="pair:demo")
    res = cross_workspace_read(target, [src], autonomy_grade="L2",
                          standing_approvals=[sa], role=ROLE_COMPANION, log_root=lr)
    link = res["links"][0]
    assert link["verdict"] == Verdict.GO.value
    assert link["audit_id"]


def test_request_principal_requires_membership_in_each_source(tmp_path):
    lr = tmp_path / "log"
    allowed, foreign, target = (tmp_path / "allowed", tmp_path / "foreign",
                                tmp_path / "target")
    _ingest(allowed, "allowed.txt", "allowed", lr)
    _ingest(foreign, "foreign.txt", "foreign", lr)
    register_party(str(allowed), party_id="alice\x40example.test", kind="human",
                   competences=[], actor="operator", log_root=str(lr))

    set_request_principal("alice\x40example.test", "alice\x40example.test")
    try:
        res = cross_workspace_read(
            target, [allowed, foreign], autonomy_grade="L2", log_root=lr)
    finally:
        clear_request_principal()

    assert res["links"][0]["pair_ids"]
    denied = res["links"][1]
    assert denied["verdict"] == Verdict.NO_GO.value
    assert denied["pair_ids"] == [] and denied["audit_id"] is None
    assert "active membership" in denied["error"]
