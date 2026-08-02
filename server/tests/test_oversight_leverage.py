# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Oversight leverage, measured — makes the thesis "Workspaces scales
human oversight in a responsible format" falsifiable rather than rhetorical.

Two halves, both measured:
  * LEVERAGE — a human closure on a problem shape governs every future
    instance of that shape (reuse). The reusable asset base is the count of
    distinct shapes closed; redundant closures (the SAME shape closed again)
    are leverage NOT captured — humans repeating themselves.
  * INTEGRITY — the binding risk is automation bias (Bainbridge, Ironies of
    Automation): reuse WITHOUT human re-sampling means the automation is
    unverified and oversight quality may be decaying silently. The sampling
    guard flags this — responsible scaling requires a non-zero sampling rate.

Claims under test (written BEFORE the logic):
  L1  leverage_report: human_closures = edges; shapes_governed = distinct
      fingerprints; redundant_closures = closures - shapes
  L2  a shape closed twice shows redundancy (leverage not yet captured)
  L3  the report declares it measures POTENTIAL leverage, not realised reuse
      (no overclaim — realised reuse needs recall logging)
  L4  sampling guard: reuse>0 with zero sampling → automation-bias flag,
      not responsible
  L5  reuse with an adequate sampling rate → responsible, no flag
  L6  zero reuse → trivially responsible (nothing to sample yet)
  L7  deterministic
"""
from __future__ import annotations

import pytest

from workspaces.oversight_leverage import leverage_report, sampling_adequacy


def _edge(itype, rooms=()):
    return {"solver": f"skill:{itype}-nd", "outcome": "ratified",
            "fingerprint": {"issue_type": itype, "rooms": sorted(rooms),
                            "profile": "legal-de"}, "receipt": "r"}


def test_leverage_counts_closures_and_shapes():                   # L1
    edges = [_edge("liability_cap"), _edge("data_processing"),
             _edge("ip_assignment")]
    rep = leverage_report(edges)
    assert rep["human_closures"] == 3
    assert rep["shapes_governed"] == 3
    assert rep["redundant_closures"] == 0


def test_redundant_closure_is_uncaptured_leverage():              # L2
    edges = [_edge("liability_cap"), _edge("liability_cap"),
             _edge("data_processing")]
    rep = leverage_report(edges)
    assert rep["human_closures"] == 3
    assert rep["shapes_governed"] == 2
    assert rep["redundant_closures"] == 1      # same shape closed twice


def test_report_declares_potential_not_realised():               # L3
    rep = leverage_report([_edge("liability_cap")])
    assert "potential" in rep["basis"].lower()


def test_reuse_without_sampling_is_flagged():                     # L4
    rep = sampling_adequacy(reuse_count=200, sampled_count=0)
    assert rep["responsible"] is False
    assert rep["flag"] == "automation-bias"


def test_reuse_with_adequate_sampling_is_responsible():           # L5
    rep = sampling_adequacy(reuse_count=200, sampled_count=20, floor=0.05)
    assert rep["sampling_rate"] == 0.1
    assert rep["responsible"] is True
    assert rep["flag"] is None


def test_zero_reuse_is_trivially_responsible():                   # L6
    rep = sampling_adequacy(reuse_count=0, sampled_count=0)
    assert rep["responsible"] is True


def test_deterministic():                                         # L7
    edges = [_edge("a"), _edge("b")]
    assert leverage_report(edges) == leverage_report(edges)
    assert sampling_adequacy(10, 1) == sampling_adequacy(10, 1)
