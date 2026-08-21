# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Precision regressions for the rule extractor — the rough edges the RULE_ND
doc named, now fixed and locked."""

from __future__ import annotations

from rvnd.rule_extractor import extract_rules


def _one(text):
    rules = extract_rules(text, gated_by_fingerprint=False)
    assert rules, f"no rule extracted from: {text}"
    return rules[0]


def test_full_subject_phrase_is_captured_not_truncated():
    r = _one("The provider of a high-risk AI system shall establish a risk management system.")
    assert r.subject == "the provider of a high-risk ai system"   # not "of a high-risk ai system"
    assert r.modal == "obligation"


def test_unless_exception_is_split_from_the_action():
    r = _one("The provider shall establish a risk management system, unless the system "
             "is used solely for scientific research.")
    assert r.action == "establish a risk management system"        # action no longer swallows it
    assert "scientific research" in r.exception
    assert r.modal == "obligation"


def test_where_condition_is_split_from_the_action():
    r = _one("The controller shall notify the supervisory authority where feasible.")
    assert r.action == "notify the supervisory authority"
    assert r.condition == "feasible"


def test_german_regressions_still_hold():
    # the earlier-fixed German cases must remain correct
    assert _one("Der Anbieter ist verpflichtet, die Daten offenzulegen.").modal == "obligation"
    assert _one("Der Anbieter darf die Daten nicht offenlegen.").modal == "prohibition"
