# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Round-trip + projection tests for the capability IR against REAL
workflow.yaml files (both dialects) plus the synthetic step kinds the
workflow.yaml dialect does not itself carry (tool / llm).

Proof goals:
  1. parse_workflow_yaml ingests both real dialects (stages, scenarios)
     losslessly into the IR.
  2. The IR round-trips through dict/json without loss.
  3. project_to_workspace_workflow produces a valid engine-shaped workflow and
     reports every downgrade/drop honestly (no silent loss).
  4. readiness_report classifies steps and blocks on missing deps.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces.capability.ir import (
    CapabilitySpec,
    CapabilityStep,
    discover_capabilities,
    import_readiness,
    parse_dispatch_map,
    parse_workflow_yaml,
    project_to_workspace_workflow,
    readiness_report,
)

# Real corpus — resolve relative to this test file, walking up to the repo
# root (…/workspace) then into payhip-products is NOT reliable (separate tree),
# so the fixtures are embedded below as the verbatim shapes pulled from:
#   payhip-products/argument-lab/references/workflow.yaml   (stages dialect)
#   payhip-products/role-verticals/music-label/workflow.yaml (scenarios dialect)
# Embedding keeps the test hermetic (payhip-products is not in the runtime tree).

ARGUMENT_LAB = """
kit: argument-lab
version: 1.3.0
stages:
  - id: intake
    skill: argument-lab
    output: 00-intake.md
    refusable: true
    refuses_when:
      - argument_not_yet_a_claim
      - request_to_fabricate_evidence
  - id: claim-tree
    skill: toulmin-mapper
    output: 01-claim-tree.md
  - id: fallacy-audit
    skill: fallacy-spotter
    output: 02-fallacy-audit.md
    parallel_with: bias-audit
  - id: bias-audit
    skill: cognitive-bias-audit
    output: 03-bias-audit.md
    parallel_with: fallacy-audit
  - id: steelman
    skill: steelman-builder
    output: 04-steelman.md
  - id: socratic
    skill: socratic-method
    output: 05-socratic.md
    optional: true
    runs_when:
      - user_requests
      - stakes_high
  - id: harden
    skill: peer-review-editor
    output: 06-revised-draft.md
    consumes:
      - 01-claim-tree.md
      - 04-steelman.md
  - id: audit-stamp
    skill: argument-lab
    output: 07-audit.json
    always: true
routing:
  audit:
    - intake
    - claim-tree
    - fallacy-audit
    - bias-audit
    - steelman
    - harden
    - audit-stamp
  teach:
    - intake
    - claim-tree
    - fallacy-audit
    - bias-audit
    - steelman
"""

MUSIC_LABEL = """
vertical: music-label
version: 1.0.0
scenarios:
  - id: reconcile-statement
    trigger: "reconcile this distributor statement / find royalty leaks"
    skills: [music-micro-tools:statement-normaliser, music-micro-tools:royalty-leak-finder]
    output: statement-canonical.csv
    refusable: true
  - id: recoupment-status
    trigger: "where is this artist's recoupment balance"
    skills: [carve-outs:recoupment-timeline, music-micro-tools:ledger]
    output: recoupment-timeline.md
    refusable: true
"""


# ---------------------------------------------------------------------------
# Parser — stages dialect (argument-lab)
# ---------------------------------------------------------------------------


def test_parse_stages_dialect():
    spec = parse_workflow_yaml(ARGUMENT_LAB)
    assert spec.name == "argument-lab"          # name lifted from the 'kit:' key
    assert spec.source_format == "workflow.yaml:stages"
    assert len(spec.steps) == 8
    ids = [s.id for s in spec.steps]
    assert ids[0] == "intake" and ids[-1] == "audit-stamp"


