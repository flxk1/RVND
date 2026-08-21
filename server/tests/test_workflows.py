# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for workflows + runner + HOTL live view."""

from __future__ import annotations


import pytest

from rvnd.workflows import (
    Workflow,
    WorkflowStep,
    active_workflows,
    define_workflow,
    delete_workflow,
    list_workflows,
    list_workflows_for_folder,
    load_workflow,
    recent_dispatches,
    run_workflow,
)
from rvnd.mutation_log import MutationLog


def _wf(name, *step_skills):
    return Workflow(name=name,
                    description=f"test workflow {name}",
                    steps=[WorkflowStep(skill_id=s) for s in step_skills])


def test_define_load_roundtrip(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    path = define_workflow(str(fc), _wf("intake", "p:a", "p:b"),
                            log_root=log)
    assert path.exists()
    loaded = load_workflow(str(fc), "intake", log_root=log)
    assert loaded is not None
    assert loaded.name == "intake"
    assert [s.skill_id for s in loaded.steps] == ["p:a", "p:b"]


def test_define_workflow_validates_name(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    # Empty name
    with pytest.raises(ValueError):
        define_workflow(str(fc), _wf("", "p:a"), log_root=log)
    # Path traversal attempt
    with pytest.raises(ValueError):
        define_workflow(str(fc), _wf("../evil", "p:a"), log_root=log)
    # Leading dot
    with pytest.raises(ValueError):
        define_workflow(str(fc), _wf(".hidden", "p:a"), log_root=log)


def test_define_workflow_validates_steps(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    # No steps
    with pytest.raises(ValueError):
        define_workflow(str(fc), _wf("empty"), log_root=log)
    # Empty skill_id
    with pytest.raises(ValueError):
        define_workflow(str(fc), Workflow(name="bad", steps=[WorkflowStep(skill_id="")]),
                         log_root=log)
    # Bad on_failure
    with pytest.raises(ValueError):
        define_workflow(str(fc), Workflow(name="bad",
                                            steps=[WorkflowStep(skill_id="p:a", on_failure="lol")]),
                         log_root=log)


def test_list_workflows_for_folder(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("a", "p:s"), log_root=log)
    define_workflow(str(fc), _wf("b", "p:s"), log_root=log)
    wfs = list_workflows_for_folder(str(fc), log_root=log)
    assert sorted(w.name for w in wfs) == ["a", "b"]


def test_list_workflows_asymmetric(tmp_path):
    parent = tmp_path / "p"; child = parent / "c"
    for d in (parent, child): d.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "log"
    define_workflow(str(parent), _wf("from-parent", "p:a"), log_root=log)
    define_workflow(str(child),  _wf("from-child",  "p:b"), log_root=log)

    # Child sees both
    out = list_workflows(str(child), log_root=log)
    names = [w["name"] for w in out["workflows"]]
    assert "from-parent" in names
    assert "from-child"  in names

    # Parent only sees its own
    out2 = list_workflows(str(parent), log_root=log)
    names2 = [w["name"] for w in out2["workflows"]]
    assert "from-parent" in names2
    assert "from-child"  not in names2  # descendant must NOT leak up


def test_delete_workflow(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("a", "p:s"), log_root=log)
    assert delete_workflow(str(fc), "a", log_root=log) is True
    assert delete_workflow(str(fc), "a", log_root=log) is False  # gone
    assert load_workflow(str(fc), "a", log_root=log) is None


def test_run_workflow_records_events(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("intake", "p:a", "p:b"), log_root=log)

    def fake_dispatch(folder_context, skill_id, query):
        return {"ok": True, "skill_id": skill_id}

    out = run_workflow(str(fc), "intake",
                        dispatcher=fake_dispatch, log_root=log)
    assert out["ok"] is True
    assert out["final_state"] == "done"
    assert [s["skill_id"] for s in out["steps"]] == ["p:a", "p:b"]
    assert all(s["state"] == "done" for s in out["steps"])

    # Mutation log should have 1 run-start + 2 step-running + 2 step-done +
    # 1 run-done = 6 workflow-event entries
    events = [e for e in MutationLog(str(fc), log_root=log).replay()
              if e.pair_id == "workflow-event"]
    assert len(events) == 6


def test_run_workflow_stop_on_failure(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    wf = Workflow(name="halt", steps=[
        WorkflowStep(skill_id="p:a", on_failure="stop"),
        WorkflowStep(skill_id="p:b"),
    ])
    define_workflow(str(fc), wf, log_root=log)

    def failing_dispatch(folder_context, skill_id, query):
        return {"ok": False, "error": "boom"}

    out = run_workflow(str(fc), "halt",
                        dispatcher=failing_dispatch, log_root=log)
    assert out["ok"] is False
    assert out["final_state"] == "failed"
    # Only the first step ran
    assert len(out["steps"]) == 1
    assert out["steps"][0]["state"] == "failed"
    assert "boom" in out["steps"][0]["error"]


def test_run_workflow_continue_on_failure(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    wf = Workflow(name="resilient", steps=[
        WorkflowStep(skill_id="p:a", on_failure="continue"),
        WorkflowStep(skill_id="p:b", on_failure="stop"),
    ])
    define_workflow(str(fc), wf, log_root=log)

    calls = []
    def mixed_dispatch(folder_context, skill_id, query):
        calls.append(skill_id)
        if skill_id == "p:a":
            return {"ok": False, "error": "transient"}
        return {"ok": True}

    out = run_workflow(str(fc), "resilient",
                        dispatcher=mixed_dispatch, log_root=log)
    assert calls == ["p:a", "p:b"]
    # Second step succeeded, run is "done" because no remaining failure
    # was encountered after p:b succeeded.
    assert out["steps"][0]["state"] == "failed"
    assert out["steps"][1]["state"] == "done"


def test_run_workflow_inherited_from_ancestor(tmp_path):
    parent = tmp_path / "p"; child = parent / "c"
    for d in (parent, child): d.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "log"
    define_workflow(str(parent), _wf("shared", "p:a"), log_root=log)

    def fake(folder_context, skill_id, query):
        return {"ok": True}

    # Running from child should resolve the workflow from parent
    out = run_workflow(str(child), "shared",
                        dispatcher=fake, log_root=log)
    assert out["ok"] is True
    assert out["workflow"] == "shared"


def test_run_workflow_missing_raises(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    with pytest.raises(FileNotFoundError):
        run_workflow(str(fc), "ghost", log_root=log)


def test_recent_dispatches_includes_workflow_events(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    define_workflow(str(fc), _wf("flow", "p:a"), log_root=log)
    run_workflow(str(fc), "flow",
                  dispatcher=lambda **kw: {"ok": True}, log_root=log)

    out = recent_dispatches(str(fc), log_root=log)
    kinds = {e["kind"] for e in out}
    assert "workflow-event" in kinds
    # Newest first
    timestamps = [e["timestamp"] for e in out]
    assert timestamps == sorted(timestamps, reverse=True)


def test_step_validation_rejects_bad_on_failure(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    wf = Workflow(name="bad", steps=[WorkflowStep(skill_id="p:s", on_failure="wat")])
    with pytest.raises(ValueError):
        define_workflow(str(fc), wf, log_root=log)


def test_step_retry_without_retries_count_rejected(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    wf = Workflow(name="bad", steps=[
        WorkflowStep(skill_id="p:s", on_failure="retry", retries=0),
    ])
    with pytest.raises(ValueError) as ei:
        define_workflow(str(fc), wf, log_root=log)
    assert "requires retries" in str(ei.value)


def test_retry_succeeds_on_second_attempt(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    wf = Workflow(name="flaky", steps=[
        WorkflowStep(skill_id="p:flaky", on_failure="retry",
                     retries=2, backoff_ms=0),
    ])
    define_workflow(str(fc), wf, log_root=log)

    calls = {"n": 0}
    def transient(folder_context, skill_id, query):
        calls["n"] += 1
        if calls["n"] < 2:
            return {"ok": False, "error": "503"}
        return {"ok": True}

    out = run_workflow(str(fc), "flaky",
                        dispatcher=transient, log_root=log)
    assert out["ok"] is True
    assert out["steps"][0]["state"] == "done"
    assert out["steps"][0]["attempts"] == 2
    assert calls["n"] == 2


def test_retry_exhausted_marks_run_failed(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    wf = Workflow(name="permabroken", steps=[
        WorkflowStep(skill_id="p:dead", on_failure="retry",
                     retries=3, backoff_ms=0),
    ])
    define_workflow(str(fc), wf, log_root=log)

    calls = {"n": 0}
    def always_fail(folder_context, skill_id, query):
        calls["n"] += 1
        return {"ok": False, "error": "still-broken"}

    out = run_workflow(str(fc), "permabroken",
                        dispatcher=always_fail, log_root=log)
    assert out["ok"] is False
    assert out["final_state"] == "failed"
    # 1 initial + 3 retries = 4 attempts
    assert calls["n"] == 4
    assert out["steps"][0]["attempts"] == 4


def test_retry_records_step_retry_events(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    wf = Workflow(name="flaky2", steps=[
        WorkflowStep(skill_id="p:flaky", on_failure="retry",
                     retries=1, backoff_ms=0),
    ])
    define_workflow(str(fc), wf, log_root=log)

    n = {"i": 0}
    def trans(folder_context, skill_id, query):
        n["i"] += 1
        return {"ok": n["i"] >= 2, "error": "x" if n["i"] < 2 else ""}

    run_workflow(str(fc), "flaky2", dispatcher=trans, log_root=log)
    events = [e for e in MutationLog(str(fc), log_root=log).replay()
              if e.pair_id == "workflow-event"]
    retry_events = [e for e in events
                    if (e.extra or {}).get("state") == "step-retry"]
    assert len(retry_events) == 1


def test_template_substitution_unknown_step(tmp_path):
    from rvnd.workflows import _substitute_step_refs
    # Out-of-range index is replaced with an error literal — fails loudly.
    s = _substitute_step_refs("answer: ${steps[5].output}", [])
    assert "[unresolved: step 5 out of range]" in s


def test_template_substitution_known_step(tmp_path):
    from rvnd.workflows import _substitute_step_refs
    prior = [{
        "skill_id": "p:s",
        "body":     "BODY-TEXT",
        "output":   {"ok": True, "skill_id": "p:s", "body": "BODY-TEXT"},
        "error":    "",
    }]
    out = _substitute_step_refs(
        "skill=${steps[0].skill_id} body=${steps[0].body} err=${steps[0].error}",
        prior,
    )
    assert "skill=p:s" in out
    assert "body=BODY-TEXT" in out
    assert "err=" in out  # empty error → empty string


def test_run_workflow_threads_step_output(tmp_path):
    """Step 2 receives step 1's output via ${steps[0].body} substitution."""
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    wf = Workflow(name="threaded", steps=[
        WorkflowStep(skill_id="p:first", query="initial"),
        WorkflowStep(skill_id="p:second",
                     query="refine: ${steps[0].body}"),
    ])
    define_workflow(str(fc), wf, log_root=log)

    seen_queries = []
    def thread_aware(folder_context, skill_id, query):
        seen_queries.append((skill_id, query))
        # First step returns a fake body that step 2 will reference
        if skill_id == "p:first":
            return {"ok": True, "skill_id": skill_id, "body": "DRAFT-FROM-FIRST"}
        return {"ok": True, "skill_id": skill_id}

    out = run_workflow(str(fc), "threaded",
                        dispatcher=thread_aware, log_root=log)
    assert out["ok"] is True
    # Step 2's query had the placeholder resolved
    assert seen_queries[0] == ("p:first", "initial")
    assert seen_queries[1][0] == "p:second"
    assert "DRAFT-FROM-FIRST" in seen_queries[1][1]


def test_run_workflow_threading_with_failed_prior_step(tmp_path):
    """If a continue-on-failure step fails, later substitutions of its
    output produce empty body but the error field is populated."""
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    wf = Workflow(name="resilient-thread", steps=[
        WorkflowStep(skill_id="p:flaky", on_failure="continue"),
        WorkflowStep(skill_id="p:recover",
                     query="err was: ${steps[0].error}; body was: ${steps[0].body}"),
    ])
    define_workflow(str(fc), wf, log_root=log)

    seen = []
    def mixed(folder_context, skill_id, query):
        seen.append((skill_id, query))
        if skill_id == "p:flaky":
            return {"ok": False, "error": "transient-503"}
        return {"ok": True}

    out = run_workflow(str(fc), "resilient-thread",
                        dispatcher=mixed, log_root=log)
    # Step 2 ran with the resolved template
    assert any(q[0] == "p:recover" and "transient-503" in q[1] for q in seen)
    assert out["steps"][1]["state"] == "done"


def test_recent_dispatches_scope_self_excludes_child_events(tmp_path):
    """In self-scope, the parent folder must NOT see the child's events."""
    parent = tmp_path / "p"; child = parent / "c"
    for d in (parent, child): d.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "log"
    define_workflow(str(child), _wf("child-flow", "p:a"), log_root=log)
    run_workflow(str(child), "child-flow",
                  dispatcher=lambda **kw: {"ok": True}, log_root=log)

    parent_events = recent_dispatches(str(parent), log_root=log)
    # Parent's own log is empty; child-flow events live in child's log.
    assert parent_events == []


def test_recent_dispatches_scope_recursive_aggregates(tmp_path):
    """In recursive scope, parent sees its descendant folders' events too."""
    parent = tmp_path / "p"; child = parent / "c"
    for d in (parent, child): d.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "log"
    define_workflow(str(child), _wf("child-flow", "p:a"), log_root=log)
    run_workflow(str(child), "child-flow",
                  dispatcher=lambda **kw: {"ok": True}, log_root=log)
    # Also a dispatch on parent itself
    from rvnd.pinned_skills import record_dispatch
    record_dispatch(str(parent), "p:parent-skill", log_root=log)

    parent_recursive = recent_dispatches(str(parent), scope="recursive",
                                          log_root=log)
    origins = {e.get("folder_origin", "") for e in parent_recursive}
    # Both the parent's own event and at least one descendant event surfaced
    assert str(parent.resolve()) in origins
    assert str(child.resolve()) in origins


def test_recent_dispatches_rejects_bad_scope(tmp_path):
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    with pytest.raises(ValueError):
        recent_dispatches(str(fc), scope="ancestors", log_root=log)


def test_active_workflows_surfaces_unfinished(tmp_path):
    """If we record a run-start without a terminal state, active_workflows
    surfaces it. Simulate by ginning up workflow events directly."""
    fc = tmp_path / "wks"; fc.mkdir()
    log = tmp_path / "log"
    from rvnd.workflows import _log_workflow_event
    _log_workflow_event(str(fc), run_id="wfrun:abc", workflow="ghost",
                         step_index=-1, state="running",
                         log_root=log)
    active = active_workflows(str(fc), log_root=log)
    assert any(a["run_id"] == "wfrun:abc" for a in active)

    # Now record terminal — should disappear from active
    _log_workflow_event(str(fc), run_id="wfrun:abc", workflow="ghost",
                         step_index=-1, state="done",
                         log_root=log)
    active = active_workflows(str(fc), log_root=log)
    assert not any(a["run_id"] == "wfrun:abc" for a in active)
