# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Witness escape as a Breaker tripwire input — the "respond" half.

A recorded witness-escape event (``witness_escape.record_witness_escape``)
must trip the SAME flag-tripwire mechanism every other integrity check
already uses (``default_tripwires``, ``oversight_drift.drift_tripwire``): no
new state, no new ordering. That composition gives, for free, everything
this file pins:

  * a recorded escape -> ``Breaker.status()`` QUARANTINED, ``effective_grade()``
    L0, for that actor in that workspace (existing Tripwire/Breaker.status);
  * scoping: an escape in workspace A never quarantines an unrelated actor,
    nor the same actor in an unrelated workspace B (folder-scoped log +
    actor-filtered read);
  * behaviour-preserving: with no witness-escape event, status/effective_grade
    are exactly what they would have been without this module existing;
  * quarantine stays STICKY and clears only via ``Breaker.clear(by, rationale)``
    with a named human — never by a renewal, never by itself.

It also confirms (does not re-implement) two things this file does NOT own:

  * consistency — ``witness_escape.record_witness_escape`` itself causes the
    PUBLIC party-register call ``parties.set_party_status(..., "suspended")``
    as its second, causal step; this file only reads that back (through
    ``parties.list_parties``) to show the Breaker's QUARANTINED verdict and
    the party register's status agree on the same escape. This module does
    not read the register to derive its OWN trip (event-driven, per its
    contract) — it only grounds that the two independently-produced signals
    never disagree.
  * that ``governance.decide_action`` already denies a
    quarantined/suspended/killed actor's consequential action through its
    EXISTING grade-cap path (the party-register kill switch
    ``decide_action``'s ``_actor_grade_cap`` reads) — the same pattern
    ``test_oversight_runpath_d9.py`` uses. This file does not modify
    ``governance.py`` or ``witness_escape.py``.
"""
from __future__ import annotations

import pytest

from rvnd.breaker import (
    Breaker,
    BreakerState,
    Lease,
    WITNESS_ESCAPE_METRIC,
    ensure_witness_escape_armed,
    status_after_witness_escape_check,
    witness_escape_metrics,
    witness_escape_tripwire,
)
from rvnd.governance import decide_action
from rvnd.parties import list_parties, register_party, set_party_status
from rvnd.witness_escape import record_witness_escape


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "org"
    f.mkdir()
    return str(f)


@pytest.fixture
def other_folder(tmp_path, monkeypatch):
    f = tmp_path / "org-b"
    f.mkdir()
    return str(f)


def _lease(agent, grade="L3", expires=100000.0, granted_at=0.0):
    return Lease(agent=agent, granted_grade=grade, expires_at=expires,
                 ttl_seconds=60.0, granted_at=granted_at)


def _party_status(folder_context, party_id):
    """Read the party register's current status for ``party_id`` through the
    PUBLIC ``parties.list_parties`` projection — there is no
    ``get_party_status`` function in ``parties.py``; this composes the same
    public read every other caller of the registry uses. Not a new read
    path: ``parties.py`` is out of this module's declared territory."""
    rows = list_parties(folder_context)["parties"]
    row = next((r for r in rows if r.get("party_id") == party_id), None)
    return row.get("status") if row else None


# ── the tripwire itself reuses the existing flag mechanism ───────────────────

def test_witness_escape_tripwire_is_a_flag_tripwire():
    tw = witness_escape_tripwire()
    assert tw.kind == "flag"
    assert tw.metric == WITNESS_ESCAPE_METRIC
    assert tw.trips(True) is True
    assert tw.trips(False) is False
    assert tw.trips(None) is False        # unmeasured != tripped


def test_ensure_witness_escape_armed_is_idempotent():
    b = Breaker(_lease("bot7"), tripwires=[])
    ensure_witness_escape_armed(b)
    ensure_witness_escape_armed(b)
    matching = [t for t in b.tripwires if t.metric == WITNESS_ESCAPE_METRIC]
    assert len(matching) == 1


# ── a recorded escape -> QUARANTINED -> L0 ────────────────────────────────────

def test_recorded_escape_quarantines_the_actor_to_l0(folder):
    record_witness_escape(folder, ["/etc/passwd"], "bot7")

    b = Breaker(_lease("bot7"), tripwires=[])
    status = status_after_witness_escape_check(b, folder, "bot7", now=50.0)

    assert status.state is BreakerState.QUARANTINED
    assert status.effective_grade == "L0"
    assert b.effective_grade(metrics={}, now=50.0) == "L0"


def test_witness_escape_metrics_reads_true_only_when_recorded(folder):
    assert witness_escape_metrics(folder, "bot7") == {WITNESS_ESCAPE_METRIC: False}
    record_witness_escape(folder, ["/etc/passwd"], "bot7")
    assert witness_escape_metrics(folder, "bot7") == {WITNESS_ESCAPE_METRIC: True}


# ── scoping: A never bleeds into an unrelated actor or workspace ─────────────

def test_escape_does_not_quarantine_an_unrelated_actor_in_the_same_workspace(folder):
    record_witness_escape(folder, ["/etc/passwd"], "bot7")

    innocent = Breaker(_lease("bot-innocent"), tripwires=[])
    status = status_after_witness_escape_check(innocent, folder, "bot-innocent", now=50.0)

    assert status.state is BreakerState.RUNNING
    assert status.effective_grade == "L3"


def test_escape_in_workspace_a_does_not_quarantine_the_same_actor_in_workspace_b(
    folder, other_folder,
):
    record_witness_escape(folder, ["/etc/passwd"], "bot7")

    elsewhere = Breaker(_lease("bot7"), tripwires=[])
    status = status_after_witness_escape_check(elsewhere, other_folder, "bot7", now=50.0)

    assert status.state is BreakerState.RUNNING
    assert status.effective_grade == "L3"


# ── behaviour-preserving: no event, nothing changes ───────────────────────────

def test_no_witness_escape_event_leaves_status_unchanged(folder):
    plain = Breaker(_lease("bot7"), tripwires=[])
    checked = Breaker(_lease("bot7"), tripwires=[])

    plain_status = plain.status(now=50.0)
    checked_status = status_after_witness_escape_check(checked, folder, "bot7", now=50.0)

    assert checked_status.state == plain_status.state == BreakerState.RUNNING
    assert checked_status.effective_grade == plain_status.effective_grade == "L3"


def test_no_witness_escape_event_leaves_effective_grade_and_decide_action_unchanged(folder):
    register_party(folder, "bot7", "agent")
    before = decide_action(folder, action_class="dispatch:x", grade="L4", actor="bot7")

    # Constructing/arming the witness-escape machinery with nothing recorded
    # must not itself change anything the gate sees.
    b = Breaker(_lease("bot7"), tripwires=[])
    status_after_witness_escape_check(b, folder, "bot7", now=50.0)

    after = decide_action(folder, action_class="dispatch:x", grade="L4", actor="bot7")
    assert after["grade"] == before["grade"] == "L4"
    assert after["breaker_grade"] == before["breaker_grade"] == ""
    assert after["verdict"] == before["verdict"]


# ── stickiness: only a named human clear()s it, never a renewal ──────────────

def test_quarantine_is_sticky_and_clears_only_via_named_human(folder):
    record_witness_escape(folder, ["/etc/passwd"], "bot7")
    b = Breaker(_lease("bot7"), tripwires=[])

    s1 = status_after_witness_escape_check(b, folder, "bot7", now=50.0)
    assert s1.state is BreakerState.QUARANTINED

    # A renewal cannot lift it.
    r = b.renew(ok=True, now=60.0)
    assert r.renewed is False

    # Asking again (with the metric now reading clean, e.g. a caller checking
    # only a later window) — still quarantined; the trip is sticky on THIS
    # breaker instance regardless of the current metric read.
    s2 = status_after_witness_escape_check(
        b, folder, "bot7", since=1_000_000.0, now=70.0)
    assert s2.state is BreakerState.QUARANTINED

    # clear() requires a named human and a rationale.
    assert "error" in b.clear(by="", rationale="root cause fixed")
    assert "error" in b.clear(by="alice", rationale="")

    cleared = b.clear(by="alice", rationale="reviewed escape, false positive on a symlink")
    assert cleared["cleared"] is True

    b.renew(ok=True, now=71.0)
    s3 = b.status(now=72.0)
    assert s3.state is BreakerState.RUNNING


def test_self_renewal_never_clears_a_witness_escape_quarantine(folder):
    record_witness_escape(folder, ["/etc/passwd"], "bot7")
    b = Breaker(_lease("bot7"), tripwires=[])
    status_after_witness_escape_check(b, folder, "bot7", now=50.0)

    for _ in range(3):
        r = b.renew(ok=True, now=60.0)
        assert r.renewed is False
        assert "quarantine" in r.reason.lower()
    assert b.status(now=61.0).state is BreakerState.QUARANTINED


# ── consistency (deliverable 5): Breaker verdict and party register agree ────

def test_breaker_quarantine_agrees_with_the_party_register_suspension(folder):
    """Deliverable 5: after ONE call to ``witness_escape.record_witness_escape``
    (imported, not re-implemented — this module does not read the party
    register to derive its own trip), the Breaker's QUARANTINED verdict and
    the party register's status BOTH reflect that same escape. They agree —
    the register is not read to produce the trip, but the trip and the
    register-side effect ``record_witness_escape`` itself causes are
    consistent, never contradictory."""
    register_party(folder, "bot7", "agent")
    assert _party_status(folder, "bot7") == "active"

    record_witness_escape(folder, ["/etc/passwd"], "bot7")

    b = Breaker(_lease("bot7"), tripwires=[])
    status = status_after_witness_escape_check(b, folder, "bot7", now=50.0)

    assert status.state is BreakerState.QUARANTINED
    assert status.effective_grade == "L0"
    assert _party_status(folder, "bot7") == "suspended"


# ── confirming (not re-implementing) the existing grade-cap path ─────────────

@pytest.mark.parametrize("status", ["suspended", "killed"])
def test_a_quarantined_actor_is_already_denied_via_the_existing_grade_cap_path(
    folder, status,
):
    """Grounds the claim the contract asks this build to confirm: once an
    actor's quarantine is reflected as a party-register kill-switch state
    (the SAME mechanism ``test_oversight_runpath_d9.py`` exercises against
    ``governance.decide_action``), a consequential action for that actor is
    already capped to L0 — with no change to ``decide_action`` or the gate.
    ``record_witness_escape`` itself only ever moves an actor to
    ``"suspended"`` (never ``"killed"``); the ``killed`` branch here exercises
    a SEPARATE, human/escalation-driven transition to confirm the same
    grade-cap path holds for it too."""
    register_party(folder, "bot7", "agent")
    record_witness_escape(folder, ["/etc/passwd"], "bot7")
    b = Breaker(_lease("bot7"), tripwires=[])
    breaker_status = status_after_witness_escape_check(b, folder, "bot7", now=50.0)
    assert breaker_status.state is BreakerState.QUARANTINED
    assert breaker_status.effective_grade == "L0"

    if status == "killed":
        set_party_status(folder, "bot7", "killed")
    else:
        assert _party_status(folder, "bot7") == "suspended"   # already automatic

    decision = decide_action(
        folder, action_class="dispatch:x", grade="L4", actor="bot7")
    assert decision["grade"] == "L0"
    assert decision["breaker_grade"] == "L0"
    assert decision["requested_grade"] == "L4"
