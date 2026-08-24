# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Witness escape — recording one signed event AND causally quarantining the
actor, never silently.

``record_witness_escape`` is detect-then-respond in one call: it appends
exactly one event to the workspace's signed mutation chain through the
EXISTING ``MutationLog.append`` signing path (never hand-rolled signing),
places its ``kind`` where every other gate-verdict/incident event places
theirs (``extra["kind"]``), leaves the chain verifying afterward, and THEN
calls the public ``parties.set_party_status(..., "suspended")`` — the same
transition ``governance._actor_grade_cap`` reads to cap the actor's autonomy
grade to L0 before ``governance.decide_action``'s gate runs (D9). Every
failure path raises a typed error rather than returning a partial result.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from rvnd import parties
from rvnd.governance import decide_action
from rvnd.mutation_log import MutationLog
from rvnd.witness_escape import (
    WITNESS_ESCAPE_KIND,
    WitnessEscapeInputError,
    WitnessEscapeQuarantineError,
    WitnessEscapeVerificationError,
    record_witness_escape,
    recent_witness_escapes,
)


@pytest.fixture
def folder(tmp_path, monkeypatch):
    # Isolated key + log root: nothing here ever touches the real ~/.workspace.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "org"
    f.mkdir()
    return str(f)


@pytest.fixture
def log_root(tmp_path):
    root = tmp_path / "logs"
    root.mkdir(exist_ok=True)
    return root


# ── the append is one real, signed, chain-verifying event ────────────────────

def test_records_exactly_one_signed_event_and_chain_still_verifies(folder):
    log = MutationLog(folder)
    assert log.count() == 0

    result = record_witness_escape(
        folder, ["/etc/passwd", "/tmp/other"], "bot7", run_since=100.0)

    assert result["audit_id"]

    events = list(log.replay())
    # One witness-escape event + one PartyStatus (quarantine) event.
    assert len(events) == 2
    escape_events = [e for e in events if (e.extra or {}).get("kind") == WITNESS_ESCAPE_KIND]
    assert len(escape_events) == 1
    evt = escape_events[0]
    assert evt.audit_id == result["audit_id"]
    assert evt.actor == "bot7"
    # Signed via the EXISTING append() path — never hand-rolled.
    assert evt.signature
    assert evt.prev_hash

    extra = evt.extra
    assert extra["kind"] == WITNESS_ESCAPE_KIND
    assert extra["actor"] == "bot7"
    assert extra["count"] == 2
    assert extra["run_since"] == 100.0
    assert len(extra["paths"]) == 2

    verification = log.verify_chain()
    assert verification.ok


def test_kind_is_placed_the_same_way_gate_verdict_events_place_theirs(folder):
    """Match the convention: ``event`` stays the generic ``"system"``, the
    discriminator lives at ``extra["kind"]`` (see ``incidents.log_gate_decision``:
    ``event="system", extra={"kind": _GATE_KIND, ...}``)."""
    record_witness_escape(folder, ["/etc/passwd"], "bot7")
    evt = next(e for e in MutationLog(folder).replay()
               if (e.extra or {}).get("kind") == WITNESS_ESCAPE_KIND)
    assert evt.event == "system"
    assert evt.extra["kind"] == "witness-escape"


def test_relative_paths_recorded_not_bare_absolute_paths(folder):
    record_witness_escape(folder, [f"{folder}/../secrets/keys.pem"], "bot7")
    evt = next(e for e in MutationLog(folder).replay()
               if (e.extra or {}).get("kind") == WITNESS_ESCAPE_KIND)
    (path,) = evt.extra["paths"]
    assert not path.startswith(folder)


def test_return_shape_is_audit_id_and_event(folder):
    result = record_witness_escape(folder, ["/etc/passwd"], "bot7")
    assert set(result) == {"audit_id", "event"}
    assert isinstance(result["event"], dict)
    assert result["event"]["audit_id"] == result["audit_id"]


# ── read path: recent_witness_escapes ─────────────────────────────────────────

