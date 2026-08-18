# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P0b Option B — one run_id across the queue and workflow ledgers.

A queued run used to wear two disconnected ``wfrun:`` ids: the queue minted one
at enqueue, and ``run_workflow`` minted its own at execution. So the effect
ledger (queue ``effect-observed``) and the authorisation ledger (workflow
``gate-verdict`` / ``workflow-event``) could not be tied to one run by identity —
an auditor had to correlate by folder + workflow + time.

B threads the queue's ``entry.run_id`` into ``run_workflow``, so the whole
lifecycle — enqueue → gated steps → finalise — shares one id. It is a
traceability change, not a coverage one: the per-step reconciliation already
worked within the workflow namespace.
"""
from __future__ import annotations

import pytest

from workspaces.mutation_log import MutationLog
from workspaces.queue import enqueue_run
from workspaces.worker import WorkerConfig, run_once
from workspaces.workflows import (
    Workflow,
    WorkflowStep,
    define_workflow,
    run_workflow,
)


@pytest.fixture(autouse=True)
def _keys(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")


def _wf(name, *skills):
    return Workflow(name=name, steps=[WorkflowStep(skill_id=s) for s in skills])


def _run_ids_by_kind(fc, log) -> dict:
    """The distinct run_ids seen on the chain, per reconcilable event kind."""
    out: dict = {}
    for e in MutationLog(str(fc), log_root=log).replay():
        k = (e.extra or {}).get("kind")
        if k in ("gate-verdict", "workflow-event", "effect-observed"):
            out.setdefault(k, set()).add((e.extra or {}).get("run_id"))
    return out


def test_run_workflow_honours_a_supplied_run_id(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("intake", "p:a"), log_root=log)
    out = run_workflow(str(fc), "intake", run_id="wfrun:EXTERNAL",
                       dispatcher=lambda **kw: {"ok": True}, log_root=log)
    assert out["run_id"] == "wfrun:EXTERNAL"
    ids = _run_ids_by_kind(fc, log)
    assert ids["gate-verdict"] == {"wfrun:EXTERNAL"}
    assert ids["workflow-event"] == {"wfrun:EXTERNAL"}


def test_run_workflow_mints_its_own_id_when_none_given(tmp_path):
    # Backward compatible: a direct (non-queued) caller still gets a fresh id.
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("intake", "p:a"), log_root=log)
    out = run_workflow(str(fc), "intake",
                       dispatcher=lambda **kw: {"ok": True}, log_root=log)
    assert out["run_id"].startswith("wfrun:")


def test_queued_run_shares_one_run_id_end_to_end(tmp_path):
    # B's payoff: through the real worker path, the authorisation ledger
    # (gate-verdict), the step effects (workflow-event) and the queue witness
    # (effect-observed) all carry the SAME id — the queue's — so a run is one
    # traceable thing, not two.
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("intake", "p:a"), log_root=log)
    entry = enqueue_run(str(fc), "intake", log_root=log)

    out = run_once(WorkerConfig(worker_id="w1", log_root=log))
    assert out["state"] == "done"

    ids = _run_ids_by_kind(fc, log)
    assert ids["gate-verdict"] == {entry.run_id}
    assert ids["workflow-event"] == {entry.run_id}
    assert ids["effect-observed"] == {entry.run_id}
