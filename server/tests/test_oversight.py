# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for oversight dial + interactive review."""

from __future__ import annotations

import io
import json


from workspaces.lock import (
    Finding,
    OversightDecision,
    OversightLevel,
    PRIVACY_CLASS_DEFAULTS,
    effective_level,
    review_findings,
)
from workspaces.lock.oversight import (
    asks_user_per_finding,
    asks_user_per_plan,
    notifies_user_post_execution,
    waits_for_review_after_execution,
)
from workspaces.lock.interactive import interactive_cli


# ---------------------------------------------------------------------------
# Oversight level semantics
# ---------------------------------------------------------------------------


def test_six_levels_present():
    assert OversightLevel.AUTONOMOUS.value == 1
    assert OversightLevel.NOTIFY.value == 2
    assert OversightLevel.REVIEW.value == 3
    assert OversightLevel.APPROVE.value == 4
    assert OversightLevel.SUPERVISED.value == 5
    assert OversightLevel.MANUAL.value == 6


def test_higher_level_is_stricter():
    # MAX = strictest under our IntEnum convention
    assert max(OversightLevel.AUTONOMOUS, OversightLevel.APPROVE) == OversightLevel.APPROVE
    assert max(OversightLevel.NOTIFY, OversightLevel.SUPERVISED) == OversightLevel.SUPERVISED


def test_effective_level_respects_strictest():
    eff = effective_level(
        user_default=OversightLevel.AUTONOMOUS,
        op_floor=OversightLevel.APPROVE,
    )
    assert eff == OversightLevel.APPROVE


def test_effective_level_privacy_class_default():
    eff = effective_level(
        user_default=OversightLevel.AUTONOMOUS,
        privacy_class="regulated",
    )
    assert eff == OversightLevel.SUPERVISED


def test_effective_level_user_can_strictify():
    # User asks for MANUAL even though op only requires NOTIFY → MANUAL wins
    eff = effective_level(
        user_default=OversightLevel.MANUAL,
        op_floor=OversightLevel.NOTIFY,
    )
    assert eff == OversightLevel.MANUAL


def test_privacy_class_defaults():
    assert PRIVACY_CLASS_DEFAULTS["public"] == OversightLevel.NOTIFY
    assert PRIVACY_CLASS_DEFAULTS["pseudonymous"] == OversightLevel.REVIEW
    assert PRIVACY_CLASS_DEFAULTS["sensitive"] == OversightLevel.APPROVE
    assert PRIVACY_CLASS_DEFAULTS["regulated"] == OversightLevel.SUPERVISED


# ---------------------------------------------------------------------------
# Per-level user-interaction predicates
# ---------------------------------------------------------------------------


def test_only_supervised_and_manual_ask_per_finding():
    assert asks_user_per_finding(OversightLevel.SUPERVISED) is True
    assert asks_user_per_finding(OversightLevel.MANUAL) is True
    assert asks_user_per_finding(OversightLevel.APPROVE) is False
    assert asks_user_per_finding(OversightLevel.AUTONOMOUS) is False


def test_only_approve_and_higher_ask_per_plan():
    assert asks_user_per_plan(OversightLevel.APPROVE) is True
    assert asks_user_per_plan(OversightLevel.SUPERVISED) is True
    assert asks_user_per_plan(OversightLevel.MANUAL) is True
    assert asks_user_per_plan(OversightLevel.REVIEW) is False
    assert asks_user_per_plan(OversightLevel.NOTIFY) is False


def test_review_waits_for_post_exec_review():
    assert waits_for_review_after_execution(OversightLevel.REVIEW) is True
    assert waits_for_review_after_execution(OversightLevel.NOTIFY) is False


def test_notify_notifies_post_exec():
    assert notifies_user_post_execution(OversightLevel.NOTIFY) is True
    assert notifies_user_post_execution(OversightLevel.REVIEW) is False


# ---------------------------------------------------------------------------
# review_findings — auto-decision paths (deterministic tests)
# ---------------------------------------------------------------------------


def _make_finding(severity="high", type_="over_collection", field="x") -> Finding:
    return Finding(
        tier="A",
        type=type_,
        severity=severity,
        field=field,
        detail=f"test finding on {field}",
    )


def test_review_findings_autonomous_returns_auto_accepts():
    findings = [_make_finding(field=f"f{i}") for i in range(3)]
    decisions = review_findings(
        findings,
        oversight=OversightLevel.AUTONOMOUS,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
    )
    assert len(decisions) == 3
    assert all(d.user_action == "auto-accepted" for d in decisions)


def test_review_findings_supervised_with_auto_decision():
    # Auto-decision lets us test the SUPERVISED path without real stdin
    findings = [_make_finding(field=f"f{i}") for i in range(2)]
    out = io.StringIO()
    decisions = review_findings(
        findings,
        oversight=OversightLevel.SUPERVISED,
        stdin=io.StringIO(""),
        stdout=out,
        auto_decision="accept",
    )
    assert len(decisions) == 2
    assert all(d.user_action == "accept" for d in decisions)
    output = out.getvalue()
    assert "finding review" in output
    assert "supervised" in output
    assert "f0" in output  # field rendered


