# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""GOVERN CLI — the thin argparse front door onto ``governance.decide_action``.

Every call resolves to PERMIT / HOLD / DENY via the SAME chokepoint every
in-process caller uses, and the gate-verdict lands on the SAME signed chain
(``incidents.log_gate_decision``, called from inside ``decide_action``) before
this module ever sees the result. These tests exercise the CLI as an external
caller would — a subprocess, stdout as the only channel — and confirm the
exit-code contract (0 permit / 3 hold / 4 deny) plus the causal effect of
``parties.set_party_status(..., "suspended")`` reaching the CLI's verdict via
the EXISTING grade-cap path (``governance._actor_grade_cap``), never a new one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from rvnd import parties
from rvnd import policy_matrix as pm
from rvnd.govern import main
from rvnd.mutation_log import MutationLog


@pytest.fixture
def folder(tmp_path, monkeypatch):
    # Isolated key + log root: nothing here ever touches the real ~/.workspace.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "org"
    f.mkdir()
    return str(f)


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, "-m", "rvnd.govern", *args],
        env=env, capture_output=True, text=True,
    )


def _gate_verdict_events(folder: str) -> list:
    return [e for e in MutationLog(folder).replay()
            if (e.extra or {}).get("kind") == "gate-verdict"]


# ── (a) permit: exit 0, stdout JSON, chain grows by the recorded verdict ─────

def test_cli_permit_exits_zero_and_records_on_chain(folder):
    log = MutationLog(folder)
    before = log.count()

    proc = _run_cli(
        "--folder", folder, "--actor", "agent-1",
        "--action-class", "dispatch:x", "--grade", "L1",
    )
    assert proc.returncode == 0, proc.stderr

    payload = json.loads(proc.stdout.strip())
    assert payload["verdict"] == "permit"
    assert payload["audit_id"]

    after = log.count()
    assert after == before + 1  # chain grew by exactly the gate-verdict event

    events = _gate_verdict_events(folder)
    assert len(events) == 1
    assert events[0].audit_id == payload["audit_id"]


def test_main_function_directly_returns_zero_on_permit(folder, capsys):
    rc = main([
        "--folder", folder, "--actor", "agent-1",
        "--action-class", "dispatch:x", "--grade", "L1",
    ])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["verdict"] == "permit"
    assert payload["audit_id"]


# ── (b) hold: exit 3 ───────────────────────────────────────────────────────

def test_cli_hold_exits_three(folder):
    m = pm.recommended_default()
    pm.set_cell(m, "L1", "approve", "ask")
    pm.save_own_matrix(folder, m)

    proc = _run_cli(
        "--folder", folder, "--actor", "agent-1",
        "--action-class", "dispatch:x", "--grade", "L1",
    )
    assert proc.returncode == 3, proc.stderr
    payload = json.loads(proc.stdout.strip())
    assert payload["verdict"] == "hold"
    assert payload["audit_id"]


# ── (c) deny: exit 4 ────────────────────────────────────────────────────────

def test_cli_deny_exits_four(folder):
    m = pm.recommended_default()
    pm.set_cell(m, "L1", "approve", "block")
    pm.save_own_matrix(folder, m)

    proc = _run_cli(
        "--folder", folder, "--actor", "agent-1",
        "--action-class", "dispatch:x", "--grade", "L1",
    )
    assert proc.returncode == 4, proc.stderr
    payload = json.loads(proc.stdout.strip())
    assert payload["verdict"] == "deny"
    assert payload["audit_id"]


# ── (d) a suspended actor is denied — the grade passes through unclamped,   ──
#        so it is the GATE'S OWN cap (governance._actor_grade_cap reading    ─
#        parties.set_party_status) that produces the deny, not this module ──

