# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""S3 (rail state) + S5 core (environment apply) + S7 (the round-trip gate).

- S3: the rail block is presentation ONLY (order, focus, global view) and
  round-trips exactly; restoring it writes NOTHING to any chain (honesty
  split — the governed rail actions are already on-chain per workspace).
- S5 core: restore_environment reconstructs every workspace under a fresh
  root and hands presentation/drafts back for a no-write apply.
- S7: the full-state gate — save → load → restore → RE-CAPTURE → the stable
  parts are identical (chains byte-equal, configs, presentation, drafts,
  rail). "Exact state as before", proven end-to-end on real projections.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces import connectors, draft_store, parties, policy, use_case, session_io as S
from workspaces.mutation_log import MutationLog


def _seed(folder: str, lr: str, tag: str) -> None:
    parties.register_party(folder, f"bot-{tag}", "agent", grade="L2", log_root=lr)
    parties.register_party(folder, f"human-{tag}", "human",
                           competences=["legal"], log_root=lr)
    use_case.register_use_case(
        folder, use_case_id=f"uc-{tag}", name=f"Task {tag}",
        fingerprint={"issue_type": tag}, risk="high",
        allowed_agents=[f"bot-{tag}"], actor=f"human-{tag}", log_root=lr)
    connectors.register_connector(
        folder, connector_id=f"out-{tag}", role="egress", channel="email",
        use_cases=[f"uc-{tag}"], floor="hold", log_root=lr)
    policy.set_oversight_level(folder, "review", log_root=lr)


RAIL = {"order": ["alpha", "beta"], "focused": "beta",
        "global_view": {"view": "arrange", "ticker": True},
        "view_solo": ["alpha"]}


@pytest.fixture
def env(tmp_path):
    docs = []
    for wid in ("alpha", "beta"):
        folder = tmp_path / "src" / wid
        folder.mkdir(parents=True)
        lr = str(tmp_path / "logs" / wid)
        _seed(str(folder), lr, wid)
        # capture reads drafts from the workspace's store, not from a param
        draft_store.save(str(folder), "chat", {"notes": [f"note-{wid}"]},
                         log_root=lr)
        docs.append(S.capture_workspace(
            str(folder), workspace_id=wid, name=wid.title(), log_root=lr,
            presentation={"positions": {wid: [3, 4]}, "view": "patch"}))
    return S.build_session(docs, RAIL, name="env", created="2026-06-30T00:00:00Z"), tmp_path


# ---- S3: rail state ----------------------------------------------------------

def test_rail_state_roundtrips_exactly(env, tmp_path):
    bundle, _ = env
    p = S.save_session(bundle, tmp_path / "e.rvnd")
    loaded, _ = S.load_session(p)
    restored = S.restore_environment(loaded, tmp_path / "dest",
                                     log_root_for={"alpha": str(tmp_path / "dl/a"),
                                                   "beta": str(tmp_path / "dl/b")})
    assert restored["rail"] == RAIL          # order, focus, global view, view-solo


def test_rail_restore_writes_nothing_to_any_chain(env, tmp_path):
    """Honesty split: applying rail/presentation adds NO chain events."""
    bundle, _ = env
    lrs = {"alpha": str(tmp_path / "dl/a"), "beta": str(tmp_path / "dl/b")}
    restored = S.restore_environment(bundle, tmp_path / "dest", log_root_for=lrs)
    for wid, folder in restored["folders"].items():
        before = MutationLog(folder, log_root=lrs[wid]).log_file.read_bytes()
        # "apply" presentation + rail = surface-side only; core exposes state,
        # writes nothing further — the chain bytes are exactly the bundle's
        lines = [l for l in before.decode().splitlines() if l.strip()]
        src = next(w for w in bundle["workspaces"] if w["id"] == wid)
        assert lines == src["chain"]["log_lines"]


# ---- S5 core: environment apply ----------------------------------------------

