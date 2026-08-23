# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the strictest-wins join (``rvnd.hook._meet_decisions``).

Synthetic multi-context governance dicts stand in for what axis-B resolution
will one day produce for real (a decide() result per target workspace). These
tests prove the join composes them correctly today, on synthetic input, ahead
of any real target ever existing: strictest verdict wins (deny > hold >
permit, via ``rvnd.verdict``), the WHOLE winning dict rides through (so its
``reason``/``audit_id`` are attributable to the context that actually
produced them), ties resolve to the first context at the strictest level, and
the singleton case — today's only case in practice — passes its sole dict
through unchanged.
"""
from __future__ import annotations

from rvnd import hook as H


def _gov(light, reason, audit_id):
    return {"light": light, "reason": reason, "audit_id": audit_id}


def test_meet_decisions_empty_list_is_defensive_not_raising():
    assert H._meet_decisions([]) == {}


def test_meet_decisions_singleton_passes_through_verbatim():
    sole = _gov("ask", "needs sign-off", "aud-1")
    assert H._meet_decisions([sole]) is sole


def test_meet_decisions_picks_strictest_block_over_ask_and_go():
    go = _gov("go", "permitted", "aud-go")
    ask = _gov("ask", "needs sign-off", "aud-ask")
    block = _gov("block", "blocked by policy", "aud-block")
    chosen = H._meet_decisions([go, ask, block])
    assert chosen is block
    assert chosen["reason"] == "blocked by policy"
    assert chosen["audit_id"] == "aud-block"


def test_meet_decisions_picks_strictest_ask_over_go():
    go = _gov("go", "permitted", "aud-go")
    ask = _gov("ask", "needs sign-off", "aud-ask")
    chosen = H._meet_decisions([go, ask])
    assert chosen is ask
    assert chosen["reason"] == "needs sign-off"
    assert chosen["audit_id"] == "aud-ask"


def test_meet_decisions_all_go_stays_go():
    a = _gov("go", "permitted A", "aud-a")
    b = _gov("go", "permitted B", "aud-b")
    chosen = H._meet_decisions([a, b])
    assert chosen["light"] == "go"
    assert chosen is a   # first-at-strictest (all tied at "go")


def test_meet_decisions_ties_resolve_to_first_at_strictest():
    first_block = _gov("block", "first block reason", "aud-first")
    second_block = _gov("block", "second block reason", "aud-second")
    ask = _gov("ask", "needs sign-off", "aud-ask")
    chosen = H._meet_decisions([ask, first_block, second_block])
    assert chosen is first_block
    assert chosen["reason"] == "first block reason"
    assert chosen["audit_id"] == "aud-first"


def test_meet_decisions_order_independent_result_is_strictest_regardless_of_position():
    block = _gov("block", "blocked", "aud-block")
    go = _gov("go", "permitted", "aud-go")
    ask = _gov("ask", "needs sign-off", "aud-ask")
    # block first this time — still block, still the same dict identity
    chosen = H._meet_decisions([block, go, ask])
    assert chosen is block
