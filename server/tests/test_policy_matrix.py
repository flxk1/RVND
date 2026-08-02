# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for policy_matrix — the autonomy×oversight grid + safe composition."""

from __future__ import annotations

from workspaces import policy_matrix as pm


def test_default_shape():
    m = pm.recommended_default()
    assert set(m) == set(pm.GRADES)
    for g in pm.GRADES:
        assert set(m[g]) == set(pm.OVERSIGHT)
        for o in pm.OVERSIGHT:
            assert m[g][o] in pm.LIGHTS


def test_anti_diagonal_recommendation():
    m = pm.recommended_default()
    assert m["L0"]["autonomous"] == "go"          # tiny move, never-asks: fine
    assert m["L4"]["autonomous"] == "block"       # full reach, never-asks: danger corner
    assert m["L2"]["approve"] == "ask"            # standard reach, asks-first: show me
    assert all(m[g]["manual"] == "block" for g in pm.GRADES)   # manual = you do it
    assert m["L4"]["notify"] == "ask"


# --- the safety invariant: paint can only tighten ---

def test_paint_cannot_loosen_a_no_go():
    m = pm.recommended_default()
    pm.set_cell(m, "L4", "autonomous", "go")      # user paints the danger cell green
    r = pm.effective_light(m, grade="L4", oversight="autonomous", gate_verdict="NO-GO")
    assert r["light"] == "block"                  # gate NO-GO wins over the green paint
    assert "gate NO-GO" in r["reason"]


def test_regulated_floor_overrides_green_paint():
    m = pm.recommended_default()
    pm.set_cell(m, "L1", "autonomous", "go")      # green at the autonomous row
    r = pm.effective_light(m, grade="L1", oversight="autonomous", privacy_class="regulated")
    # regulated floors oversight up to supervised -> that row is consulted, not autonomous
    assert r["floored_oversight"] == "supervised"
    assert r["light"] == "ask"                    # supervised row is amber, not the painted green
    assert "privacy floor regulated" in r["reason"]


def test_painted_block_beats_go_verdict():
    m = pm.recommended_default()
    pm.set_cell(m, "L0", "autonomous", "block")   # user is stricter than the gate
    r = pm.effective_light(m, grade="L0", oversight="autonomous", gate_verdict="GO")
    assert r["light"] == "block"                  # strictest of (painted block, gate go)


def test_clean_path_stays_go():
    m = pm.recommended_default()
    r = pm.effective_light(m, grade="L0", oversight="autonomous",
                           privacy_class="public", gate_verdict="GO")
    assert r["light"] == "go"


# --- bulk ops (the GUI bulk-click + the CLI set-row/set-col) ---

def test_bulk_set_row_and_col():
    m = pm.recommended_default()
    pm.set_row(m, "autonomous", "block")
    assert all(m[g]["autonomous"] == "block" for g in pm.GRADES)
    pm.set_col(m, "L0", "go")
    assert all(m["L0"][o] == "go" for o in pm.OVERSIGHT)


# --- CLI render + store round-trip ---

def test_render_is_cli_friendly():
    txt = pm.render_matrix_text(pm.recommended_default())
    assert "grade" in txt and "legend" in txt
    assert txt.count("\n") >= len(pm.OVERSIGHT)   # a row per oversight level


def test_save_load_roundtrip(tmp_path):
    m = pm.recommended_default()
    pm.set_cell(m, "L3", "review", "block")
    p = pm.save_matrix(tmp_path / "matrix.json", m)
    m2 = pm.load_matrix(p)
    assert m2["L3"]["review"] == "block"
    assert m2["L0"]["autonomous"] == "go"         # untouched cell preserved


def test_load_missing_returns_default(tmp_path):
    m = pm.load_matrix(tmp_path / "nope.json")
    assert m == pm.recommended_default()


# --- per-step workflow binding round-trips ---

def test_workflow_step_carries_grade_and_oversight():
    from workspaces.workflows import WorkflowStep
    s = WorkflowStep(skill_id="x", autonomy_grade="L3", oversight="notify")
    d = s.to_dict()
    assert d["autonomy_grade"] == "L3" and d["oversight"] == "notify"
    s2 = WorkflowStep.from_dict(d)
    assert s2.autonomy_grade == "L3" and s2.oversight == "notify"
    # default: both inherit (None) — grade from the run's dispatch context,
    # oversight from the folder matrix default
    assert WorkflowStep.from_dict({"skill_id": "y"}).oversight is None
    assert WorkflowStep.from_dict({"skill_id": "y"}).autonomy_grade is None


# --- the workspace_matrix MCP facade (the policy surface the dashboard reads) ---