def test_suspended_actor_is_denied_via_the_existing_grade_cap(folder):
    actor = "escaped-agent"
    # A non-agent kind, matching the existing witness_escape test pattern:
    # keeps this isolated from governance_lane's separate agent-only checks,
    # so the ONLY thing capping the grade is the breaker/quarantine cap this
    # test targets.
    parties.register_party(folder, actor, "human")

    # Paint L0 (and only L0) as blocked outright — the effective cell an
    # already-suspended actor's capped grade will land on. L1 (the requested,
    # uncapped grade) stays at its default "go", so the ONLY way to reach
    # this cell is via the cap, never via what was actually requested.
    m = pm.recommended_default()
    pm.set_cell(m, "L0", "approve", "block")
    pm.save_own_matrix(folder, m)

    # Negative control: BEFORE suspension, the same call permits.
    before = _run_cli(
        "--folder", folder, "--actor", actor,
        "--action-class", "dispatch:x", "--grade", "L1",
    )
    assert before.returncode == 0, before.stderr
    assert json.loads(before.stdout.strip())["verdict"] == "permit"

    # The ONLY suspension in this test — via the PUBLIC parties API, exactly
    # as a human/operator would invoke it.
    cleared = parties.set_party_status(
        folder, actor, "suspended", reason="test quarantine", actor="human-operator")
    assert cleared["ok"]

    # The identical call, after suspension: the requested grade "L1" passed
    # straight through to decide_action (never clamped by this module) is
    # capped to "L0" by governance._actor_grade_cap BEFORE the gate runs —
    # that is what lands it on the blocked L0 cell.
    after = _run_cli(
        "--folder", folder, "--actor", actor,
        "--action-class", "dispatch:x", "--grade", "L1",
    )
    assert after.returncode == 4, after.stderr
    payload = json.loads(after.stdout.strip())
    assert payload["verdict"] == "deny"
    assert payload["grade"] == "L0"
    assert payload["requested_grade"] == "L1"


# ── grade passed straight through, never clamped or defaulted-away ──────────

def test_grade_is_not_clamped_or_defaulted(folder):
    proc = _run_cli(
        "--folder", folder, "--actor", "agent-1",
        "--action-class", "dispatch:x", "--grade", "L2",
    )
    payload = json.loads(proc.stdout.strip())
    assert payload["requested_grade"] == "L2"


# ── --action-class default ───────────────────────────────────────────────────

def test_action_class_defaults_to_shell_exec(folder):
    proc = _run_cli("--folder", folder, "--actor", "agent-1", "--grade", "L1")
    payload = json.loads(proc.stdout.strip())
    assert payload["action_class"] == "shell.exec"
    assert proc.returncode in (0, 3, 4)


# ── required args ────────────────────────────────────────────────────────────

def test_missing_required_args_is_a_usage_error(folder):
    proc = _run_cli("--folder", folder, "--actor", "agent-1")  # no --grade
    assert proc.returncode not in (0, 3, 4)
    assert proc.returncode != 0


# ── --log-root is honoured ───────────────────────────────────────────────────

def test_log_root_override_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "org"
    f.mkdir()
    custom_root = tmp_path / "custom-logs"

    proc = _run_cli(
        "--folder", str(f), "--actor", "agent-1",
        "--action-class", "dispatch:x", "--grade", "L1",
        "--log-root", str(custom_root),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip())
    assert payload["audit_id"]

    events = _gate_verdict_events_at(str(f), str(custom_root))
    assert len(events) == 1
    assert events[0].audit_id == payload["audit_id"]


def _gate_verdict_events_at(folder: str, log_root: str) -> list:
    return [e for e in MutationLog(folder, log_root=log_root).replay()
            if (e.extra or {}).get("kind") == "gate-verdict"]


# ── single JSON line on stdout ───────────────────────────────────────────────

def test_stdout_is_exactly_one_json_line(folder):
    proc = _run_cli(
        "--folder", folder, "--actor", "agent-1",
        "--action-class", "dispatch:x", "--grade", "L1",
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert {"audit_id", "verdict"} <= set(payload)
