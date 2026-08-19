# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-15: the variety-absorption eval is a gated regression, not just a print.

server/eval/variety/measure_variety_absorption.py operationalises the
Technical Report §1.3 claim — the gate is a variety *attenuator*: it resolves
decidable action-variety mechanically (GO) and routes only the residual to a
human. That eval printed a number nobody checked; an attenuation collapse
(the dial stops moving, or absorption craters) would have gone unnoticed.

This gate runs the SAME deterministic measurement and asserts the structural
properties the claim rests on. Loose by intent — the eval's author states it
catches order-of-magnitude regressions, not micro-drift — so the bounds guard
the claim's shape, not an exact ratio.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "server" / "eval" / "variety"))

import measure_variety_absorption as mva  # noqa: E402
from rvnd.action_gate import StandingApproval, Verdict  # noqa: E402

AGENT = mva.AGENT


def _absorption(max_uses: int, max_total: float):
    stream = mva.build_stream()
    appr = [StandingApproval(AGENT, "pay_invoice", "obligation:ap-policy",
                             max_uses=max_uses, max_total=max_total)]
    total, absorbed, residual, counts = mva.run(stream, appr)
    return {"total": total, "absorbed": absorbed, "residual": residual,
            "counts": counts, "frac": absorbed / total}


def test_widening_the_dial_absorbs_strictly_more():
    """The core §1.3 claim: the human's standing-approval breadth is the
    variety budget — widen it and the machine absorbs more. If BROAD does not
    absorb strictly more than TIGHT, the dial has stopped attenuating."""
    broad = _absorption(100, 10_000.0)
    tight = _absorption(20, 1_000.0)
    assert broad["absorbed"] > tight["absorbed"], (
        f"the dial does not move: BROAD absorbed {broad['absorbed']} "
        f"<= TIGHT absorbed {tight['absorbed']}")


def test_broad_absorption_has_not_collapsed():
    """Order-of-magnitude guard: a wide dial should absorb most routine
    variety. A collapse toward zero means the attenuator broke."""
    broad = _absorption(100, 10_000.0)
    assert broad["frac"] >= 0.5, (
        f"BROAD absorption cratered to {broad['frac']:.2f} (< 0.5) — the "
        "mechanical attenuation is no longer resolving routine variety")


def test_tight_dial_pushes_residual_to_human():
    """The other end of the dial: a tight budget must route real residual to
    a human (absorption well under the broad case), but not refuse everything
    — some routine actions still clear."""
    tight = _absorption(20, 1_000.0)
    assert 0.0 < tight["frac"] <= 0.6, (
        f"TIGHT absorption {tight['frac']:.2f} outside the expected residual "
        "band — the dial no longer concentrates variety on the human")


def test_prohibition_binds_regardless_of_dial():
    """A prohibited action is NO-GO at every dial setting — the attenuator
    never absorbs a prohibited act, however wide the standing approval."""
    for max_uses, max_total in ((100, 10_000.0), (20, 1_000.0)):
        r = _absorption(max_uses, max_total)
        assert r["counts"][Verdict.NO_GO] >= 1, (
            f"no NO-GO verdict at dial ({max_uses}, {max_total}) — the "
            "prohibited action was not refused")
