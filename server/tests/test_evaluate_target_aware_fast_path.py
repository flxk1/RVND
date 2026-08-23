# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the target-aware benign fast path in ``rvnd.hook.evaluate``.

The fast path (no risk footprint, not strict) must now ALSO require the
resolved context set to be a singleton (cwd alone). A benign structured
file-write that reaches a SECOND, foreign, registered workspace must go
through the full per-context chokepoint + ``_meet_decisions`` instead, so
that workspace's own policy is evaluated and recorded — while every
non-foreign-target action (within-cwd writes, unregistered targets, Bash,
Read, ...) must be byte-identical to the pre-existing behaviour, proven here
by a within-cwd benign Write that still takes the fast path with ``decide``
never called.

Uses a temp registry (``WORKSPACE_L0_LOG_ROOT`` + a scratch
``known-workspaces.json``) so none of this depends on machine state.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rvnd import hook as H


def _write_registry(log_root: Path, roots: list[Path]) -> None:
    log_root.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "default": "",
        "workspaces": [
            {"path": str(r), "label": "", "added_at": "2026-01-01T00:00:00.000000Z"}
            for r in roots
        ],
    }
    (log_root / "known-workspaces.json").write_text(json.dumps(data))


@pytest.fixture
def two_workspaces(tmp_path, monkeypatch):
    log_root = tmp_path / "log"
    ws_a = tmp_path / "ws-a"       # cwd's own workspace
    ws_b = tmp_path / "ws-b"       # a foreign, registered workspace
    ws_a.mkdir()
    ws_b.mkdir()
    _write_registry(log_root, [ws_a, ws_b])
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    monkeypatch.delenv("RVND_HOOK_STRICT", raising=False)
    return ws_a, ws_b


def _permit_gov(tag: str):
    return {"light": "go", "reason": f"permitted ({tag})", "verdict": "permit",
             "audit_id": f"aud-go-{tag}", "oversight_level": "approve", "grade": "L2",
             "gate_verdict": "GO", "obligation_pairs": [], "policy_digest": "",
             "grounded": False, "traffic_light": "amber"}


def _block_gov(tag: str):
    return {"light": "block", "reason": f"blocked by {tag}'s policy",
             "gate_reason": f"{tag} policy: writes require sign-off",
             "verdict": "deny", "audit_id": f"aud-block-{tag}", "oversight_level": "approve",
             "grade": "L2", "gate_verdict": "NO-GO", "obligation_pairs": [],
             "policy_digest": "", "grounded": True, "traffic_light": "red"}


def test_benign_write_into_foreign_workspace_does_not_fast_path(two_workspaces):
    ws_a, ws_b = two_workspaces
    calls = []

    def recording_decide(folder, **kwargs):
        calls.append(folder)
        # cwd's own workspace permits; the FOREIGN target workspace blocks.
        if folder == str(ws_a):
            return _permit_gov("ws-a")
        return _block_gov("ws-b")

    evt = {"tool_name": "Write",
           "tool_input": {"file_path": str(ws_b / "new-file.txt"), "content": "x"},
           "cwd": str(ws_a)}
    decision = H.evaluate(evt, decide=recording_decide)

    # decide invoked for BOTH contexts.
    assert len(calls) == 2
    assert str(ws_a) in calls
    assert str(ws_b.resolve()) in calls

    # strictest wins: the target workspace's block carries its own reason.
    assert decision.kind == "deny"
    assert "ws-b policy" in decision.reason
    assert decision.detail["audit_id"] == "aud-block-ws-b"


def test_benign_write_within_cwd_still_fast_paths(two_workspaces):
    """Behavior preservation: a write that stays inside cwd's own workspace
    (or, as here, an entirely unregistered target) must be indistinguishable
    from the pre-axis-B fast path — decide is NEVER called."""
    ws_a, _ws_b = two_workspaces

    def must_not_run(folder, **kwargs):
        raise AssertionError("decide must not be called for a within-cwd benign write")

    evt = {"tool_name": "Write",
           "tool_input": {"file_path": str(ws_a / "ordinary-file.txt"), "content": "x"},
           "cwd": str(ws_a)}
    decision = H.evaluate(evt, decide=must_not_run)

    assert decision.kind == "allow"
    assert decision.detail == {}
    assert "no risk footprint" in decision.reason


def test_benign_write_to_unregistered_target_still_fast_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("RVND_HOOK_STRICT", raising=False)
    cwd = tmp_path

    def must_not_run(folder, **kwargs):
        raise AssertionError("decide must not be called for an unregistered target")

    evt = {"tool_name": "Write",
           "tool_input": {"file_path": str(tmp_path / "elsewhere" / "f.txt")},
           "cwd": str(cwd)}
    decision = H.evaluate(evt, decide=must_not_run)
    assert decision.kind == "allow"


def test_bash_and_read_are_unaffected_by_the_seam(two_workspaces):
    """Non-write tools never resolve a target, so their fast-path behaviour
    must be exactly what it always was, regardless of how many workspaces
    are registered."""
    ws_a, ws_b = two_workspaces

    def must_not_run(folder, **kwargs):
        raise AssertionError("decide must not be called for a benign Read")

    evt = {"tool_name": "Read", "tool_input": {"file_path": str(ws_b / "f.txt")},
           "cwd": str(ws_a)}
    decision = H.evaluate(evt, decide=must_not_run)
    assert decision.kind == "allow"


def test_resolve_contexts_is_computed_exactly_once_per_evaluate_call(two_workspaces, monkeypatch):
    ws_a, ws_b = two_workspaces
    call_count = {"n": 0}
    real = H.resolve_contexts

    def counting_resolve_contexts(cwd, tool_name, tool_input):
        call_count["n"] += 1
        return real(cwd, tool_name, tool_input)

    monkeypatch.setattr(H, "resolve_contexts", counting_resolve_contexts)

    def recording_decide(folder, **kwargs):
        return _permit_gov("any") if folder == str(ws_a) else _block_gov("ws-b")

    evt = {"tool_name": "Write",
           "tool_input": {"file_path": str(ws_b / "f.txt")},
           "cwd": str(ws_a)}
    H.evaluate(evt, decide=recording_decide)
    assert call_count["n"] == 1

    # Also true on the fast-path branch (within-cwd write, singleton context).
    call_count["n"] = 0
    evt_local = {"tool_name": "Write",
                 "tool_input": {"file_path": str(ws_a / "g.txt")},
                 "cwd": str(ws_a)}
    H.evaluate(evt_local, decide=lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("decide must not run on the fast path")))
    assert call_count["n"] == 1


def test_flagged_action_into_foreign_workspace_is_still_fully_evaluated(two_workspaces):
    """A footprint-flagged action already skipped the fast path before this
    change; confirm it still resolves both contexts and joins strictest-wins
    when RVND_HOOK_STRICT is not involved at all."""
    ws_a, ws_b = two_workspaces
    calls = []

    def recording_decide(folder, **kwargs):
        calls.append(folder)
        return _permit_gov("ws-a") if folder == str(ws_a) else _block_gov("ws-b")

    evt = {"tool_name": "Write",
           "tool_input": {"file_path": str(ws_b / ".ssh" / "id_rsa")},
           "cwd": str(ws_a)}
    decision = H.evaluate(evt, decide=recording_decide)
    assert len(calls) == 2
    assert decision.kind == "deny"
