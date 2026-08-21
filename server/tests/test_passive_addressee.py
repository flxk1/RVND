# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Agentless-passive addressee detection (rule_extractor).

A passive operative sentence with no named agent ("X shall be established")
makes the grammatical subject the PATIENT, not the legal addressee. We do not
guess the addressee — we flag it (addressee_resolved=False) and shave
confidence, so downstream routes the addressee question to the residual.
Closes finding (b) of the three-doc test.
"""
from rvnd.rule_extractor import extract_rules, _is_agentless_passive


def _one(text):
    rs = extract_rules(text)
    assert rs, f"no rule extracted from: {text!r}"
    return rs[0]


def test_active_subject_is_resolved():
    r = _one("Deployers shall assign human oversight to natural persons.")
    assert r.addressee_resolved is True
    assert "deployer" in r.subject


def test_agentless_passive_is_flagged_unresolved():
    r = _one("High-risk AI systems shall be designed and developed in such a "
             "way that they can be effectively overseen by natural persons.")
    # The trailing "by natural persons" belongs to "overseen", not "designed" —
    # must NOT count as the main verb's agent.
    assert r.addressee_resolved is False


def test_named_agent_resolves_passive():
    # Agent named right after the passive verb → resolved.
    assert _is_agentless_passive("shall", "be established by the provider", "en") is False


def test_agentless_resolves_false():
    assert _is_agentless_passive("shall", "be established and maintained", "en") is True


def test_unknown_language_defaults_resolved():
    # No passive table for the language → never over-flags.
    assert _is_agentless_passive("shall", "be established", "xx") is False


def test_passive_lowers_confidence():
    passive = _one("High-risk AI systems shall be designed and developed "
                   "appropriately.")
    active = _one("Providers shall establish a risk management system.")
    assert passive.confidence <= active.confidence


def test_field_defaults_true_for_plain_rules():
    r = _one("Providers shall establish a risk management system.")
    assert r.addressee_resolved is True
    # serialises
    assert r.to_dict()["addressee_resolved"] is True
