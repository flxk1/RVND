# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Jurisdiction-pack schema + composition.

Written before the logic. The TASKS verification line: TWO packs (EU base +
DE overlay, shipped as data) COMPOSE, with a monotonicity test — adding a
pack never loosens. Composition is the control-form algebra (`compose_all`),
nothing pack-specific: a pack maps footprint tags to named control forms,
and the composed stack feeds `effective_control_form(required_forms=...)`.
Closed vocabulary at load (an invented form name is a load error, like
NT-14), no compliance claims in the data.
"""
from __future__ import annotations


import pytest

from workspaces import policy_matrix as pm
from workspaces.controlforms import leq


def _load(name):
    from workspaces.juris_packs import load_reference_pack
    return load_reference_pack(name)


# --- the shipped reference packs load + validate ------------------------------

def test_reference_packs_load():
    eu, de = _load("eu-base"), _load("de-overlay")
    assert eu["pack_id"] == "eu-base" and de["pack_id"] == "de-overlay"
    assert de["extends"] == "eu-base"
    assert eu["controls"] and de["controls"]


def test_pack_with_unknown_form_refused():
    from workspaces.juris_packs import load_pack
    with pytest.raises(ValueError):
        load_pack({"pack_id": "x", "version": "1", "jurisdiction": "XX",
                   "controls": {"personal-data": "notarized"}})


@pytest.mark.parametrize("missing", ["pack_id", "version", "jurisdiction",
                                     "controls"])
def test_pack_missing_required_field_refused(missing):
    from workspaces.juris_packs import load_pack
    raw = {"pack_id": "x", "version": "1", "jurisdiction": "XX",
           "controls": {"personal-data": "notify"}}
    raw.pop(missing)
    with pytest.raises(ValueError):
        load_pack(raw)


def test_bad_effective_from_refused():
    from workspaces.juris_packs import load_pack
    with pytest.raises(ValueError):
        load_pack({"pack_id": "x", "version": "1", "jurisdiction": "XX",
                   "effective_from": "soon", "controls": {}})


# --- composition: strictest wins, monotone, order-free ------------------------

def test_two_packs_compose_monotone():
    """THE verification line: for every governed tag, the composed form is at
    least as strict as what EACH pack demands alone."""
    from workspaces.juris_packs import compose_packs
    eu, de = _load("eu-base"), _load("de-overlay")
    composed = compose_packs([eu, de])
    for tag in set(eu["controls"]) | set(de["controls"]):
        assert tag in composed
        for pack in (eu, de):
            if tag in pack["controls"]:
                assert leq(pack["controls"][tag], composed[tag]), (
                    f"{tag}: composing loosened {pack['pack_id']}")


def test_overlay_never_removes_a_base_guarantee():
    """Adding the DE overlay on top of EU alone only ever ADDS guarantees."""
    from workspaces.juris_packs import compose_packs
    eu, de = _load("eu-base"), _load("de-overlay")
    alone = compose_packs([eu])
    stacked = compose_packs([eu, de])
    for tag, g in alone.items():
        assert g <= stacked[tag]


def test_composition_order_free_and_idempotent():
    from workspaces.juris_packs import compose_packs
    eu, de = _load("eu-base"), _load("de-overlay")
    assert compose_packs([eu, de]) == compose_packs([de, eu])
    assert compose_packs([eu, eu]) == compose_packs([eu])


def test_empty_stack_governs_nothing():
    from workspaces.juris_packs import compose_packs, required_forms
    assert compose_packs([]) == {}
    assert required_forms([], ["external-publish"]) == []


# --- the stack feeds the matrix wiring ----------------------------------------

def test_required_forms_for_a_footprint():
    from workspaces.juris_packs import required_forms
    eu, de = _load("eu-base"), _load("de-overlay")
    forms = required_forms([eu, de], ["personal-data", "external-publish"])
    assert forms, "governed tags must yield forms"
    for f in forms:
        assert isinstance(f, frozenset)


def test_pack_stack_through_effective_control_form():
    """End to end: a 'go' cell + the pack stack = the packs' guarantees —
    the painted policy is never loosened, only tightened, by pack data."""
    from workspaces.juris_packs import required_forms
    eu, de = _load("eu-base"), _load("de-overlay")
    m = pm.recommended_default()
    r = pm.effective_control_form(
        m, grade="L0", oversight="autonomous",
        required_forms=required_forms([eu, de], ["personal-data"]))
    assert r["light"] == "go"                      # the grid is untouched
    for pack in (eu, de):
        if "personal-data" in pack["controls"]:
            assert leq(pack["controls"]["personal-data"],
                       frozenset(r["guarantees"]))


def test_ungoverned_footprint_stays_auto():
    from workspaces.juris_packs import required_forms
    eu = _load("eu-base")
    m = pm.recommended_default()
    r = pm.effective_control_form(
        m, grade="L0", oversight="autonomous",
        required_forms=required_forms([eu], ["benign-read"]))
    assert r["control_form"] == "auto"