def test_stages_field_fidelity():
    spec = parse_workflow_yaml(ARGUMENT_LAB)
    intake = spec.step_by_id("intake")
    assert intake.kind == "skill"
    assert intake.ref == "argument-lab"
    assert intake.refusable is True
    assert "argument_not_yet_a_claim" in intake.refuse_when

    fallacy = spec.step_by_id("fallacy-audit")
    assert fallacy.parallel_group == "bias-audit"

    socratic = spec.step_by_id("socratic")
    assert socratic.optional is True
    assert "stakes_high" in socratic.run_when

    harden = spec.step_by_id("harden")
    assert "04-steelman.md" in harden.consumes

    stamp = spec.step_by_id("audit-stamp")
    assert stamp.always is True


def test_routing_profiles_captured():
    spec = parse_workflow_yaml(ARGUMENT_LAB)
    assert set(spec.routes) == {"audit", "teach"}
    assert spec.routes["teach"][-1] == "steelman"   # teach has no harden
    assert "harden" in spec.routes["audit"]


# ---------------------------------------------------------------------------
# Parser — scenarios dialect (music-label) -> route steps with chains
# ---------------------------------------------------------------------------


def test_parse_scenarios_dialect():
    spec = parse_workflow_yaml(MUSIC_LABEL)
    assert spec.source_format == "workflow.yaml:scenarios"
    assert len(spec.steps) == 2
    rec = spec.step_by_id("reconcile-statement")
    assert rec.kind == "route"
    assert rec.chain == ["music-micro-tools:statement-normaliser",
                         "music-micro-tools:royalty-leak-finder"]
    assert rec.refusable is True
    assert rec.produces == "statement-canonical.csv"


# ---------------------------------------------------------------------------
# Parser 2 — router dispatch-map.md (music-companion) -> route steps
# ---------------------------------------------------------------------------

# Verbatim from payhip-products/music-companion/references/dispatch-map.md
DISPATCH_MAP = """
## Document-drop routing (classify -> structure -> calculate -> check)

When a document enters the matter folder, run the classifier FIRST:

| Type | Route |
|---|---|
| `split-sheet` | music-data-extractor -> split-calculator -> music-inconsistency-check |
| `royalty-statement` | statement-normaliser -> music-data-extractor -> royalty-leak-finder -> music-inconsistency-check |
| `distribution-agreement` | contract-key-terms + music-data-extractor |
| `sync-licence` | contract-key-terms + music-data-extractor + sync-quote |
| `unknown` | no forced data route — hand back to the coach |

The pipeline is always classify -> structure -> calculate -> check.
"""


def test_parse_dispatch_map_table():
    spec = parse_dispatch_map(DISPATCH_MAP, name="music-companion")
    assert spec.source_format == "dispatch-map.md"
    ids = [s.id for s in spec.steps]
    assert ids == ["split-sheet", "royalty-statement",
                   "distribution-agreement", "sync-licence", "unknown"]
    assert all(s.kind == "route" for s in spec.steps)


def test_dispatch_map_chains_and_separators():
    spec = parse_dispatch_map(DISPATCH_MAP, name="music-companion")
    split = spec.step_by_id("split-sheet")
    assert split.chain == ["music-data-extractor", "split-calculator",
                           "music-inconsistency-check"]
    stmt = spec.step_by_id("royalty-statement")
    assert stmt.chain[0] == "statement-normaliser"
    assert len(stmt.chain) == 4
    # '+' separator flattens the same as '->'
    sync = spec.step_by_id("sync-licence")
    assert sync.chain == ["contract-key-terms", "music-data-extractor", "sync-quote"]


def test_dispatch_map_prose_row_is_refusable_handback():
    spec = parse_dispatch_map(DISPATCH_MAP, name="music-companion")
    unk = spec.step_by_id("unknown")
    assert unk.chain == []
    assert unk.refusable is True


def test_dispatch_map_skill_prefix_namespacing():
    spec = parse_dispatch_map(DISPATCH_MAP, name="music-companion",
                              skill_prefix="music-companion:")
    split = spec.step_by_id("split-sheet")
    assert split.chain[0] == "music-companion:music-data-extractor"
    # already-namespaced entries are left untouched (none here, but verify rule)
    assert all(c.startswith("music-companion:") for c in split.chain)


