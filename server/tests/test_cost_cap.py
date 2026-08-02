# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Cost-cap enforcement, not just readable spend.

The tests cover spend ledger totals, refusal at or over the cap, under-cap and
no-cap runs, invalid policy handling, and unrelated malformed policy fields.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from workspaces.llm_capture import (
    IngestMode,
    LLMExchange,
    OversightLevel,
    _pair_cost,
    capture_llm_exchange,
    folder_spend_cents,
)
from workspaces.operations import operate
from workspaces.policy import (
    POLICY_FILENAME,
    FolderPolicy,
    InvalidPolicy,
    load_policy,
    save_policy,
    verified_cost_cap,
)
from workspaces.use_case import register_use_case

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def _record_spend(env, cents: float, *, n: int = 1) -> None:
    """Record `n` LLM exchanges each costing `cents`, into the same log root
    the operate guard reads from."""
    for i in range(n):
        capture_llm_exchange(
            LLMExchange(model="test-model",
                        prompt_context=f"q{i}-{cents}",
                        response="a",
                        cost_estimate_cents=cents),
            mode=IngestMode.AGENTIC,
            oversight=OversightLevel.AUTONOMOUS,
            folder_context=env["ws"],
            log_root=env["lr"],
            actor="bot-7",
        )


def _register(env, *, risk="low", approvals=20, uid="uc1"):
    register_use_case(env["ws"], use_case_id=uid, name=uid,
                      fingerprint={"issue_type": "liability_cap",
                                   "profile": "legal-de", "rooms": ["§ 309"]},
                      risk=risk, allowed_agents=["bot-7"], actor="alex",
                      prior_approvals=approvals, override_window_seconds=120,
                      log_root=env["lr"])


def _issues():
    return [{"issue_id": "i1", "issue_type": "liability_cap",
             "completeness": "high"}]


def _set_cap(env, cents):
    p = FolderPolicy(cost_cap_cents=cents)
    save_policy(env["ws"], p)


# ── helper sums the ledger ──────────────────────────────────────────────────
def test_folder_spend_cents_sums_ledger(env):
    assert folder_spend_cents(env["ws"], log_root=env["lr"]) == 0.0
    _record_spend(env, 12.5, n=2)
    assert folder_spend_cents(env["ws"], log_root=env["lr"]) == 25.0


# ── refuse once spend reaches the cap ───────────────────────────────────────
def test_operate_refuses_at_or_over_cap(env):
    _register(env)
    _record_spend(env, 30.0)            # spend 30
    _set_cap(env, 25.0)                 # cap 25  → 30 >= 25
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert run["final"] == "refused"
    assert "cost cap" in run["reason"]
    assert run["steps"] == []           # no step ran


# ── boundary: spend EXACTLY at the cap refuses (pins `>=`, not `>`) ─────────
def test_operate_refuses_at_exact_boundary(env):
    _register(env)
    _record_spend(env, 25.0)            # spend 25
    _set_cap(env, 25.0)                 # cap 25 → 25 >= 25
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert run["final"] == "refused" and "cost cap" in run["reason"]


# ── C3: the guard is the CAUSE — same use case flips on the cap alone ───────
def test_operate_guard_is_the_cause_not_the_contract(env):
    _register(env)
    _record_spend(env, 50.0)            # spend 50, fixed
    # under the cap → proceeds
    _set_cap(env, 100.0)
    ok = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                 issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert ok["final"] != "refused" and len(ok["steps"]) == 1
    # lower the cap below the SAME spend → the SAME run now refuses, proving the
    # cap (not the contract grade) is what changed the outcome.
    _set_cap(env, 40.0)
    blocked = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                      issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert blocked["final"] == "refused" and "cost cap" in blocked["reason"]


# ── C4: no cap set → opt-in no-op ───────────────────────────────────────────
def test_operate_noop_when_cap_unset(env):
    _register(env)
    _record_spend(env, 9999.0)          # huge spend, but no cap declared
    assert load_policy(env["ws"]).cost_cap_cents is None
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert run["final"] != "refused"


# ── fail-safe: a corrupt policy file does NOT silently drop a cap ───────────
def test_operate_fails_closed_on_corrupt_policy(env):
    _register(env)
    _set_cap(env, 100.0)                # a real cap was declared
    # corrupt the policy file (truncated / invalid JSON — the common case)
    (Path(env["ws"]) / POLICY_FILENAME).write_text("{ this is not json")
    cap, verifiable = verified_cost_cap(env["ws"])
    assert cap is None and verifiable is False
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert run["final"] == "refused" and "cannot verify" in run["reason"]


def test_operate_does_not_refuse_on_unrelated_malformed_field(env):
    # A malformed *unrelated* field must not make operate refuse (scoped read):
    # verified_cost_cap reads only the cap field. local_llm garbage + no cap.
    _register(env)
    (Path(env["ws"]) / POLICY_FILENAME).write_text(
        '{"local_llm": {"mode": "not-a-real-mode"}}')
    cap, verifiable = verified_cost_cap(env["ws"])
    assert cap is None and verifiable is True
    run = operate(env["ws"], use_case_id="uc1", agent_id="bot-7",
                  issues=_issues(), now_epoch=1000, log_root=env["lr"])
    assert run["final"] != "refused"


# ── fail-safe: poisoned cost facets can't disable the cap ───────────────────
@pytest.mark.parametrize("poison", [float("nan"), float("inf"), -50.0, True, "x", None])
def test_pair_cost_rejects_poison(poison):
    assert _pair_cost({"facets": {"cost_estimate_cents": poison}}) == 0.0


def test_pair_cost_counts_valid():
    assert _pair_cost({"facets": {"cost_estimate_cents": 12.5}}) == 12.5


def test_verified_cost_cap_no_file_is_no_cap(env):
    cap, verifiable = verified_cost_cap(env["ws"])     # nothing written
    assert cap is None and verifiable is True


# ── C6: malformed cap is a policy error, not a silent fail-open ─────────────
@pytest.mark.parametrize("bad", [
    {"cost_cap_cents": "lots"},
    {"cost_cap_cents": -1},
    {"cost_cap_cents": "nan"},
    {"cost_cap_cents": "inf"},
    {"cost_cap_cents": True},
])
def test_malformed_cap_raises(bad):
    with pytest.raises(InvalidPolicy):
        FolderPolicy.from_dict(bad)


def test_cost_cap_roundtrips_and_is_omitted_when_unset():
    assert "cost_cap_cents" not in FolderPolicy().to_dict()
    restored = FolderPolicy.from_dict(FolderPolicy(cost_cap_cents=42.0).to_dict())
    assert restored.cost_cap_cents == 42.0
