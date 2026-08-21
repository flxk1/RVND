# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""S2 — config-capture coverage (the replay proof).

The design claim is "configs are chain projections, so the chain embed captures
them." This test is the guard that proves it — and it CAUGHT that the claim is
only partly true: policy is a dual-write (chain audit + a state FILE), so it
must travel as an off-chain config file or it's lost. Coverage here:

  chain-projected : parties · connectors · use_cases (+ their reservations)
  file-backed     : policy (lock mode + oversight)

Capture a workspace with every subsystem set, restore into a FRESH folder, and
assert each subsystem's projection is identical. A subsystem that fails to
reconstruct = something governed lives off-chain, uncaptured — a fidelity bug.
"""
from __future__ import annotations


import pytest

from rvnd import connectors, parties, policy, session_io as S, use_case


def _seed_all_subsystems(folder: str, log_root: str) -> None:
    parties.register_party(folder, "alice", "human", competences=["legal"],
                           log_root=log_root)
    parties.register_party(folder, "bot-1", "agent", grade="L2", log_root=log_root)
    connectors.register_connector(folder, connector_id="out-email", role="egress",
                                  channel="email", floor="hold", log_root=log_root)
    use_case.register_use_case(
        folder, use_case_id="uc-1", name="Reply", fingerprint={"issue_type": "reply"},
        risk="high", allowed_agents=["bot-1"], actor="alice",
        policy_reservations={"reply": {"by": "legal", "basis_kind": "law"}},
        log_root=log_root)
    policy.set_lock_mode(folder, "clean_room", accepted_by="alice",
                         reason="test seed", log_root=log_root)
    policy.set_oversight_level(folder, "manual", log_root=log_root)


@pytest.fixture
def captured(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    log_root = str(tmp_path / "logs" / "src")
    _seed_all_subsystems(str(src), log_root)
    doc = S.capture_workspace(str(src), workspace_id="ws1", log_root=log_root)
    return doc, str(src), log_root


def _projections(folder: str, log_root: str) -> dict:
    return {
        "parties": parties.list_parties(folder, log_root=log_root)["parties"],
        "connectors": connectors.list_connectors(folder, log_root=log_root),
        "use_cases": use_case.list_use_cases(folder, log_root=log_root),
        "lock_mode": policy.load_policy(folder).lock_mode,
        "oversight": policy.load_policy(folder).oversight_default_level,
    }


def test_policy_is_captured_as_a_config_file(captured):
    """The finding: policy is file-backed, so the chain embed alone misses it —
    the bundle must carry it in `config`."""
    doc, _, _ = captured
    assert "policy" in doc["config"] and doc["config"]["policy"]


def test_every_subsystem_reconstructs_after_restore(captured, tmp_path):
    doc, src, src_log = captured
    original = _projections(src, src_log)
    # sanity: the seed actually set every subsystem
    assert original["parties"] and original["connectors"] and original["use_cases"]
    assert original["lock_mode"] == "clean_room" and original["oversight"] == "manual"

    dest = str(tmp_path / "dest")
    dest_log = str(tmp_path / "logs" / "dest")
    S.restore_workspace(doc, dest, log_root=dest_log)
    restored = _projections(dest, dest_log)

    # every config subsystem projects identically — chain-projected AND file-backed
    assert restored["parties"] == original["parties"]
    assert restored["connectors"] == original["connectors"]
    assert restored["use_cases"] == original["use_cases"]
    assert restored["lock_mode"] == original["lock_mode"]      # file-backed
    assert restored["oversight"] == original["oversight"]      # file-backed


def test_restored_chain_still_verifies(captured, tmp_path):
    """Restore preserves signatures (verbatim lines) — the restored folder's
    own chain verifies, so provenance survives the round-trip."""
    doc, _, _ = captured
    dest_log = str(tmp_path / "logs" / "dest2")
    S.restore_workspace(doc, str(tmp_path / "dest2"), log_root=dest_log)
    from rvnd.mutation_log import MutationLog
    assert MutationLog(str(tmp_path / "dest2"), log_root=dest_log).verify_chain().ok


def test_config_tamper_is_altered_content(captured):
    """A bundle with policy edited but manifest not updated → fail-closed."""
    doc, _, _ = captured
    rail = {"order": ["ws1"], "focused": "ws1"}
    bundle = S.build_session([doc], rail, name="s", created="2026-06-30T00:00:00Z")
    bundle["workspaces"][0]["config"]["policy"] = '{"lock_mode": "off"}'
    report = S.verify_session(bundle)
    assert not report["ok"]
    assert report["refusal"]["reason"] == S.REFUSAL_ALTERED_CONTENT
    assert "config" in report["refusal"]["detail"]
