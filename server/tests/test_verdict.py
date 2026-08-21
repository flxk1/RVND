# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The shared tri-state: one Permit/Hold/Deny, three surface vocabularies."""

from __future__ import annotations

import pytest

from rvnd import verdict as v
from rvnd.verdict import Verdict


def test_three_vocabularies_collapse_to_one_tristate():
    # the redundancy, now proven to be one thing under three names
    assert v.from_gate("GO") is v.from_light("go") is v.from_admission("admit") is Verdict.PERMIT
    assert v.from_gate("CONDITIONAL") is v.from_light("ask") is v.from_admission("hold") is Verdict.HOLD
    assert v.from_gate("NO-GO") is v.from_light("block") is v.from_admission("reject") is Verdict.DENY


def test_strictest_wins():
    assert v.strictest(Verdict.PERMIT, Verdict.HOLD) is Verdict.HOLD
    assert v.strictest(Verdict.HOLD, Verdict.DENY) is Verdict.DENY
    assert v.strictest(Verdict.PERMIT, Verdict.PERMIT) is Verdict.PERMIT
    assert v.strictest(Verdict.DENY, Verdict.PERMIT, Verdict.HOLD) is Verdict.DENY


def test_strictest_empty_is_permit():
    assert v.strictest() is Verdict.PERMIT
    assert v.strictest_of([]) is Verdict.PERMIT


@pytest.mark.parametrize("word", ["go", "ask", "block"])
def test_light_roundtrip(word):
    assert v.to_light(v.from_light(word)) == word


@pytest.mark.parametrize("word", ["GO", "CONDITIONAL", "NO-GO"])
def test_gate_roundtrip(word):
    assert v.to_gate(v.from_gate(word)) == word


@pytest.mark.parametrize("word", ["admit", "hold", "reject"])
def test_admission_roundtrip(word):
    assert v.to_admission(v.from_admission(word)) == word


def test_from_lock_maps_to_tristate():
    assert v.from_lock("allow") is Verdict.PERMIT
    assert v.from_lock("minimise") is Verdict.PERMIT
    assert v.from_lock("ask_user") is Verdict.HOLD
    assert v.from_lock("refuse") is Verdict.DENY
    assert v.from_lock("???") is Verdict.HOLD          # unknown → hold


def test_admission_unknown_defaults_to_hold():
    # default-deny on the learning stream: an unknown class waits
    assert v.from_admission("mystery") is Verdict.HOLD


def test_policy_matrix_uses_the_shared_rule():
    # the matrix's public words are unchanged, but its ordering/compose now
    # derive from the shared module (one source of truth)
    from rvnd import policy_matrix as pm
    assert pm.LIGHTS == ("go", "ask", "block")
    assert pm.stricter("go", "block") == "block"
    assert pm.stricter("ask", "go") == "ask"
    assert pm._VERDICT_LIGHT == {"GO": "go", "CONDITIONAL": "ask", "NO-GO": "block"}
