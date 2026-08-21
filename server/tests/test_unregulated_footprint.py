# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The gate distinguishes an UNREGULATED action — a footprint carrying tags the risk
ontology (`_RISK_MIN_GRADE`) never classified — from a genuinely benign one.

Before this, an unregulated footprint had no recognised risk tag, so it rode the
benign fast path to GO: a policy hole that was both invisible and fail-open (an
unclassified risk permitted as benign). Now it is surfaced with its own code and the
offending tags, so the hole is visible and countable. The verdict is UNCHANGED — this
is measurement, not re-gating.
"""
from __future__ import annotations

from rvnd import action_gate as AG
from rvnd.action_gate import ActionRequest

_gate = getattr(AG, "gate", None) or AG.decide_action


def _decide(grade="L2", **kw):
    return _gate(ActionRequest(agent="a", action_class="act", autonomy_grade=grade, **kw))


def test_unregulated_footprint_is_surfaced_not_laundered_as_benign():
    d = _decide(footprint=("crypto-mining",))          # not in the 5-tag ontology
    assert d.verdict.value == "GO"                     # verdict UNCHANGED — surfaced, not gated
    assert d.audit_triple["reason"] == "unregulated"   # distinct from "benign"
    assert d.audit_triple["unregulated"] == ["crypto-mining"]   # the hole is named
    assert "unregulated footprint" in d.reason


def test_a_genuinely_benign_action_keeps_the_benign_code():
    d = _decide(footprint=())                          # empty footprint = benign
    assert d.verdict.value == "GO"
    assert d.audit_triple["reason"] == "benign"
    assert "unregulated" not in d.audit_triple


def test_a_recognised_risk_tag_is_unaffected():
    d = _decide(footprint=("personal-data",))          # in the ontology → graded path
    assert "unregulated" not in d.audit_triple         # never touched the benign path