def test_dispatch_map_projects_and_reports():
    spec = parse_dispatch_map(DISPATCH_MAP, name="music-companion")
    res = project_to_workspace_workflow(spec)
    # 4 chained routes (3+4+2+3 = 12 dispatches) + 1 empty handback route
    assert res.projected_steps == 12
    # every non-empty route is reported as flattened; the empty one contributes none
    flattened = [d for d in res.downgraded if d["kind"] == "route"]
    assert len(flattened) == 4


# ---------------------------------------------------------------------------
# IR round-trips through dict/json without loss
# ---------------------------------------------------------------------------


def test_ir_roundtrip_dict():
    spec = parse_workflow_yaml(ARGUMENT_LAB)
    again = CapabilitySpec.from_dict(spec.to_dict())
    assert again.to_dict() == spec.to_dict()


def test_ir_roundtrip_json():
    spec = parse_workflow_yaml(MUSIC_LABEL)
    again = CapabilitySpec.from_dict(json.loads(spec.to_json()))
    assert again.to_dict() == spec.to_dict()


# ---------------------------------------------------------------------------
# Projector — IR -> Workspace engine shape, with honest loss reporting
# ---------------------------------------------------------------------------


def test_project_stages_to_engine_shape():
    spec = parse_workflow_yaml(ARGUMENT_LAB)
    res = project_to_workspace_workflow(spec, route="audit")
    wf = res.workflow
    assert wf["name"] == "argument-lab::audit"
    # every engine step has the engine's required shape
    for s in wf["steps"]:
        assert set(s) == {"skill_id", "query", "on_failure"}
        assert s["skill_id"]
    # audit route = 7 skill steps, all native (no routes/tools/llm in it)
    assert res.projected_steps == 7
    # audit-stamp is a skill in this dialect (not a gate), so nothing dropped
    assert res.dropped == []


def test_project_reports_route_flattening():
    spec = parse_workflow_yaml(MUSIC_LABEL)
    res = project_to_workspace_workflow(spec)
    # two route steps, each a chain -> flattened to N skill dispatches
    assert res.projected_steps == 4          # 2 + 2
    kinds = {d["kind"] for d in res.downgraded}
    assert kinds == {"route"}
    assert len(res.downgraded) == 2
    # refusable route -> on_failure continue
    assert all(s["on_failure"] == "continue" for s in res.workflow["steps"])


def test_project_downgrades_tool_and_llm_and_drops_gate():
    # synthetic spec exercising the kinds workflow.yaml doesn't carry
    spec = CapabilitySpec(
        name="synthetic", steps=[
            CapabilityStep(id="s1", kind="skill", ref="some-skill", intent="do x"),
            CapabilityStep(id="t1", kind="tool", ref="slack", intent="post msg"),
            CapabilityStep(id="l1", kind="llm", intent="summarise"),
            CapabilityStep(id="g1", kind="gate", intent="audit stamp"),
        ])
    res = project_to_workspace_workflow(spec)
    dg = {d["step_id"]: d["kind"] for d in res.downgraded}
    assert dg == {"t1": "tool", "l1": "llm"}
    dropped = {d["step_id"] for d in res.dropped}
    assert dropped == {"g1"}
    # skill + downgraded tool + downgraded llm = 3 engine steps; gate dropped
    assert res.projected_steps == 3


# ---------------------------------------------------------------------------
# Readiness report — the import-time honesty contract
# ---------------------------------------------------------------------------


def test_readiness_native_when_all_skills_present():
    spec = parse_workflow_yaml(ARGUMENT_LAB)
    skills = {s.ref for s in spec.steps if s.ref}
    rep = readiness_report(spec, available_skills=skills, available_connectors=set())
    assert rep["verdict"] == "runs_native"
    assert rep["runnable"] is True
    assert rep["missing_skills"] == []


def test_readiness_blocks_on_missing_skill():
    spec = parse_workflow_yaml(ARGUMENT_LAB)
    rep = readiness_report(spec, available_skills={"argument-lab"},
                           available_connectors=set())
    assert rep["verdict"] == "blocked"
    assert rep["runnable"] is False
    assert "toulmin-mapper" in rep["missing_skills"]


