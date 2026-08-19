# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Bind the SHIPPING governance-map panel to the governance_map/v1 contract.

The renderer of record is ``renderMapContract`` in ``app/src/index.html``: the
map drawer (``app/src/panels/map.js``) and the governance chat
(``app/src/shell/chat.js``) both call it, deliberately sharing one helper
instead of forking a second copy. An earlier standalone module,
``app/src/governance_map_view.mjs``, was superseded by that path and removed
(2026-08-19) — nothing imported it, nothing served it, and no stylesheet
carried its ``gm-*`` classes; only this file still referenced it, which is why
these tests used to point at a renderer no user could reach.

What each test is for:

* the version pin catches a contract version bump the panel was not told about;
* the field-set pin freezes every shape ``serve()`` returns, so a field can no
  longer be added or removed under an unchanged ``governance_map/v1``;
* the subset guard proves the panel can only read fields the contract actually
  provides (the cross-language half of the same drift question).

HONEST RESIDUAL — the removed module was also EXECUTED here, in node, against
a synthetic payload, with a negative assertion that ``assertContract`` throws
on a wrong version. ``renderMapContract`` is an inline function in the shell
document, not an importable module, so it cannot be driven the same way
without extracting it. Its positive execution coverage is not lost: the live
gate ``app/panels/governance_map_render.mjs`` drives paste → map → ask through
the real op and asserts the rendered result (including that the version guard
did NOT fire on a live payload). What IS lost is the negative unit — nothing
now asserts the guard fires on a wrong version. Naming that is the point; a
gap silently assumed covered is how the last several were born.
"""
from __future__ import annotations

import re
from pathlib import Path

from rvnd import governance_map as GM

_SHELL = (Path(__file__).resolve().parents[2] / "app" / "src" / "index.html")


def _render_map_contract() -> str:
    """The body of the shipping renderer, sliced out of the shell document."""
    src = _SHELL.read_text(encoding="utf-8")
    i = src.index("function renderMapContract(")
    j = src.index("\n}\n", i)
    return src[i:j]


def test_panel_version_pins_to_contract():
    body = _render_map_contract()
    m = re.search(r"p\.version\s*!==\s*'([^']+)'", body)
    assert m, ("renderMapContract must version-guard the payload it renders "
               "(p.version !== '<contract version>')")
    assert m.group(1) == GM.SCHEMA_VERSION, (
        f"panel {m.group(1)!r} != contract {GM.SCHEMA_VERSION!r} — the two drifted")


def test_panel_is_the_one_shared_renderer():
    """Both consumers call the shared helper — no second, unpinned copy."""
    app = _SHELL.parent
    for consumer in (app / "panels" / "map.js", app / "shell" / "chat.js"):
        assert "renderMapContract(" in consumer.read_text(encoding="utf-8"), (
            f"{consumer.name} no longer calls renderMapContract — if it forked "
            "its own renderer, this pin no longer covers what ships")


# The version pin only catches a version STRING change. But a field can be added or removed
# while the version stays "governance_map/v1" — silently drifting the shape the panel renders.
# The field-set test pins every contract shape and asserts the fields the JS panel actually reads
# are a SUBSET of what the contract provides. Changing a set below is a deliberate act: update it,
# check the panel renderer (renderMapContract in app/src/index.html), and decide whether the
# version must bump.
_TOP = {"version", "grouped_by", "sorted_by", "summary", "facets", "groups", "view"}
_SUMMARY = {"total", "empty", "interpreter", "prohibited", "furnished", "may_apply", "instruments"}
_GROUP = {"group", "rules"}
_GNODE = {"key", "count", "empty", "interpreter", "prohibited", "furnished", "worst_status", "rule_ids"}
_RULE = {"rule_id", "pinpoint", "instrument", "role", "duty", "operator", "risk_tier", "risk_floor",
         "room", "step", "areas", "status", "coverage", "currency", "needs_interpreter", "demand_type",
         "cta", "overlay", "carried", "secondary", "artifacts", "resolution", "confidence",
         "gate_id", "enforcement", "verdict", "allowed_agents", "source"}
# what renderMapContract actually reads — kept small and explicit; if the panel starts
# reading a new field, add it here and the subset assertion guarantees the contract has it.
_PANEL_READS_RULE = {"pinpoint", "role", "risk_tier", "cta"}
_PANEL_READS_GNODE = {"key", "count", "empty", "interpreter", "prohibited"}
_PANEL_READS_TOP = {"version", "grouped_by", "summary", "groups", "view"}

_PROVISIONS = [
    {"pinpoint": "Art. 16", "text": "Providers of high-risk AI systems shall ensure that "
     "their systems undergo the relevant conformity assessment procedure."},
    {"pinpoint": "Art. 26", "text": "Deployers of high-risk AI systems shall take "
     "appropriate technical and organisational measures to use them per the instructions."},
]


def test_panel_pins_the_field_set():
    p = GM.serve({"group_by": "role", "sort": "gaps"},
                 provisions=_PROVISIONS, instrument="AI Act")
    assert p["version"] == GM.SCHEMA_VERSION and p["groups"]
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
    # the rule CTA is rendered as `r.cta.label`, so the shape matters, not just the key
    assert "label" in g["rules"][0]["cta"], "rule cta lost its label — the panel renders cta.label"


def test_question_path_carries_what_the_panel_echoes():
    """The ask box echoes `question` + `view.filters`; only the question path
    emits `question`, so it is pinned against that payload, not the plain one."""
    p = GM.serve(provisions=_PROVISIONS, instrument="AI Act",
                 question="which rules need a human?")
    assert p["question"], "question path must echo the run query the panel prints"
    assert isinstance(p.get("view", {}).get("filters", {}), dict), (
        "panel prints view.filters as the inferred filter — it must be a mapping")


def test_the_superseded_module_stays_gone():
    """Guards the removal: a re-added parallel renderer would be unpinned and
    unreachable, exactly the state this file was cleaning up."""
    assert not (_SHELL.parent / "governance_map_view.mjs").exists(), (
        "app/src/governance_map_view.mjs is back — nothing imports or serves it; "
        "the shipping renderer is renderMapContract in app/src/index.html")