def test_recent_witness_escapes_filters_by_actor(folder):
    record_witness_escape(folder, ["/etc/passwd"], "bot7")
    record_witness_escape(folder, ["/tmp/x"], "bot8")
    assert len(recent_witness_escapes(folder, "bot7")) == 1
    assert len(recent_witness_escapes(folder, "bot8")) == 1
    assert recent_witness_escapes(folder, "nobody") == []


def test_recent_witness_escapes_filters_by_since(folder):
    # LogEvent.ts defaults to a real time.time() read at construction, so
    # bound the window against the two events' OWN recorded timestamps
    # rather than mocking the clock (mocking time.time on the ``time``
    # module does not reach mutation_log's already-bound default_factory).
    first = record_witness_escape(folder, ["/etc/passwd"], "bot7")
    time.sleep(0.01)
    second = record_witness_escape(folder, ["/tmp/x"], "bot7")

    ts_first = first["event"]["ts"]
    ts_second = second["event"]["ts"]
    assert ts_second > ts_first
    midpoint = (ts_first + ts_second) / 2

    assert len(recent_witness_escapes(folder, "bot7", since=None)) == 2
    assert len(recent_witness_escapes(folder, "bot7", since=midpoint)) == 1
    assert len(recent_witness_escapes(folder, "bot7", since=ts_second + 1.0)) == 0


def test_no_witness_escape_recorded_reads_as_empty_not_a_failure(folder):
    assert recent_witness_escapes(folder, "bot7") == []


# ── typed errors, never a silent no-op ────────────────────────────────────────

def test_empty_actor_raises_typed_error_before_writing(folder):
    log = MutationLog(folder)
    with pytest.raises(WitnessEscapeInputError):
        record_witness_escape(folder, ["/etc/passwd"], "")
    assert log.count() == 0


def test_empty_paths_raises_typed_error_before_writing(folder):
    """Empty ``unauthorised_paths`` raises with NO event AND NO suspension."""
    log = MutationLog(folder)
    parties.register_party(folder, "bot7", "human")
    with pytest.raises(WitnessEscapeInputError):
        record_witness_escape(folder, [], "bot7")
    assert log.count() == 1  # only the registration — no event was appended
    status = parties.list_parties(folder)["parties"][0]["status"]
    assert status == "active"  # no suspension happened


def test_chain_verification_failure_after_append_raises_and_skips_quarantine(folder, monkeypatch):
    """If the chain no longer verifies after the append (simulated), the
    function must raise rather than returning a green result, AND must never
    reach the quarantine call."""
    import rvnd.witness_escape as we

    class _FakeVerification:
        ok = False
        broken_links = [{"reason": "simulated"}]
        signature_failures = []
        malformed_lines = 0

    def _patched_verify_chain(self):
        return _FakeVerification()

    quarantine_calls = []
    monkeypatch.setattr(we.MutationLog, "verify_chain", _patched_verify_chain)
    monkeypatch.setattr(
        "rvnd.parties.set_party_status",
        lambda *a, **k: quarantine_calls.append((a, k)),
    )
    with pytest.raises(WitnessEscapeVerificationError):
        record_witness_escape(folder, ["/etc/passwd"], "bot7")
    assert quarantine_calls == []  # quarantine must never be attempted


def test_quarantine_failure_after_clean_append_raises_typed_error_naming_audit_id(folder, monkeypatch):
    """The event can land (and verify) while the quarantine call itself
    fails — that must surface as a typed error naming the audit_id, never a
    silent partial success."""
    def _boom(*a, **k):
        raise RuntimeError("party store unavailable")

    # record_witness_escape does ``from .parties import set_party_status``
    # freshly at call time, so patching the source attribute is what a
    # caller of the PUBLIC parties API actually observes.
    import rvnd.parties as parties_mod
    monkeypatch.setattr(parties_mod, "set_party_status", _boom)

    log = MutationLog(folder)
    with pytest.raises(WitnessEscapeQuarantineError) as excinfo:
        record_witness_escape(folder, ["/etc/passwd"], "bot7")
    # The event is already permanent on the chain — the error names it.
    events = [e for e in log.replay() if (e.extra or {}).get("kind") == WITNESS_ESCAPE_KIND]
    assert len(events) == 1
    assert events[0].audit_id in str(excinfo.value)


