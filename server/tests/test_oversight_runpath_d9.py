# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""D9 — oversight enforced on the run path.

decide_action (the one chokepoint dispatch_skill routes through) gated at the
REQUESTED grade: a quarantined/killed agent ran at its asked grade, and the
composed regulatory ceiling was computed but never consumed — fail-OPEN to less
oversight. Now the requested grade is capped (strictest-wins) by the actor's
kill-switch/quarantine state and by a composed grade_ceiling, and the effective
vs requested grades are surfaced for the UI/audit. Oversight panel."""
from __future__ import annotations

import pytest

from workspaces.governance import decide_action
from workspaces.parties import register_party, set_party_status


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "org"
    f.mkdir()
    return str(f)


# ── negative control: an active agent runs at its requested grade ────────────

def test_active_agent_keeps_requested_grade(folder):
    register_party(folder, "bot7", "agent")
    d = decide_action(folder, action_class="dispatch:x", grade="L4", actor="bot7")
    assert d["grade"] == "L4"            # not capped — proves the cap is conditional
    assert d["requested_grade"] == "L4"
    assert d["breaker_grade"] == ""


# ── kill-switch / quarantine caps to L0 (the fail-open the audit flagged) ─────

@pytest.mark.parametrize("status", ["suspended", "killed"])
def test_frozen_agent_capped_to_l0(folder, status):
    register_party(folder, "bot7", "agent")
    set_party_status(folder, "bot7", status)
    d = decide_action(folder, action_class="dispatch:x", grade="L4", actor="bot7")
    assert d["grade"] == "L0"            # gated at L0, NOT the requested L4
    assert d["requested_grade"] == "L4"
    assert d["breaker_grade"] == "L0"


# ── composed regulatory ceiling is consumed ──────────────────────────────────

def test_grade_ceiling_caps_requested(folder):
    register_party(folder, "bot7", "agent")
    d = decide_action(folder, action_class="dispatch:x", grade="L4",
                      actor="bot7", grade_ceiling="L2")
    assert d["grade"] == "L2"            # capped to the ceiling
    assert d["requested_grade"] == "L4"
    assert d["grade_ceiling"] == "L2"


def test_strictest_of_breaker_and_ceiling_wins(folder):
    # quarantine (L0) is stricter than the ceiling (L2) → L0 wins.
    register_party(folder, "bot7", "agent")
    set_party_status(folder, "bot7", "suspended")
    d = decide_action(folder, action_class="dispatch:x", grade="L4",
                      actor="bot7", grade_ceiling="L2")
    assert d["grade"] == "L0"


def test_unregistered_actor_is_not_breaker_capped(folder):
    # DELIBERATE: the breaker is an AGENT kill-switch. A non-registered actor
    # (the runtime / system caller, e.g. the default mcp:l0 dispatch actor) is
    # governed by the gate + matrix + oversight, NOT blanket-capped to L0 — which
    # would force every system action to max oversight (a DoS) and add no safety.
    d = decide_action(folder, action_class="dispatch:x", grade="L3", actor="mcp:l0")
    assert d["breaker_grade"] == ""     # no breaker cap for a non-agent actor
    assert d["grade"] == "L3"           # the gate/matrix still govern this grade


def test_registry_read_failure_fails_closed_to_l0(folder, monkeypatch):
    # Fail-closed: if the party register can't be read at all, we cannot verify
    # an agent isn't quarantined → cap to L0 rather than trust the asked grade.
    import workspaces.parties as parties
    def _boom(*a, **k):
        raise OSError("party register unreadable")
    monkeypatch.setattr(parties, "list_parties", _boom)
    d = decide_action(folder, action_class="dispatch:x", grade="L4", actor="bot7")
    assert d["grade"] == "L0" and d["breaker_grade"] == "L0"


def test_ceiling_below_request_is_surfaced_for_the_fader(folder):
    # M8 needs requested vs effective vs ceiling to render the fader + law clamp.
    register_party(folder, "bot7", "agent")
    d = decide_action(folder, action_class="dispatch:x", grade="L3",
                      actor="bot7", grade_ceiling="L1")
    assert (d["requested_grade"], d["grade"], d["grade_ceiling"]) == ("L3", "L1", "L1")
