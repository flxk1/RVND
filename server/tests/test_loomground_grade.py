# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Gate: Rvnd's engine implements Loomground v0.6 autonomy grades.

Self-contained mirror of the published 9 `grade-*` conformance vectors (Rvnd
carries its own engine, so its gate carries its own vectors). A `grade` is
GRANTED on an actor and REQUIRED on a SOURCE gate; §7.1 step-(4) disposition is
`auto` iff G ≥ R, `human` (fail-closed) for an ungraded actor at a graded gate
or G < R, and policy-as-before when the gate declares no required grade. Grade
is checked only at source gates; reservations/refusals/prohibitions keep
precedence. Delegation may not amplify a grade.
"""
from __future__ import annotations

from rvnd import loomground_lang as L


def _tok(kind: str = "act") -> dict:
    return {"id": "t1", "kind": kind, "risk": "low", "party": "deployer", "provenance": []}


def _eval(text: str, transport: dict) -> dict:
    patch = L.parse(text)
    v = L.validate(patch)
    assert v["ok"], v["errors"]
    return L.evaluate(patch, transport)


# ── positive: language-determined dispositions ────────────────────────────────

def test_grade_auto():
    # granted L3 ≥ required L2 → auto → act
    text = ("actor bot grade L3\n"
            "gate decide risk low grade L2 grant bot\n"
            "cord bot -> decide\ncord decide -> master\n")
    tp = {"activations": [{"actor": "bot", "source": "decide", "token": _tok()}]}
    out = _eval(text, tp)
    assert out["decide"]["verdict"] == "auto"
    assert out["decide"]["master"] == "act"


def test_grade_human():
    # granted L1 < required L3 → human → withhold
    text = ("actor bot grade L1\n"
            "gate decide risk low grade L3 grant bot\n"
            "cord bot -> decide\ncord decide -> master\n")
    tp = {"activations": [{"actor": "bot", "source": "decide", "token": _tok()}]}
    out = _eval(text, tp)
    assert out["decide"]["verdict"] == "human"
    assert out["decide"]["master"] == "withhold"


def test_grade_ungraded_at_graded():
    # ungraded actor at a gate requiring L2 → human (fail-closed)
    text = ("actor bot\n"
            "gate decide risk low grade L2 grant bot\n"
            "cord bot -> decide\ncord decide -> master\n")
    tp = {"activations": [{"actor": "bot", "source": "decide", "token": _tok()}]}
    out = _eval(text, tp)
    assert out["decide"]["verdict"] == "human"
    assert out["decide"]["master"] == "withhold"


def test_grade_graded_at_ungraded_is_policy():
    # gate declares no required grade → step-(4) is policy; grade is inert. The
    # granted grade still round-trips onto the actor node. No verdict pinned by the
    # language → engine default own verdict is auto (nothing reserves/prohibits).
    text = ("actor bot grade L1\n"
            "gate draft risk low grant bot\n"
            "cord bot -> draft\ncord draft -> master\n")
    patch = L.parse(text)
    assert L.validate(patch)["ok"]
    proj = L.project(patch)
    bot = next(n for n in proj["nodes"] if n["id"] == "bot")
    draft = next(n for n in proj["nodes"] if n["id"] == "draft")
    assert bot["grade"] == "L1"
    assert "grade_required" not in draft
    tp = {"activations": [{"actor": "bot", "source": "draft", "token": _tok()}]}
    assert L.evaluate(patch, tp)["draft"]["verdict"] == "auto"


def test_grade_reserved_precedence():
    # reservation (step 3) pre-empts the grade comparison (step 4)
    text = ("actor bot grade L1\n"
            "human ombud role ombud\n"
            "gate decide risk low grade L3 grant bot\n"
            "reserve audit by ombud\n"
            "cord bot -> decide\ncord decide -> master\n")
    tp = {"activations": [{"actor": "bot", "source": "decide", "token": _tok("audit")}]}
    out = _eval(text, tp)
    assert out["decide"]["verdict"] == "reserved"
    assert out["decide"]["master"] == "withhold"


def test_grade_join_follows_proposing_actor():
    # screen is a graded SOURCE gate; the comparison follows the PROPOSING actor
    # (bot L2 < L3 → human), NOT the other grantee peer (L4). human pipes to a plain
    # terminal commit and joins strictest-wins → withhold.
    text = ("actor bot grade L2\nactor peer grade L4\n"
            "gate screen risk low grade L3 grant bot peer\n"
            "gate commit risk low grant bot\n"
            "cord bot -> screen\ncord peer -> screen\n"
            "cord screen -> commit\ncord commit -> master\n")
    tp = {"activations": [{"actor": "bot", "source": "screen", "token": _tok()}]}
    out = _eval(text, tp)
    assert out["screen"]["verdict"] == "human"
    assert out["commit"]["verdict"] == "human"
    assert out["commit"]["master"] == "withhold"


# ── negative: ill-formed at apply (validate) ──────────────────────────────────

def test_reject_bad_grade():
    text = ("actor bot grade L9\n"
            "gate decide grade L9 grant bot\n"
            "cord bot -> decide\ncord decide -> master\n")
    patch = L.parse(text)            # parses
    assert not L.validate(patch)["ok"]


def test_reject_delegation_grade_amplify():
    # both actors granted equally at decide, so the risk rule passes and only
    # the grade rule can reject
    text = ("actor boss grade L2\n"
            "actor sub grade L4 on-behalf-of boss\n"
            "gate decide grade L1 grant boss sub\n"
            "cord sub -> decide\ncord decide -> master\n")
    patch = L.parse(text)
    assert not L.validate(patch)["ok"]


def test_reject_delegation_grade_from_nothing():
    # both actors granted equally at decide, so the risk rule passes and only
    # the grade rule can reject
    text = ("actor boss\n"
            "actor sub grade L4 on-behalf-of boss\n"
            "gate decide grade L1 grant boss sub\n"
            "cord sub -> decide\ncord decide -> master\n")
    patch = L.parse(text)
    assert not L.validate(patch)["ok"]


def test_required_grade_on_piped_gate_is_illformed():
    # a required grade may sit only on a SOURCE gate, never on a pipe target (§6)
    text = ("actor bot grade L2\n"
            "gate screen risk low grant bot\n"
            "gate commit risk low grade L2 grant bot\n"
            "cord bot -> screen\ncord screen -> commit\ncord commit -> master\n")
    patch = L.parse(text)
    assert not L.validate(patch)["ok"]


def test_grade_meets_is_the_shared_authority():
    # Option 2: one rule for auto-vs-human, language strings AND app integer ranks.
    assert L.grade_meets("L3", "L2") is True
    assert L.grade_meets("L1", "L3") is False
    assert L.grade_meets("L2", "L2") is True
    # app-layer integer ranks compare the same way (operate uses ints)
    assert L.grade_meets(3, 3) is True
    assert L.grade_meets(2, 3) is False
    # mixed forms compare without coupling
    assert L.grade_meets("L4", 3) is True
    # ungraded actor never meets a real requirement (fail-closed)
    assert L.grade_meets(None, "L0") is False
    assert L.grade_meets(None, 0) is False
    # no requirement is always met (grade inert; policy as before)
    assert L.grade_meets(None, None) is True
    assert L.grade_meets("L0", None) is True
    # malformed inputs fail CLOSED on BOTH sides — never silently grant auto:
    assert L.grade_meets(7, "L4") is False        # out-of-range granted earns nothing
    assert L.grade_meets("L9", "L2") is False      # unrecognised granted string
    assert L.grade_meets("L4", 99) is False        # unrecognised requirement is un-meetable
    assert L.grade_meets("L4", "L9") is False       # ditto, string form
    assert L.grade_meets(True, "L0") is False       # bool is not a grade (int subclass guard)


def test_engine_step4_uses_grade_meets():
    # the engine's source-gate disposition is the same rule, proving the language
    # evaluate() and the app run-path share one authority.
    text = ("actor bot grade L3\n"
            "gate decide risk low grade L3 grant bot\n"
            "cord bot -> decide\ncord decide -> master\n")
    tp = {"activations": [{"actor": "bot", "source": "decide", "token": _tok()}]}
    assert _eval(text, tp)["decide"]["verdict"] == "auto"          # G==R meets


def test_to_netlist_tolerates_int_grade():
    # to_netlist must not crash if a grade arrives as an int (the app layer uses
    # integer ranks). Round-trips back to a parseable, valid netlist.
    patch = {"nodes": [
        {"id": "bot", "class": "actor", "grade": 3},
        {"id": "decide", "class": "gate", "risk_floor": "low", "grade_required": 2},
        {"id": "master", "class": "master"},
    ], "cords": [
        {"from": "bot", "to": "decide", "type": "authority"},
        {"from": "decide", "to": "master", "type": "egress"},
    ], "grants": [{"gate": "decide", "actor": "bot"}]}
    text = L.to_netlist(patch)            # must not raise TypeError
    assert " grade L3" in text and " grade L2" in text   # int rank normalised to the ladder string
    assert L.validate(L.parse(text))["ok"]               # and the round-trip is a VALID netlist


def test_gradeless_graph_unchanged():
    # additive: a graph with no grades evaluates exactly as before
    text = ("actor bot\n"
            "gate draft risk low grant bot\n"
            "cord bot -> draft\ncord draft -> master\n")
    tp = {"activations": [{"actor": "bot", "source": "draft", "token": _tok()}]}
    out = _eval(text, tp)
    assert out["draft"]["verdict"] == "auto"
    assert out["draft"]["master"] == "act"
