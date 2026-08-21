# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The one oversight chokepoint: gate × matrix × oversight × privacy → one
decision, on the signed chain."""

from __future__ import annotations

from rvnd import governance as gov
from rvnd import policy_matrix as pm


def test_routine_low_reach_permits_and_audits(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    d = gov.decide_action(folder, action_class="dispatch:x", grade="L1",
                          log_root=tmp_path / "log")
    assert d["verdict"] == "permit" and d["light"] == "go"
    assert d["audit_id"]                       # recorded on the signed chain
    assert gov.permits(d)


def test_matrix_override_denies(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    m = pm.recommended_default(); pm.set_cell(m, "L1", "approve", "block")
    pm.save_own_matrix(str(folder), m)          # this workspace paints L1×approve red
    d = gov.decide_action(folder, action_class="dispatch:x", grade="L1",
                          log_root=tmp_path / "log")
    assert d["verdict"] == "deny" and gov.permits(d) is False


def test_matrix_ask_holds(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    m = pm.recommended_default(); pm.set_cell(m, "L1", "approve", "ask")
    pm.save_own_matrix(str(folder), m)
    d = gov.decide_action(folder, action_class="dispatch:x", grade="L1",
                          log_root=tmp_path / "log")
    assert d["verdict"] == "hold"               # push to the human, wait


def test_privacy_floor_lifts_to_hold(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    # default oversight=approve; regulated data floors the live row to supervised,
    # where the default grid is ask → hold (even though the gate said go)
    d = gov.decide_action(folder, action_class="egress", grade="L1",
                          privacy_class="regulated", log_root=tmp_path / "log")
    assert d["oversight_level"] == "approve"
    assert d["verdict"] == "hold"
    assert "regulated" in d["reason"]


# --- output routing: grounder responds to the normal oversight modes ---

def test_grounded_output_permits(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    d = gov.decide_output(folder, grounded=True, log_root=tmp_path / "log")
    assert d["verdict"] == "permit" and d["flagged"] is False


def test_ungrounded_stops_in_hitl_band(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    for lvl in ("approve", "supervised", "manual"):
        d = gov.decide_output(folder, grounded=False, oversight_level=lvl,
                              log_root=tmp_path / "log")
        assert d["verdict"] == "hold" and d["flagged"] is True and d["band"] == "HITL"


def test_ungrounded_keeps_running_flagged_in_hotl_hic(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    for lvl, band in (("review", "HOTL"), ("notify", "HOTL"), ("autonomous", "HIC")):
        d = gov.decide_output(folder, grounded=False, oversight_level=lvl,
                              log_root=tmp_path / "log")
        assert d["verdict"] == "permit" and d["flagged"] is True and d["band"] == band


def test_ground_routes_through_oversight(tmp_path):
    from rvnd.workspace_grounder import ground
    folder = str(tmp_path / "workspace")
    # no citation → ungrounded → default oversight (approve) stops the agent
    r0 = ground(folder, "the sky is green", [], log_root=tmp_path / "log")
    assert r0["oversight"]["grounded"] is False and r0["oversight"]["verdict"] == "hold"
    # with a cited source → grounded → permit
    r1 = ground(folder, "EU AI Act entered into force 2024",
                [{"title": "Regulation 2024/1689", "url": "https://eur-lex.europa.eu/x"}],
                log_root=tmp_path / "log")
    assert r1["oversight"]["grounded"] is True and r1["oversight"]["verdict"] == "permit"


def test_footprint_gives_the_chokepoint_teeth(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    # bare L1 action → permit; the SAME action with a high-risk footprint
    # (external-publish needs L3) at L1 must NOT permit — the gate tightens it
    bare = gov.decide_action(folder, action_class="dispatch:x", grade="L1",
                             log_root=tmp_path / "log")
    teeth = gov.decide_action(folder, action_class="publish", grade="L1",
                              footprint=("external-publish",), log_root=tmp_path / "log")
    assert bare["verdict"] == "permit"
    assert teeth["verdict"] in ("hold", "deny") and not gov.permits(teeth)


def test_lock_decision_lands_on_signed_chain(tmp_path):
    from rvnd.lock.gate_and_capture import record_lock_decision_to_chain
    from rvnd.mutation_log import MutationLog

    class D:                       # a lock GateDecision-shaped stub
        action = "refuse"; reason = "PII found"
    folder = tmp_path / "workspace"; folder.mkdir()
    aid = record_lock_decision_to_chain(str(folder), D(), model="claude",
                                        request_id="r1", log_root=tmp_path / "log")
    assert aid
    evs = [e for e in MutationLog(folder, log_root=tmp_path / "log").replay()
           if (e.extra or {}).get("kind") == "lock-decision"]
    assert evs and evs[-1].extra["action"] == "refuse"
    assert evs[-1].extra["verdict"] == "deny"   # lock action mapped to canonical


def test_decision_shape(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    d = gov.decide_action(folder, action_class="a", log_root=tmp_path / "log")
    assert set(d) >= {"verdict", "light", "oversight_level", "grade",
                      "gate_verdict", "reason", "audit_id", "action_class"}
    assert d["verdict"] in ("permit", "hold", "deny")