# ── (b) THE CAUSALITY TEST — the load-bearing assertion ──────────────────────

def test_causal_quarantine_caps_grade_and_negative_control_in_same_fixture(folder):
    """One fixture, two decide_action calls around ONE record_witness_escape
    call, and no other call to set_party_status anywhere in this test body:

      1. NEGATIVE CONTROL (no escape recorded yet): decide_action returns the
         requested grade "L4" and is NOT denied for grade.
      2. record_witness_escape(folder, ["p"], actor) — the ONLY suspension
         in this test.
      3. CAUSALITY: the identical decide_action call now returns grade
         capped to "L0" and verdict "deny".

    Registering the actor as a party first is a necessary FIXTURE
    precondition, not a duplicate suspension: ``parties.set_party_status``
    only takes projected effect for a party_id that already has a prior
    PartyRegistered event on the chain (``parties._list_parties_local``) —
    without this registration, ``record_witness_escape``'s quarantine call
    would be a recorded-but-inert no-op, and the test would prove nothing.
    Registering is not "active"/"suspended" — it carries no status effect of
    its own beyond making the actor visible to the register.

    Deleting ONLY the ``record_witness_escape(...)`` line below must flip
    the causality assertions from denied/L0 to not-denied/L4 (the identical
    negative-control outcome) — that is the whole point of this test.
    """
    actor = "escaped-agent"
    # Fixture precondition (not a suspension): make the actor visible to the
    # party register in THIS folder, as a non-agent kind so unrelated
    # governance-lane approval logic (which only gates registered "agent"
    # kind parties) cannot confound the grade-cap signal this test isolates.
    parties.register_party(folder, actor, "human")

    call_kwargs = dict(
        action_class="dispatch:x",
        grade="L4",
        actor=actor,
        footprint=("irreversible",),  # a consequential footprint: at L0 this
                                       # is structurally under-graded (NO-GO/
                                       # deny); an empty footprint would only
                                       # produce "hold", never "deny".
    )

    # 1. Negative control — BEFORE the escape is recorded.
    negative = decide_action(folder, **call_kwargs)
    assert negative["grade"] == "L4"
    assert negative["verdict"] != "deny"
    assert negative["breaker_grade"] == ""

    # 2. The ONLY suspension-causing call in this entire test body.
    record_witness_escape(folder, ["p"], actor)

    # 3. Causality — the identical call, after the escape, is capped.
    positive = decide_action(folder, **call_kwargs)
    assert positive["grade"] == "L0"
    assert positive["verdict"] == "deny"
    assert positive["breaker_grade"] == "L0"
    assert "below required" in positive["gate_reason"]


# ── (c) SCOPING — the cap never crosses a folder boundary ────────────────────

