# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Matrix × control-form wiring.

The matrix keeps its traffic-light grid; `effective_control_form` maps the
EFFECTIVE light into the § 1.5 algebra and composes any additionally required
forms (a pack or step demanding four_eyes / expert_review). Invariants pinned
here, exhaustively over the named vocabulary (small enough that randomization
would be theater):

  * monotone — a required form can only ADD guarantees; the result is never
    looser than the light's own form nor than any required form (in the
    algebra's order, where BLOCK is top);
  * block absorbs from both sides (blocked light + any form; any light +
    block form);
  * incomparable forms compose by conjunction (four_eyes + expert_review).
"""
from __future__ import annotations

import itertools

import pytest

from workspaces import policy_matrix as pm
from workspaces.controlforms import (
    FORMS, from_traffic_light, guarantees, leq, name_of,
)

NAMES = sorted(FORMS)
M = pm.recommended_default()


def _eff(grade="L0", oversight="autonomous", **kw):
    return pm.effective_control_form(M, grade=grade, oversight=oversight, **kw)


# --- light → form mapping (no required forms) -------------------------------

def test_go_maps_to_auto():
    r = _eff("L0", "autonomous")
    assert r["light"] == "go"
    assert r["control_form"] == "auto" and r["guarantees"] == []


def test_ask_maps_to_single_approver():
    r = _eff("L4", "supervised")
    assert r["light"] == "ask"
    assert r["control_form"] == "single_approver"
    assert r["guarantees"] == sorted(FORMS["single_approver"])


def test_block_maps_to_block():
    r = _eff("L4", "manual")
    assert r["light"] == "block" and r["control_form"] == "block"


def test_effective_light_fields_preserved():
    r = _eff("L1", "autonomous", privacy_class="regulated")
    for k in ("light", "painted", "floored_oversight", "gate_light", "reason"):
        assert k in r


# --- the invariant: required forms only tighten, never loosen ---------------

@pytest.mark.parametrize("light,form", list(itertools.product(
    ("go", "ask", "block"), NAMES)))
def test_monotone_over_lights_and_forms(light, form):
    cell = {"go": ("L0", "autonomous"), "ask": ("L4", "supervised"),
            "block": ("L4", "manual")}[light]
    r = _eff(*cell, required_forms=[form])
    got = frozenset(r["guarantees"])
    assert leq(from_traffic_light(light), got)
    assert leq(form, got)


def test_block_light_absorbs_any_form():
    r = _eff("L4", "manual", required_forms=["auto", "four_eyes"])
    assert r["control_form"] == "block"
    assert r["guarantees"] == sorted(FORMS["block"])


def test_block_form_absorbs_go_light():
    r = _eff("L0", "autonomous", required_forms=["block"])
    assert r["control_form"] == "block"


def test_gate_verdict_composes_before_forms():
    # NO-GO forces block; a permissive required form cannot reopen it
    r = _eff("L0", "autonomous", gate_verdict="NO-GO", required_forms=["auto"])
    assert r["light"] == "block" and r["control_form"] == "block"


# --- conjunction on incomparables (the panel's pinned discovery) -------------

def test_incomparable_forms_conjoin():
    r = _eff("L4", "supervised", required_forms=["four_eyes", "expert_review"])
    got = frozenset(r["guarantees"])
    assert leq("four_eyes", got) and leq("expert_review", got)
    assert r["control_form"] == name_of(
        guarantees("four_eyes") | guarantees("expert_review"))


def test_ask_plus_four_eyes_named_four_eyes():
    # single_approver ⊆ four_eyes — the composition lands on the named form
    r = _eff("L4", "supervised", required_forms=["four_eyes"])
    assert r["control_form"] == "four_eyes"


def test_unknown_form_raises():
    with pytest.raises(ValueError):
        _eff("L0", "autonomous", required_forms=["notarized"])


# --- the workspace_matrix facade surfaces the algebra -----------------------------

def test_workspace_matrix_explain_carries_control_form(tmp_path):
    from workspaces import mcp_server
    f = str(tmp_path / "workspace")
    ex = mcp_server.workspace_matrix("explain", {
        "folder_context": f, "grade": "L4", "oversight": "supervised"})
    assert ex["light"] == "ask" and ex["control_form"] == "single_approver"
    ex2 = mcp_server.workspace_matrix("explain", {
        "folder_context": f, "grade": "L4", "oversight": "supervised",
        "forms": ["four_eyes", "expert_review"]})
    assert leq("four_eyes", frozenset(ex2["guarantees"]))
    assert leq("expert_review", frozenset(ex2["guarantees"]))
    bad = mcp_server.workspace_matrix("explain", {
        "folder_context": f, "grade": "L0", "oversight": "autonomous",
        "forms": ["notarized"]})
    assert "error" in bad
