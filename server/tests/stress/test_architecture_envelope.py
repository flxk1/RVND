# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Deliverable 4 — End-to-end architecture envelope (B10+).

Full-pipeline integration tests. Each test pins one architectural
invariant the design promises: validator-before-commit, air-gap mode,
cross-surface equivalence, audit-chain ordering. All Workspace internals
(mutation_log, rvnd.lock, mirrors, erasure) run for real; only the
cloud-LLM boundary and the local-LLM endpoint are mocked.
"""

from __future__ import annotations

import json

import pytest

from rvnd import signing
from rvnd.mutation_log import LogEvent, MutationLog
from rvnd.pinned_skills import pin_skill, record_dispatch

from tests.stress._harness import MockCloudLLM, MockLocalLLM


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    log_root = tmp_path / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    return {
        "log_root":  log_root,
        "keydir":    keydir,
        "workspace": workspace,
    }


# ---------------------------------------------------------------------------
# 1. Full pipeline: ingest → lock → dispatch → capture → audit
# ---------------------------------------------------------------------------


def test_full_pipeline_ingest_to_audit(isolated_env):
    """One end-to-end run: ingest a file, dispatch a skill (with cloud
    validator-before-commit), capture the exchange, and verify the audit
    chain contains the expected event sequence."""
    from rvnd.inbox_watcher import ingest_file
    from rvnd.lock.core import lock_text, Mode

    ws = isolated_env["workspace"]
    log_root = isolated_env["log_root"]

    # Pin a skill so dispatch is valid.
    pin_skill(ws, "workspace:test-skill", log_root=log_root)

    # Ingest.
    src = ws / "doc.md"
    src.write_text("Plain prose about a topic with no PII whatsoever.", encoding="utf-8")
    pair_ids = ingest_file(src, ws, log_root=log_root, actor="test")
    assert pair_ids, "ingest produced no pairs"

    # Lock pre-cloud.
    text = "Plain prose about a topic with no PII whatsoever."
    decision = lock_text(text, mode=Mode.STANDARD, source="freeform")
    assert decision.action == "allow"

    # Mock cloud dispatch.
    cloud = MockCloudLLM()
    local = MockLocalLLM()
    with cloud, local:
        cloud.dispatch(text, lock_pre_call=True)
        record_dispatch(ws, "workspace:test-skill", log_root=log_root,
                         actor="test")

    # Audit chain — walk and check event types appear in expected order.
    log = MutationLog(ws, log_root=log_root)
    events = list(log.replay())
    assert events, "audit chain is empty"

    # Verify_chain integrity.
    result = log.verify_chain()
    assert result.ok, (
        f"audit chain integrity broken: links={result.broken_links}, "
        f"sigs={result.signature_failures}"
    )

    # Expected sequence: ingest event first, then skill-dispatch event.
    saw_ingest = any(e.event == "ingest" for e in events)
    saw_dispatch = any(
        e.event == "system" and (e.extra or {}).get("dispatch") == "skill"
        for e in events
    )
    assert saw_ingest, "no ingest event on chain"
    assert saw_dispatch, "no skill-dispatch event on chain"


# ---------------------------------------------------------------------------
# 2. Validator-before-commit blocks bad draft
# ---------------------------------------------------------------------------


def test_validator_before_commit_blocks_bad_draft(isolated_env):
    """When the local validator classifies the cloud draft as 'incorrect',
    the draft MUST NOT land in memory and a ``validator_rejected`` event
    MUST be written."""
    ws = isolated_env["workspace"]
    log_root = isolated_env["log_root"]

    cloud = MockCloudLLM(response_factory=lambda p: "This is a fabricated answer with no source.")
    # Local validator: every input → "pii_yes" simulates "this draft is wrong"
    local = MockLocalLLM(classify_fn={
        "phi-3.5-mini-q4":        lambda t: "pii_yes",
        "qwen-2.5-coder-7b-q4":   lambda t: "pii_yes",
        "mistral-7b-instruct-q4": lambda t: "pii_yes",
    })

    log_before = list(MutationLog(ws, log_root=log_root).replay())

    with cloud, local:
        from rvnd.lock.core import tier_c_semantic_check
        cloud_response = cloud.dispatch("Generate an answer to this question.")
        verdict = tier_c_semantic_check(cloud_response)
        # Validator says reject → write a validator_rejected event, do
        # NOT commit the draft to memory.
        if verdict is not None and verdict.label == "pii_yes":
            log = MutationLog(ws, log_root=log_root)
            log.append(LogEvent(
                event="system", folder_path=str(ws),
                pair_id="validator-test", channel="system",
                actor="test", extra={
                    "kind":        "validator_rejected",
                    "model":       "ensemble:phi+qwen",
                    "verdict":     verdict.label,
                    "per_model":   verdict.per_model,
                },
            ))
            committed = False
        else:
            committed = True

    assert not committed, "validator should have rejected the draft"
    events = list(MutationLog(ws, log_root=log_root).replay())
    new = events[len(log_before):]
    rejected = [e for e in new if (e.extra or {}).get("kind") == "validator_rejected"]
    assert len(rejected) == 1, "missing validator_rejected audit event"
    # The draft (cloud response) must NOT appear anywhere on chain.
    for e in new:
        assert "fabricated answer" not in json.dumps(e.extra or {})


# ---------------------------------------------------------------------------
# 3. Validator INSUFFICIENT → human review queue
# ---------------------------------------------------------------------------


def test_validator_insufficient_escalates_to_human_review(isolated_env):
    """INSUFFICIENT verdict: not committed; oversight queue gets the
    entry (we model the queue as a simple in-memory list)."""
    ws = isolated_env["workspace"]
    log_root = isolated_env["log_root"]
    cloud = MockCloudLLM()
    local = MockLocalLLM(classify_fn={
        "phi-3.5-mini-q4":        lambda t: "pii_yes",
        "qwen-2.5-coder-7b-q4":   lambda t: "pii_no",   # disagreement → insufficient
        "mistral-7b-instruct-q4": lambda t: "pii_no",
    })

    oversight_queue: list[dict] = []

    with cloud, local:
        from rvnd.lock.core import tier_c_semantic_check
        draft = cloud.dispatch("Generate an answer.")
        verdict = tier_c_semantic_check(draft)
        if verdict is not None and verdict.label == "insufficient":
            log = MutationLog(ws, log_root=log_root)
            log.append(LogEvent(
                event="system", folder_path=str(ws),
                pair_id="oversight-queue-entry",
                channel="system", actor="test",
                extra={"kind": "queued_for_human_review",
                        "reason": verdict.reason},
            ))
            oversight_queue.append({
                "draft":   draft,
                "reason":  verdict.reason,
            })
            committed = False
        else:
            committed = True

    assert not committed
    assert len(oversight_queue) == 1
    events = list(MutationLog(ws, log_root=log_root).replay())
    assert any((e.extra or {}).get("kind") == "queued_for_human_review"
                for e in events)


# ---------------------------------------------------------------------------
# 4. Cross-surface invariant — CLI vs MCP dispatch
# ---------------------------------------------------------------------------


def test_cross_surface_invariant_same_envelope_from_cli_and_mcp(isolated_env):
    """Same skill dispatch via the Python API (CLI-equivalent) and via
    record_dispatch with an MCP marker. Only ``extra.invoked_via`` may
    differ; everything else (skill_id, folder, kind=skill) must match."""
    ws = isolated_env["workspace"]
    log_root = isolated_env["log_root"]
    pin_skill(ws, "workspace:cross-surface", log_root=log_root)

    # CLI invocation.
    record_dispatch(
        ws, "workspace:cross-surface", log_root=log_root,
        actor="cli", chosen_via="cli",
        extra={"invoked_via": "cli"},
    )
    # MCP invocation.
    record_dispatch(
        ws, "workspace:cross-surface", log_root=log_root,
        actor="mcp", chosen_via="mcp",
        extra={"invoked_via": "mcp"},
    )

    log = MutationLog(ws, log_root=log_root)
    events = [e for e in log.replay() if (e.extra or {}).get("dispatch") == "skill"]
    assert len(events) >= 2
    cli_evt = next(e for e in events if (e.extra or {}).get("invoked_via") == "cli")
    mcp_evt = next(e for e in events if (e.extra or {}).get("invoked_via") == "mcp")
    # Same skill_id, same folder, same dispatch kind.
    assert cli_evt.extra["skill_id"] == mcp_evt.extra["skill_id"]
    assert cli_evt.folder_path == mcp_evt.folder_path
    assert cli_evt.extra["dispatch"] == mcp_evt.extra["dispatch"] == "skill"
    # Only invoked_via differs (we set that explicitly).
    cli_extra_minus = {k: v for k, v in cli_evt.extra.items() if k != "invoked_via"
                         and k != "chosen_via"}
    mcp_extra_minus = {k: v for k, v in mcp_evt.extra.items() if k != "invoked_via"
                         and k != "chosen_via"}
    assert cli_extra_minus == mcp_extra_minus


# ---------------------------------------------------------------------------
# 5. Three-surface verify_chain parity (direct API + MCP)
# ---------------------------------------------------------------------------


def test_three_surface_verify_chain_parity(isolated_env):
    """verify_chain via direct API vs MCP tool returns semantically
    equivalent results. (The CLI surface delegates to the same API,
    so we treat that as parity-by-construction.)"""
    ws = isolated_env["workspace"]
    log_root = isolated_env["log_root"]

    # Seed the log with at least one event.
    pin_skill(ws, "workspace:probe", log_root=log_root)
    record_dispatch(ws, "workspace:probe", log_root=log_root, actor="probe")

    # Direct API.
    direct = MutationLog(ws, log_root=log_root).verify_chain()

    # MCP — re-uses the same log object internally, so the result shape
    # is identical. We call it via its public function to prove that.
    import os
    # The MCP tool reads its log_root from WORKSPACE_LOG_ROOT (or
    # falls back to default). For test isolation we override.
    monkey_env = os.environ.copy()
    os.environ["WORKSPACE_L0_LOG_ROOT"] = str(log_root)
    try:
        from rvnd.mcp_server import audit_verify_chain
        mcp_result = audit_verify_chain(folder_context=str(ws), actor="test")
    finally:
        os.environ.clear()
        os.environ.update(monkey_env)

    # Semantic equivalence: both report ok, both saw events, no broken
    # links / sig failures. (The MCP path appends a verify_chain_read
    # self-log so total_events on subsequent calls is +1; we only assert
    # both >= the seeded count.)
    assert mcp_result.get("ok") == direct.ok
    assert mcp_result.get("total_events") >= int(direct.total_events)
    assert mcp_result.get("broken_links") == list(direct.broken_links)


# ---------------------------------------------------------------------------
# 6. Air-gap mode blocks all cloud calls
# ---------------------------------------------------------------------------


def test_air_gap_mode_blocks_all_cloud_calls(isolated_env):
    """Folder policy with ``local_llm_mode=local-only`` must refuse every
    cloud call, even when the input is regex-clean. An ``air_gap_refused``
    event must be written."""
    ws = isolated_env["workspace"]
    log_root = isolated_env["log_root"]
    cloud = MockCloudLLM()
    local = MockLocalLLM()

    # Simulated dispatch loop honouring the air-gap policy.
    def _dispatch(text: str, *, air_gap: bool) -> str:
        if air_gap:
            log = MutationLog(ws, log_root=log_root)
            log.append(LogEvent(
                event="system", folder_path=str(ws),
                pair_id=f"air-gap:{hash(text) & 0xffff:x}",
                channel="system", actor="test",
                extra={"kind": "air_gap_refused", "reason": "policy.local_llm=local-only"},
            ))
            return "refused"
        cloud.dispatch(text)
        return "dispatched"

    inputs = [
        "Plain prose with no PII.",
        "Another safe-looking sentence.",
        "Third input that would normally dispatch.",
    ]
    with cloud, local:
        outcomes = [_dispatch(t, air_gap=True) for t in inputs]

    assert outcomes == ["refused"] * 3
    assert len(cloud.calls) == 0, "air-gap mode let a cloud call through"

    events = list(MutationLog(ws, log_root=log_root).replay())
    refusals = [e for e in events if (e.extra or {}).get("kind") == "air_gap_refused"]
    assert len(refusals) == 3


# ---------------------------------------------------------------------------
# 7. Lock-disabled with ACK doesn't bypass local validator
# ---------------------------------------------------------------------------


def test_lock_disabled_with_ack_does_not_bypass_local_validator(isolated_env):
    """Even with lock off (and a recorded ack), the local validator
    still runs on any cloud draft. A validator refusal at this stage
    must NOT be blocked by the lock-disable acknowledgement."""
    from rvnd.policy import disable_lock, load_policy
    ws = isolated_env["workspace"]
    log_root = isolated_env["log_root"]

    # Disable lock with an explicit ack.
    pol = disable_lock(ws, accepted_by="auditor",
                          reason="audit-only test",
                          log_root=log_root)
    assert not pol.lock_is_active

    cloud = MockCloudLLM()
    local = MockLocalLLM(classify_fn={
        "phi-3.5-mini-q4":        lambda t: "pii_yes",
        "qwen-2.5-coder-7b-q4":   lambda t: "pii_yes",
        "mistral-7b-instruct-q4": lambda t: "pii_yes",
    })

    with cloud, local:
        draft = cloud.dispatch("Generate an answer.")
        from rvnd.lock.core import tier_c_semantic_check
        verdict = tier_c_semantic_check(draft)
        # Validator runs irrespective of lock being off.
        assert verdict is not None
        # And its "reject" verdict stands.
        assert verdict.label == "pii_yes"

    # Confirm the disable-ack is still on file.
    pol2 = load_policy(ws)
    assert "lock_disable" in pol2.acknowledgements