def test_review_findings_supervised_reject_path():
    findings = [_make_finding(field="x")]
    decisions = review_findings(
        findings,
        oversight=OversightLevel.SUPERVISED,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        auto_decision="reject",
    )
    assert decisions[0].user_action == "reject"


def test_review_findings_supervised_interactive_inputs():
    findings = [
        _make_finding(field="f1"),
        _make_finding(field="f2"),
    ]
    stdin = io.StringIO("a\nr\nbad reason\n")  # accept, reject, reason
    out = io.StringIO()
    decisions = review_findings(
        findings,
        oversight=OversightLevel.SUPERVISED,
        stdin=stdin,
        stdout=out,
    )
    assert decisions[0].user_action == "accept"
    assert decisions[1].user_action == "reject"
    assert decisions[1].reason == "bad reason"


def test_review_findings_waive_records_reason():
    findings = [_make_finding(field="x")]
    stdin = io.StringIO("w\nbusiness override approved by counsel 2026-05-16\n")
    decisions = review_findings(
        findings,
        oversight=OversightLevel.SUPERVISED,
        stdin=stdin,
        stdout=io.StringIO(),
    )
    assert decisions[0].user_action == "waive"
    assert "counsel" in decisions[0].reason


def test_review_findings_approve_mode_batches_decision():
    findings = [_make_finding(field=f"f{i}") for i in range(3)]
    stdin = io.StringIO("y\n")  # one yes for the whole batch
    out = io.StringIO()
    decisions = review_findings(
        findings,
        oversight=OversightLevel.APPROVE,
        stdin=stdin,
        stdout=out,
    )
    assert len(decisions) == 3
    assert all(d.user_action == "accept" for d in decisions)
    assert "Accept ALL" in out.getvalue()


def test_review_findings_skip_on_empty_input():
    findings = [_make_finding(field="x")]
    stdin = io.StringIO("\n")  # empty line
    decisions = review_findings(
        findings,
        oversight=OversightLevel.SUPERVISED,
        stdin=stdin,
        stdout=io.StringIO(),
    )
    assert decisions[0].user_action == "skip"


def test_review_findings_skip_on_unrecognised_input():
    findings = [_make_finding(field="x")]
    stdin = io.StringIO("xyz\n")
    decisions = review_findings(
        findings,
        oversight=OversightLevel.SUPERVISED,
        stdin=stdin,
        stdout=io.StringIO(),
    )
    assert decisions[0].user_action == "skip"


# ---------------------------------------------------------------------------
# Interactive CLI
# ---------------------------------------------------------------------------


def test_interactive_cli_end_to_end_with_auto_accept():
    """End-to-end test of the CLI with --auto accept."""
    spec = {
        "tool": "hr.get_employee",
        "arguments": {"employee_id": "E-1", "include_salary_band": True},
        "task_scope": ["employee_id"],
        "capability_token": None,
    }
    stdin = io.StringIO(json.dumps(spec))
    stdout = io.StringIO()
    stderr = io.StringIO()
    rc = interactive_cli(
        argv=["--oversight", "supervised", "--auto", "accept", "--mode", "permissive"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )
    assert rc == 0
    output = stdout.getvalue()
    assert "agent-tool-lock review" in output
    assert "egress" in output
    assert "RECORD" in output
    # Parse the JSON record at the end
    record_line = output.strip().split("\n")[-1]
    record = json.loads(record_line)
    assert record["tool"] == "hr.get_employee"
    assert record["egress_action"] in ("allow", "strip", "refuse")


def test_interactive_cli_rejects_bad_json():
    stdin = io.StringIO("this is not json")
    stderr = io.StringIO()
    rc = interactive_cli(
        argv=["--oversight", "autonomous"],
        stdin=stdin,
        stdout=io.StringIO(),
        stderr=stderr,
    )
    assert rc == 1
    assert "invalid JSON" in stderr.getvalue()


def test_interactive_cli_with_autonomous_skips_review():
    spec = {
        "tool": "hr.get_employee",
        "arguments": {"employee_id": "E-1"},  # clean call
        "task_scope": ["employee_id"],
        "capability_token": None,
    }
    stdin = io.StringIO(json.dumps(spec))
    stdout = io.StringIO()
    rc = interactive_cli(
        argv=["--oversight", "autonomous", "--mode", "permissive"],
        stdin=stdin,
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert rc == 0
    # No "finding review" header since autonomous skips per-finding review
    assert "finding review" not in stdout.getvalue() or "0 finding(s)" in stdout.getvalue()


# ---------------------------------------------------------------------------
# OversightDecision dataclass
# ---------------------------------------------------------------------------


def test_oversight_decision_default_elapsed_zero():
    d = OversightDecision(finding_id="x", user_action="accept")
    assert d.elapsed_ms == 0
    assert d.reason == ""