def test_escape_in_folder_a_does_not_cap_actor_in_folder_b(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")

    folder_a = tmp_path / "org_a"
    folder_a.mkdir()
    folder_b = tmp_path / "org_b"
    folder_b.mkdir()
    folder_a, folder_b = str(folder_a), str(folder_b)

    actor = "shared-actor"
    parties.register_party(folder_a, actor, "human")
    parties.register_party(folder_b, actor, "human")

    record_witness_escape(folder_a, ["p"], actor)

    # Capped in A...
    in_a = decide_action(folder_a, action_class="dispatch:x", grade="L4",
                         actor=actor, footprint=("irreversible",))
    assert in_a["grade"] == "L0"
    assert in_a["verdict"] == "deny"

    # ...but NOT in B — a different folder's mutation log/party register.
    in_b = decide_action(folder_b, action_class="dispatch:x", grade="L4",
                         actor=actor, footprint=("irreversible",))
    assert in_b["grade"] == "L4"
    assert in_b["verdict"] != "deny"
    assert in_b["breaker_grade"] == ""


# ── (d) BEHAVIOR-PRESERVING — no escape recorded, nothing changes ────────────

def test_no_escape_recorded_decide_action_unchanged(folder):
    actor = "quiet-agent"
    parties.register_party(folder, actor, "human")

    before = decide_action(folder, action_class="dispatch:x", grade="L4",
                           actor=actor, footprint=("irreversible",))
    after = decide_action(folder, action_class="dispatch:x", grade="L4",
                          actor=actor, footprint=("irreversible",))
    assert before["grade"] == after["grade"] == "L4"
    assert before["verdict"] == after["verdict"]
    assert after["breaker_grade"] == ""
    assert recent_witness_escapes(folder, actor) == []


def test_no_escape_recorded_register_unchanged(folder):
    actor = "quiet-agent"
    reg = parties.register_party(folder, actor, "human")
    status_before = parties.list_parties(folder)["parties"]
    status_after = parties.list_parties(folder)["parties"]
    assert status_before == status_after
    assert status_before[0]["party_id"] == actor
    assert status_before[0]["status"] == "active"
    assert reg["ok"]


# ── (e) HUMAN-CLEAR-ONLY — the actor cannot self-clear ────────────────────────

def test_only_a_human_set_party_status_call_restores_grade_after_escape(folder):
    actor = "escaped-agent"
    parties.register_party(folder, actor, "human")

    record_witness_escape(folder, ["p"], actor)
    capped = decide_action(folder, action_class="dispatch:x", grade="L4",
                           actor=actor, footprint=("irreversible",))
    assert capped["grade"] == "L0"
    assert capped["verdict"] == "deny"

    # This module exposes NO self-clear path at all: no "clear"/"release"/
    # "resume"/"reactivate" function of any kind, for the actor or anyone
    # else. The only way back to "active" is calling the public parties API
    # directly, as a human/operator action — never something this module's
    # public surface can do on the actor's behalf.
    import rvnd.witness_escape as we
    self_clear_names = {"clear_witness_escape", "clear", "release", "resume",
                        "reactivate", "unsuspend", "restore", "activate"}
    assert not (self_clear_names & set(we.__all__))
    for name in self_clear_names:
        assert not hasattr(we, name)

    # A human-invoked call to the public parties API is what restores it.
    cleared = parties.set_party_status(folder, actor, "active",
                                       reason="human review cleared the flag",
                                       actor="human-operator")
    assert cleared["ok"]

    restored = decide_action(folder, action_class="dispatch:x", grade="L4",
                             actor=actor, footprint=("irreversible",))
    assert restored["grade"] == "L4"
    assert restored["verdict"] != "deny"
    assert restored["breaker_grade"] == ""


# ── CLI ────────────────────────────────────────────────────────────────────────

def test_cli_record_prints_audit_id_and_exits_zero(folder, log_root):
    import os
    env = dict(os.environ)
    proc = subprocess.run(
        [sys.executable, "-m", "rvnd.witness_escape", "record",
         "--folder", folder, "--actor", "bot7",
         "--paths", "/etc/passwd,/tmp/x", "--since", "100.0"],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    audit_id = proc.stdout.strip()
    assert audit_id

    log = MutationLog(folder)
    events = [e for e in log.replay() if (e.extra or {}).get("kind") == WITNESS_ESCAPE_KIND]
    assert len(events) == 1
    assert events[0].audit_id == audit_id


def test_cli_record_failure_exits_nonzero_with_clear_message(folder):
    import os
    env = dict(os.environ)
    proc = subprocess.run(
        [sys.executable, "-m", "rvnd.witness_escape", "record",
         "--folder", folder, "--actor", "bot7", "--paths", "   ,  "],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "witness-escape record failed" in proc.stderr
    assert proc.stderr.strip()