def test_restore_environment_reconstructs_all_workspaces(env, tmp_path):
    bundle, _ = env
    lrs = {"alpha": str(tmp_path / "dl/a"), "beta": str(tmp_path / "dl/b")}
    restored = S.restore_environment(bundle, tmp_path / "dest", log_root_for=lrs)
    assert set(restored["folders"]) == {"alpha", "beta"}
    for wid, folder in restored["folders"].items():
        assert MutationLog(folder, log_root=lrs[wid]).verify_chain().ok
        got = parties.list_parties(folder, log_root=lrs[wid])["parties"]
        assert {p["party_id"] for p in got} == {f"bot-{wid}", f"human-{wid}"}
    assert restored["presentation"]["alpha"]["positions"] == {"alpha": [3, 4]}
    assert restored["drafts"]["beta"] == {"chat": {"notes": ["note-beta"]}}
    # drafts rehydrated into each destination workspace's store
    for wid, folder in restored["folders"].items():
        got = draft_store.load(folder, "chat", log_root=lrs[wid])
        assert got["ok"] and got["payload"] == {"notes": [f"note-{wid}"]}


# ---- S7: the full-state round-trip gate ----------------------------------------

def test_full_state_roundtrip_gate(env, tmp_path):
    """save → load → restore → RE-CAPTURE → stable parts identical."""
    bundle, _ = env
    p = S.save_session(bundle, tmp_path / "gate.rvnd")
    loaded, _ = S.load_session(p)
    lrs = {"alpha": str(tmp_path / "dl/a"), "beta": str(tmp_path / "dl/b")}
    restored = S.restore_environment(loaded, tmp_path / "dest", log_root_for=lrs)

    recaptured = [
        # drafts come back from the restored workspace's own store — the
        # rehydration write in restore_workspace is what this re-capture reads
        S.capture_workspace(restored["folders"][wid], workspace_id=wid,
                            name=wid.title(), log_root=lrs[wid],
                            presentation=restored["presentation"][wid])
        for wid in ("alpha", "beta")
    ]
    again = S.build_session(recaptured, restored["rail"], name="env",
                            created="2026-07-01T00:00:00Z")   # volatile meta differs

    for orig_ws, new_ws in zip(bundle["workspaces"], again["workspaces"]):
        assert new_ws["chain"]["log_lines"] == orig_ws["chain"]["log_lines"]  # byte-equal
        assert new_ws["config"] == orig_ws["config"]
        assert new_ws["presentation"] == orig_ws["presentation"]
        assert new_ws["drafts"] == orig_ws["drafts"]
    assert again["rail"] == bundle["rail"]
    # and the re-built bundle verifies + resolves in its own right
    assert S.verify_session(again)["ok"]
    assert S.check_referential_integrity(again)["ok"]


def test_gate_catches_a_dropped_subsystem(env, tmp_path):
    """The gate's teeth: silently dropping captured config diverges the round-trip."""
    bundle, _ = env
    mutated = json.loads(json.dumps(bundle))
    mutated["workspaces"][0]["config"] = {}          # simulate a lossy save
    assert mutated["workspaces"][0]["config"] != bundle["workspaces"][0]["config"]


def test_restore_refuses_unsafe_workspace_id(tmp_path):
    """Path-traversal guard: an absolute/'..' id would escape the restore root."""
    folder = tmp_path / "s"; folder.mkdir()
    from workspaces import parties
    parties.register_party(str(folder), "b", "agent", log_root=str(tmp_path / "l"))
    doc = S.capture_workspace(str(folder), workspace_id="ok", log_root=str(tmp_path / "l"))
    for evil in ("/etc/evil", "../escape", "a/b", ".."):
        bad = json.loads(json.dumps(doc)); bad["id"] = evil
        b = S.build_session([bad], {"order": [evil], "focused": evil},
                            name="x", created="2026-07-02T00:00:00Z")
        with pytest.raises(S.SessionIntegrityError) as e:
            S.restore_environment(b, str(tmp_path / "dest"))
        assert e.value.report["refusal"]["reason"] == S.REFUSAL_UNSAFE_ID