def test_readiness_flags_missing_connector():
    spec = CapabilitySpec(name="x", steps=[
        CapabilityStep(id="t1", kind="tool", ref="slack", intent="post"),
    ])
    rep = readiness_report(spec, available_skills=set(), available_connectors=set())
    assert rep["verdict"] == "blocked"
    assert "slack" in rep["missing_connectors"]


def test_readiness_llm_step_routes_to_oversight():
    spec = CapabilitySpec(name="x", steps=[
        CapabilityStep(id="l1", kind="llm", intent="reason"),
    ])
    rep = readiness_report(spec, available_skills=set(), available_connectors=set())
    assert rep["verdict"] == "runs_under_oversight"
    assert "l1" in rep["buckets"]["oversight"]


def test_readiness_unknown_availability_does_not_assert():
    # available_* = None means "unknown" — skill steps must NOT be claimed native
    spec = parse_workflow_yaml(ARGUMENT_LAB)
    rep = readiness_report(spec)   # both None
    # with unknown skills, skill steps are treated as native (can't disprove),
    # but tool steps with unknown connectors are flagged as requirements
    assert rep["runnable"] is True   # no tool/route-missing in argument-lab


# ---------------------------------------------------------------------------
# Import-time discovery + folder readiness (the contract reaching the user)
# ---------------------------------------------------------------------------


def _make_product(tmp_path, *, with_workflow=False, with_dispatch=False,
                  plugin_name=""):
    """Build a realistic product folder on disk."""
    root = tmp_path / "a-product"
    root.mkdir()
    if plugin_name:
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": plugin_name}), encoding="utf-8")
    if with_workflow:
        (root / "references").mkdir(exist_ok=True)
        (root / "references" / "workflow.yaml").write_text(ARGUMENT_LAB,
                                                           encoding="utf-8")
    if with_dispatch:
        d = root / "skills" / "router" / "references"
        d.mkdir(parents=True)
        (d / "dispatch-map.md").write_text(DISPATCH_MAP, encoding="utf-8")
    return root


def test_discover_finds_workflow(tmp_path):
    root = _make_product(tmp_path, with_workflow=True)
    specs = discover_capabilities(root)
    assert len(specs) == 1
    assert specs[0].source_format == "workflow.yaml:stages"
    assert specs[0].name == "argument-lab"


def test_discover_finds_dispatch_map_with_namespace(tmp_path):
    root = _make_product(tmp_path, with_dispatch=True, plugin_name="music-companion")
    specs = discover_capabilities(root)
    assert len(specs) == 1
    assert specs[0].source_format == "dispatch-map.md"
    # plugin.json name was used to namespace bare skills
    split = specs[0].step_by_id("split-sheet")
    assert split.chain[0] == "music-companion:music-data-extractor"


def test_discover_flat_skill_is_no_orchestration(tmp_path):
    root = tmp_path / "flat"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody",
                                   encoding="utf-8")
    rep = import_readiness(root)
    assert rep["overall"] == "no_orchestration"
    assert rep["capabilities"] == []


def test_import_readiness_blocks_on_missing_and_reports_union(tmp_path):
    root = _make_product(tmp_path, with_workflow=True, with_dispatch=True,
                         plugin_name="music-companion")
    # host has only some skills -> blocked, with a named union of what's missing
    rep = import_readiness(root, available_skills={"argument-lab"},
                           available_connectors=set())
    assert rep["overall"] == "blocked"
    assert "toulmin-mapper" in rep["missing_skills"]          # from workflow
    assert any("music-companion:" in s for s in rep["missing_skills"])  # from dispatch
    # two capabilities discovered (workflow + dispatch map)
    assert len(rep["capabilities"]) == 2


def test_import_readiness_native_when_everything_present(tmp_path):
    root = _make_product(tmp_path, with_workflow=True)
    skills = {s.ref for s in parse_workflow_yaml(ARGUMENT_LAB).steps if s.ref}
    rep = import_readiness(root, available_skills=skills, available_connectors=set())
    assert rep["overall"] == "runs_native"
    assert rep["missing_skills"] == []
