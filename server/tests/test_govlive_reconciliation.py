# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P0b — the reconciliation panel on the governance_live board.

The complete-mediation summary (authorisation ledger vs effect ledger) is a
read-only projection over the chain, so it belongs on the live board too, not
only in the evidence pack. These pin that the board carries it and that an
unauthorised effect shows up in the at-a-glance summary — without the board
mutating anything (its load-bearing doctrine).
"""
from __future__ import annotations

import pytest

from rvnd.governance_live import governance_live
from rvnd.mutation_log import MutationLog


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))


def test_board_reconciles_a_real_run(tmp_path):
    fc = tmp_path / "ws"; fc.mkdir()
    log = tmp_path / "log"
    from rvnd.workflows import (
        Workflow, WorkflowStep, define_workflow, run_workflow,
    )
    define_workflow(str(fc), Workflow(name="intake", description="t",
                    steps=[WorkflowStep(skill_id="p:a")]), log_root=log)
    assert run_workflow(str(fc), "intake",
                        dispatcher=lambda **kw: {"ok": True}, log_root=log)["ok"]

    board = governance_live(str(fc), log_root=str(log))
    rec = board["reconciliation"]
    assert rec["matched"] >= 1
    assert rec["unauthorised_rate"] == 0.0
    assert rec["observed_not_authorised"] == 0          # a count on the board
    assert board["summary"]["unauthorised_effects"] == 0


def test_board_summary_flags_an_unauthorised_effect(tmp_path):
    fc = tmp_path / "ws"; fc.mkdir()
    log = tmp_path / "log"
    # A step outcome on the chain with no gate-verdict behind it.
    from rvnd.workflows import _log_workflow_event
    _log_workflow_event(str(fc), run_id="ghost", workflow="wf", step_index=0,
                        state="done", skill_id="p:x", log_root=log)

    board = governance_live(str(fc), log_root=str(log))
    assert board["reconciliation"]["status"] == "diverged"
    assert board["reconciliation"]["observed_not_authorised"] == 1
    assert board["summary"]["unauthorised_effects"] == 1


def test_reconciliation_panel_mutates_nothing(tmp_path):
    # The board's doctrine: a read never changes the chain. The reconciliation
    # panel must honour it too.
    fc = tmp_path / "ws"; fc.mkdir()
    log = tmp_path / "log"
    from rvnd.workflows import _log_workflow_event
    _log_workflow_event(str(fc), run_id="r", workflow="wf", step_index=0,
                        state="done", skill_id="p:a", log_root=log)
    before = MutationLog(str(fc), log_root=log).count()
    governance_live(str(fc), log_root=str(log))
    governance_live(str(fc), log_root=str(log))
    assert MutationLog(str(fc), log_root=log).count() == before
