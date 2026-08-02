# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Bind the panel to the governance-map contract.

The tests cover version pinning, rendering a real serve() payload, wrong-version
rejection, and field-set compatibility between Python and the JS panel.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from workspaces import governance_map as GM

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

_MJS = (Path(__file__).resolve().parents[2] / "app" / "src" / "governance_map_view.mjs")


def test_panel_version_pins_to_contract():
    src = _MJS.read_text(encoding="utf-8")
    m = re.search(r'SCHEMA_VERSION\s*=\s*"([^"]+)"', src)
    assert m, "panel must declare SCHEMA_VERSION"
    assert m.group(1) == GM.SCHEMA_VERSION, (
        f"panel {m.group(1)!r} != contract {GM.SCHEMA_VERSION!r} — the two drifted")


def test_panel_renders_real_serve_payload():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    # the exact payload the MCP op returns
    payload = GM.serve(
        {"group_by": "role", "sort": "gaps"},
        provisions=[
            {"pinpoint": "Art. 16", "text": "Providers of high-risk AI systems shall ensure that "
             "their systems undergo the relevant conformity assessment procedure."},
            {"pinpoint": "Art. 26", "text": "Deployers of high-risk AI systems shall take "
             "appropriate technical and organisational measures to use them per the instructions."},
        ], instrument="AI Act")
    assert payload["version"] == GM.SCHEMA_VERSION and payload["groups"]

    harness = (
        "import { readFileSync } from 'node:fs';\n"
        "import { pathToFileURL } from 'node:url';\n"
        "const [,, mjs, pj] = process.argv;\n"
        "const mod = await import(pathToFileURL(mjs).href);\n"
        "const payload = JSON.parse(readFileSync(pj, 'utf8'));\n"
        "const html = mod.renderMap(payload);\n"
        "if (!html.includes('gm-panel')) { console.error('no panel'); process.exit(1); }\n"
        "if (!html.includes(payload.groups[0].group.key)) { console.error('group not rendered'); process.exit(1); }\n"
        "let threw = false; try { mod.assertContract({ version: 'nope' }); } catch { threw = true; }\n"
        "if (!threw) { console.error('version guard missing'); process.exit(1); }\n"
        "console.log('OK ' + mod.SCHEMA_VERSION);\n"
    )
    tmp = Path(tempfile.mkdtemp(prefix="gmpanel_"))
    try:
        (tmp / "h.mjs").write_text(harness, encoding="utf-8")
        (tmp / "p.json").write_text(json.dumps(payload), encoding="utf-8")
        r = subprocess.run([node, str(tmp / "h.mjs"), str(_MJS), str(tmp / "p.json")],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, f"panel render failed: {r.stderr or r.stdout}"
        assert r.stdout.strip() == f"OK {GM.SCHEMA_VERSION}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# The version pin only catches a version STRING change. But a field can be added or removed
# while the version stays "governance_map/v1" — silently drifting the shape the panel renders.
# The field-set test pins every contract shape and asserts the fields the JS panel actually reads
# are a SUBSET of what the contract provides. Changing a set below is a deliberate act: update it,
# check the panel renderer (governance_map_view.mjs), and decide whether the version must bump.
_TOP = {"version", "grouped_by", "sorted_by", "summary", "facets", "groups", "view"}
_SUMMARY = {"total", "empty", "interpreter", "prohibited", "furnished", "may_apply", "instruments"}
_GROUP = {"group", "rules"}
_GNODE = {"key", "count", "empty", "interpreter", "prohibited", "furnished", "worst_status", "rule_ids"}
_RULE = {"rule_id", "pinpoint", "instrument", "role", "duty", "operator", "risk_tier", "risk_floor",
         "room", "step", "areas", "status", "coverage", "currency", "needs_interpreter", "demand_type",
         "cta", "overlay", "carried", "secondary", "artifacts", "resolution", "confidence",
         "gate_id", "enforcement", "verdict", "allowed_agents", "source"}
# what governance_map_view.mjs reads (renderMap + bar) — kept small and explicit; if the panel
# starts reading a new field, add it here and the subset assertion guarantees the contract has it.
_PANEL_READS_RULE = {"rule_id", "pinpoint", "instrument", "role", "duty", "risk_tier",
                     "operator", "coverage", "needs_interpreter"}
_PANEL_READS_GNODE = {"key", "count", "empty", "interpreter", "prohibited"}
_PANEL_READS_TOP = {"version", "grouped_by", "summary", "facets", "groups", "view"}


def test_panel_pins_the_field_set():
    p = GM.serve(
        {"group_by": "role", "sort": "gaps"},
        provisions=[
            {"pinpoint": "Art. 16", "text": "Providers of high-risk AI systems shall ensure that "
             "their systems undergo the relevant conformity assessment procedure."},
            {"pinpoint": "Art. 26", "text": "Deployers of high-risk AI systems shall take "
             "appropriate technical and organisational measures to use them per the instructions."},
        ], instrument="AI Act")
    g = p["groups"][0]
    # 1) every contract shape's field set is pinned — an add/remove fails HERE, deliberately
    assert set(p) == _TOP, f"top-level payload keys drifted: {set(p) ^ _TOP}"
    assert set(p["summary"]) == _SUMMARY, f"summary keys drifted: {set(p['summary']) ^ _SUMMARY}"
    assert set(g) == _GROUP, f"group keys drifted: {set(g) ^ _GROUP}"
    assert set(g["group"]) == _GNODE, f"group-node keys drifted: {set(g['group']) ^ _GNODE}"
    assert set(g["rules"][0]) == _RULE, f"rule-row keys drifted: {set(g['rules'][0]) ^ _RULE}"
    # facet axes are the contract's, not a hardcoded list
    assert set(p["facets"]) == set(GM.FACETS)
    # 2) the panel can only read fields the contract PROVIDES (cross-language subset guard)
    assert _PANEL_READS_TOP <= set(p)
    assert _PANEL_READS_GNODE <= set(g["group"])
    assert _PANEL_READS_RULE <= set(g["rules"][0]), (
        f"panel reads rule fields the contract no longer provides: {_PANEL_READS_RULE - set(g['rules'][0])}")
