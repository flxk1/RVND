# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Autonomy ladder aligned to ISO/IEC 22989:2022 §5.13 (level of automation 0–6).

The ladder itself is owned by governance's grammar; these pin the two guarantees
that matter on RVND's enforcement gate after the re-scale:

* **no widening** — no footprint's clearing set grew; high-stakes footprints
  require L4 (high), so L3 "standby-conditional" no longer clears them;
* **the self-governing ceiling (L6) is always refused** — the taxonomy names it,
  the gate forbids it.
"""
from __future__ import annotations

from rvnd.action_gate import ActionRequest, Verdict, gate
from rvnd.adapters.policy_languages import grade_levels


def _verdict(footprint, grade):
    fp = (footprint,) if footprint else ()
    parties = ("acme",) if footprint == "external-publish" else ()
    return gate(ActionRequest(agent="a", action_class="dispatch:x",
                              autonomy_grade=grade, footprint=fp,
                              folder="/f", affected_parties=parties)).verdict


def test_ladder_is_iso_0_to_6():
    assert grade_levels() == ("L0", "L1", "L2", "L3", "L4", "L5", "L6")


def test_high_stakes_clear_at_standby_L3_as_conditional():
    # financial / irreversible / external-publish require L3 "standby-conditional"
    # — a human on standby — where they proceed only WITH sign-off (CONDITIONAL),
    # never below it. Bar indices are unchanged from the old 0–4 ladder, so this
    # is exactly today's behaviour (non-loosening), now concept-named.
    for tag in ("financial", "irreversible", "external-publish"):
        assert _verdict(tag, "L2") is Verdict.NO_GO, tag        # below standby → refused
        assert _verdict(tag, "L3") is not Verdict.NO_GO, tag    # standby clears (sign-off)
        assert _verdict(tag, "L5") is not Verdict.NO_GO, tag
    assert _verdict("financial", "L3") is Verdict.CONDITIONAL   # standby = sign-off, not auto


def test_pii_and_security_clear_at_partial_L2_and_standby_L3():
    for tag in ("personal-data", "security-control"):
        assert _verdict(tag, "L1") is Verdict.NO_GO, tag        # below partial
        assert _verdict(tag, "L2") is not Verdict.NO_GO, tag    # partial clears
        assert _verdict(tag, "L3") is not Verdict.NO_GO, tag    # standby clears too


def test_self_governing_L6_is_always_refused():
    # ISO level 6 (self-governing) is categorically never permitted — even for a
    # benign action, and even though index 6 would otherwise clear every bar.
    assert _verdict("", "L6") is Verdict.NO_GO
    assert _verdict("personal-data", "L6") is Verdict.NO_GO
    assert _verdict("financial", "L6") is Verdict.NO_GO
    d = gate(ActionRequest(agent="a", action_class="dispatch:x",
                           autonomy_grade="L6", footprint=(), folder="/f"))
    assert d.verdict is Verdict.NO_GO
    assert "self-governing" in d.reason
