# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Review cards + override-as-event — the human layer's spine.

Every node (lock / analysis / routing / oversight) emits ONE card shape:
what it did, why (explanation + cited sources), its signals, the inputs it
used, and an override affordance. The differentiator: a human override is not
a throwaway edit — it is a signed chain event, it becomes a case the memory
learns from, and a recurring override proposes a rule. No workflow tool does
that; it is what makes every human touch compound.

Claims under test (written BEFORE the logic):
  C1  review_card emits the uniform contract — every card carries
      what/why/citations/signals/inputs/override/status, whatever the stage
  C2  status is derived: a reserved act → 'reserved'; low completeness →
      'needs-review'; otherwise 'auto' (reserved outranks completeness)
  C3  record_override appends a signed event; fail-closed without actor +
      rationale (it is a human correction, attributable)
  C4  an override is retrievable and becomes a correction-case (compounds)
  C5  recurrence: the same (stage, field) override ≥ threshold → 'propose-rule'
  C6  deterministic projection
"""
from __future__ import annotations

import os

import pytest

from workspaces.review_card import (
    overrides_for, recurrence_flags, record_override, review_card,
)

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def test_uniform_card_contract():                                 # C1
    for stage in ("lock", "analysis", "routing", "oversight"):
        card = review_card(node_id=f"{stage}:1", stage=stage,
                           what="did a thing", why="because X",
                           citations=[{"source": "GDPR Art. 28", "tier": 1}],
                           signals={"completeness": "high"},
                           inputs=[{"span": "12-40"}])
        for key in ("node_id", "stage", "what", "why", "citations",
                    "signals", "inputs", "override", "status"):
            assert key in card
        assert card["override"]["editable"] is True


def test_status_is_derived_reserved_outranks_completeness():      # C2
    auto = review_card(node_id="a", stage="analysis", what="", why="",
                       signals={"completeness": "high"})
    assert auto["status"] == "auto"
    low = review_card(node_id="b", stage="analysis", what="", why="",
                      signals={"completeness": "low"})
    assert low["status"] == "needs-review"
    reserved = review_card(node_id="c", stage="oversight", what="", why="",
                           signals={"completeness": "high"},
                           reserved_act={"reserved_to": "counsel",
                                         "act_type": "sign"})
    assert reserved["status"] == "reserved"        # outranks high completeness
    assert reserved["override"]["human_required"] is True


def test_record_override_failclosed(env):                         # C3
    card = review_card(node_id="n1", stage="analysis", what="x", why="y")
    with pytest.raises(ValueError):
        record_override(env["ws"], card=card, actor="", field="conclusion",
                        new_value="z", rationale="r", log_root=env["lr"])
    with pytest.raises(ValueError):
        record_override(env["ws"], card=card, actor="alex",
                        field="conclusion", new_value="z", rationale="",
                        log_root=env["lr"])
    assert overrides_for(env["ws"], log_root=env["lr"]) == []


def test_override_recorded_and_retrievable(env):                  # C4
    card = review_card(node_id="n1", stage="analysis", what="x",
                       why="reused precedent")
    aid = record_override(env["ws"], card=card, actor="alex",
                          field="conclusion", old_value="A", new_value="B",
                          rationale="precedent superseded", log_root=env["lr"])
    ovs = overrides_for(env["ws"], log_root=env["lr"])
    assert len(ovs) == 1
    assert ovs[0]["field"] == "conclusion" and ovs[0]["new_value"] == "B"
    assert ovs[0]["actor"] == "alex" and ovs[0]["receipt"] == aid


def test_recurring_override_proposes_a_rule(env):                 # C5
    for i in range(3):
        c = review_card(node_id=f"n{i}", stage="routing",
                        what="routed to ND-A", why="fingerprint match")
        record_override(env["ws"], card=c, actor="alex", field="route",
                        old_value="ND-A", new_value="ND-B",
                        rationale="A keeps misrouting these", log_root=env["lr"])
    flags = recurrence_flags(env["ws"], threshold=3, log_root=env["lr"])
    assert any(f["kind"] == "propose-rule" and f["stage"] == "routing"
               and f["field"] == "route" for f in flags)


def test_projection_deterministic():                              # C6
    a = review_card(node_id="n", stage="analysis", what="x", why="y",
                    signals={"completeness": "medium"})
    b = review_card(node_id="n", stage="analysis", what="x", why="y",
                    signals={"completeness": "medium"})
    assert a == b
