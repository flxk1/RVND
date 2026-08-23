# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Behavior-preservation test for the ``evaluate()`` axis-B refactor.

``evaluate()`` now resolves ``ctxs = resolve_contexts(cwd, tool_name,
tool_input)``, calls ``decide()`` once per context, and joins the results
through ``_meet_decisions``. Today ``resolve_contexts`` always returns the
singleton ``(cwd,)``, so this must be indistinguishable from calling
``decide(cwd, ...)`` exactly once and mapping its result directly — the same
Decision (kind, reason, detail), the same single call, with the acting
``cwd`` as the sole folder argument. This test proves that equivalence with
an injected, call-recording ``decide`` stub rather than relying on the real
governance chokepoint.
"""
from __future__ import annotations

from pathlib import Path

from rvnd import hook as H

REPO = Path(__file__).resolve().parents[2]


def _flagged_evt(cwd: str):
    return {"tool_name": "Bash", "tool_input": {"command": "sudo rm x"}, "cwd": cwd}


def _stub_gov():
    return {"light": "block", "reason": "gate NO-GO",
            "gate_reason": "grade L2 below required for irreversible (needs grade >= 3)",
            "verdict": "deny", "audit_id": "aud-singleton", "oversight_level": "approve",
            "grade": "L2", "gate_verdict": "NO-GO", "obligation_pairs": ["pair-1"],
            "policy_digest": "deadbeef16chars0", "grounded": True, "traffic_light": "red"}


def test_evaluate_singleton_context_calls_decide_exactly_once_with_cwd():
    cwd = str(REPO)
    calls = []

    def recording_decide(folder, **kwargs):
        calls.append((folder, kwargs))
        return _stub_gov()

    H.evaluate(_flagged_evt(cwd), decide=recording_decide)
    assert len(calls) == 1
    folder_arg, kwargs = calls[0]
    assert folder_arg == cwd
    assert kwargs["action_class"] == "shell.exec"


def test_evaluate_singleton_context_matches_direct_decide_and_map():
    cwd = str(REPO)
    evt = _flagged_evt(cwd)

    # (A) the refactored path: evaluate() resolves contexts (singleton today)
    # and joins through _meet_decisions.
    refactored = H.evaluate(evt, decide=lambda folder, **k: _stub_gov())

    # (B) the pre-refactor shape: call the SAME stub once directly for cwd and
    # map its result exactly as evaluate()'s downstream mapping does.
    gov = _stub_gov()
    light = str(gov.get("light") or "")
    reason = str(gov.get("reason") or "")
    why = str(gov.get("gate_reason") or "") or reason
    action_class, footprint, _affected, evidence = H.classify(
        evt["tool_name"], evt["tool_input"], cwd)
    detail = {"action_class": action_class, "footprint": list(footprint),
              "evidence": evidence, "verdict": gov.get("verdict"),
              "audit_id": gov.get("audit_id"),
              "oversight_level": gov.get("oversight_level"),
              "grade": gov.get("grade"),
              "gate_verdict": gov.get("gate_verdict"),
              "obligation_pairs": gov.get("obligation_pairs") or [],
              "policy_digest": gov.get("policy_digest", ""),
              "grounded": bool(gov.get("grounded")),
              "traffic_light": gov.get("traffic_light") or "amber"}
    hint = H._unblock_hint(footprint)
    direct = H.Decision("deny", f"{why or 'blocked by policy'}"
                        + (f". {hint}" if hint else ""), detail)

    assert refactored.kind == direct.kind == "deny"
    assert refactored.reason == direct.reason
    assert refactored.detail == direct.detail


def test_evaluate_singleton_context_go_matches_direct_path():
    cwd = str(REPO)
    evt = _flagged_evt(cwd)
    gov = {"light": "go", "reason": "permitted", "verdict": "permit",
           "audit_id": "aud-go", "oversight_level": "approve", "grade": "L2",
           "gate_verdict": "GO", "obligation_pairs": [], "policy_digest": "",
           "grounded": False, "traffic_light": "amber"}

    refactored = H.evaluate(evt, decide=lambda folder, **k: gov)
    assert refactored.kind == "allow"
    assert refactored.reason == "permitted"
    assert refactored.detail["audit_id"] == "aud-go"


def test_evaluate_singleton_context_ask_matches_direct_path():
    cwd = str(REPO)
    evt = _flagged_evt(cwd)
    gov = {"light": "ask", "reason": "requires human sign-off", "verdict": "hold",
           "audit_id": "aud-ask", "oversight_level": "approve", "grade": "L2",
           "gate_verdict": "CONDITIONAL", "obligation_pairs": [], "policy_digest": "",
           "grounded": False, "traffic_light": "amber"}

    refactored = H.evaluate(evt, decide=lambda folder, **k: gov)
    assert refactored.kind == "ask"
    assert "requires human sign-off" in refactored.reason
    assert refactored.detail["audit_id"] == "aud-ask"


def test_evaluate_benign_fast_path_unaffected_by_seam():
    """The benign short-circuit must still bypass resolve_contexts/decide entirely."""
    def must_not_run(folder, **k):
        raise AssertionError("decide must not be called for a benign action")
    d = H.evaluate({"tool_name": "Read", "tool_input": {"file_path": "x"}, "cwd": str(REPO)},
                   decide=must_not_run)
    assert d.kind == "allow"


def test_evaluate_decide_error_still_fails_closed_through_the_seam():
    def boom(folder, **k):
        raise RuntimeError("engine down")
    d = H.evaluate(_flagged_evt(str(REPO)), decide=boom)
    assert d.kind == "fail"
    assert "engine down" in d.reason