def test_workspace_matrix_facade(tmp_path):
    from workspaces import mcp_server
    f = str(tmp_path / "workspace")
    assert "workspace_matrix" in mcp_server._DECLARED_TOOLS
    h = mcp_server.workspace_matrix("help")
    assert {"show", "set", "reset", "explain"} <= {o["op"] for o in h["ops"]}
    # a fresh workspace inherits the global default and has no own override
    shown = mcp_server.workspace_matrix("show", {"folder_context": f})
    assert shown["matrix"]["L4"]["autonomous"] == "block"
    assert shown["inherits"] is True and shown["own"] is None
    # set one cell → creates this workspace's override, read it back
    mcp_server.workspace_matrix("set", {"folder_context": f, "grade": "L1",
                                   "oversight": "approve", "light": "block"})
    after = mcp_server.workspace_matrix("show", {"folder_context": f})
    assert after["matrix"]["L1"]["approve"] == "block"
    assert after["inherits"] is False and after["own"] is not None
    # explain shows the gate composing stricter over a painted cell
    ex = mcp_server.workspace_matrix("explain", {"folder_context": f, "grade": "L2",
                                            "oversight": "approve", "verdict": "NO-GO"})
    assert ex["light"] == "block"
    # set_all persists a whole custom grid in one call (the maker's "Customize")
    g = pm.recommended_default(); pm.set_row(g, "autonomous", "block")
    mcp_server.workspace_matrix("set_all", {"folder_context": f, "matrix": g})
    allset = mcp_server.workspace_matrix("show", {"folder_context": f})
    assert all(allset["matrix"][gr]["autonomous"] == "block" for gr in pm.GRADES)
    assert allset["inherits"] is False
    # reset drops the override → inherits the global default again
    mcp_server.workspace_matrix("reset", {"folder_context": f})
    back = mcp_server.workspace_matrix("show", {"folder_context": f})
    assert back["matrix"]["L1"]["approve"] == "go" and back["inherits"] is True


# --- hierarchy: in every workspace, global top-down, override cascade (like the lock) ---

def test_matrix_hierarchy_global_topdown_override(tmp_path, monkeypatch):
    from workspaces import memory as _mem
    root = str(tmp_path / "root")
    child = str(tmp_path / "root" / "sub")
    monkeypatch.setattr(_mem, "discover_ancestors",
                        lambda f, log_root=None: [root] if str(f) == child else [])
    # root sets the global policy: L2×review go -> block
    rm = pm.recommended_default(); pm.set_cell(rm, "L2", "review", "block")
    pm.save_own_matrix(root, rm)
    # a child with no own grid INHERITS root (global top-down)
    assert pm.own_matrix(child) is None
    assert pm.resolve_matrix(child)["L2"]["review"] == "block"
    # child overrides for itself — and an override may LOOSEN the parent (the
    # runtime gate/privacy floors still bind; that is a separate axis)
    cm = pm.resolve_matrix(child)              # start from inherited
    pm.set_cell(cm, "L0", "notify", "ask")     # child-specific change
    pm.set_cell(cm, "L2", "review", "go")       # override the parent's block
    pm.save_own_matrix(child, cm)
    eff = pm.resolve_matrix(child)
    assert eff["L0"]["notify"] == "ask"
    assert eff["L2"]["review"] == "go"          # nearest setting (the child) wins
    # inherited view still shows what's above (root's block)
    assert pm.resolve_inherited(child)["L2"]["review"] == "block"
    # clearing the override returns the child to inheriting
    pm.clear_own_matrix(child)
    assert pm.own_matrix(child) is None
    assert pm.resolve_matrix(child)["L2"]["review"] == "block"
    assert pm.has_matrix_in_chain(child) is True   # root still has one


# --- runner hook: the painted matrix tightens at run time (doing layer) ---

def test_runner_matrix_tightens_a_passing_step(tmp_path):
    """A step the gate would pass is BLOCKED when the folder's matrix paints its
    cell red — the doing layer reads the plan layer, strictest-wins."""
    from workspaces.workflows import Workflow, WorkflowStep, define_workflow, run_workflow
    folder = tmp_path / "workspace"; folder.mkdir()
    log_root = tmp_path / "log"
    define_workflow(str(folder),
                    Workflow(name="w", steps=[WorkflowStep(skill_id="noop",
                                                           autonomy_grade="L1")]),
                    log_root=log_root)
    # this workspace's matrix paints L1 × approve (the default oversight) red
    m = pm.recommended_default()
    pm.set_cell(m, "L1", "approve", "block")
    pm.save_own_matrix(str(folder), m)

    calls = []
    res = run_workflow(
        str(folder), "w", log_root=log_root, autonomy_grade="L1",
        dispatcher=lambda **k: calls.append(k) or {"ok": True, "output": {}, "body": ""})
    assert calls == []                                   # never dispatched
    assert any(s["state"] == "step-blocked" for s in res["steps"])
    assert res["final_state"] == "failed"                # on_failure default = stop


def test_runner_no_matrix_file_is_unchanged(tmp_path):
    """No painted matrix → the step runs exactly as before (opt-in safety)."""
    from workspaces.workflows import Workflow, WorkflowStep, define_workflow, run_workflow
    folder = tmp_path / "workspace"; folder.mkdir()
    log_root = tmp_path / "log"
    define_workflow(str(folder),
                    Workflow(name="w", steps=[WorkflowStep(skill_id="noop",
                                                           autonomy_grade="L1")]),
                    log_root=log_root)
    calls = []
    res = run_workflow(
        str(folder), "w", log_root=log_root, autonomy_grade="L1",
        dispatcher=lambda **k: calls.append(k) or {"ok": True, "output": {}, "body": ""})
    assert len(calls) == 1                                # dispatched normally
    assert res["final_state"] == "done"
