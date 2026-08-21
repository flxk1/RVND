# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Finding #3 — adopt a session AS the active environment (registry reconcile).

The active environment is the workspace REGISTRY. Adopting is NON-DESTRUCTIVE:
restore into fresh folders, then rewrite the registry — never delete a folder.
"replace" swaps the registry (old workspaces deregistered, folders kept on
disk, recoverable). Foreign-key sessions are refused (decision B).
"""
from __future__ import annotations



from rvnd import parties, session_io as S, session_mcp as M
from rvnd import workspace_registry as WR


def _bundle(tmp_path, ids, name="env"):
    docs = []
    for wid in ids:
        folder = tmp_path / "src" / wid
        folder.mkdir(parents=True)
        lr = str(tmp_path / "slog" / wid)
        parties.register_party(str(folder), f"bot-{wid}", "agent", log_root=lr)
        docs.append(S.capture_workspace(str(folder), workspace_id=wid,
                                        name=wid.title(), log_root=lr))
    rail = {"order": list(ids), "focused": ids[0]}
    return S.build_session(docs, rail, name=name, created="2026-07-02T00:00:00Z")


def _reg_paths(rlr):
    return {w["path"] for w in WR.load_registry(log_root=rlr).get("workspaces") or []}


def test_adopt_into_empty_registers(tmp_path):
    rlr = str(tmp_path / "reg")
    b = _bundle(tmp_path, ["a", "b"])
    out = M.session_adopt(b, str(tmp_path / "dest"),
                          log_root_for={"a": str(tmp_path / "d/a"), "b": str(tmp_path / "d/b")},
                          registry_log_root=rlr)
    assert out["ok"] and out["mode"] == "replace" and out["retired"] == []
    assert _reg_paths(rlr) == set(out["adopted"].values())


def test_replace_swaps_registry_but_keeps_old_folders(tmp_path):
    rlr = str(tmp_path / "reg")
    # a pre-existing active environment: one registered workspace on disk
    old = tmp_path / "old_ws"; old.mkdir()
    parties.register_party(str(old), "bot-old", "agent", log_root=str(tmp_path / "oldlog"))
    WR.add_known_workspace(str(old), label="Old", log_root=rlr)
    assert str(old.resolve()) in _reg_paths(rlr)

    out = M.session_adopt(_bundle(tmp_path, ["a"]), str(tmp_path / "dest"),
                          log_root_for={"a": str(tmp_path / "d/a")}, registry_log_root=rlr)
    assert out["ok"]
    # registry now holds the adopted set, NOT the old one
    assert _reg_paths(rlr) == set(out["adopted"].values())
    assert str(old.resolve()) in out["retired"]
    # ...but the old folder is NOT destroyed (non-destructive; recoverable)
    assert old.exists()


def test_beside_keeps_current(tmp_path):
    rlr = str(tmp_path / "reg")
    old = tmp_path / "old_ws"; old.mkdir()
    WR.add_known_workspace(str(old), label="Old", log_root=rlr)
    out = M.session_adopt(_bundle(tmp_path, ["a"]), str(tmp_path / "dest"),
                          mode="beside", log_root_for={"a": str(tmp_path / "d/a")},
                          registry_log_root=rlr)
    assert out["ok"] and out["retired"] == []
    assert str(old.resolve()) in _reg_paths(rlr)               # old kept
    assert set(out["adopted"].values()) <= _reg_paths(rlr)     # new added too


def test_adopt_refuses_bad_bundle_atomically(tmp_path):
    """A refused bundle leaves the registry UNTOUCHED (adopt is atomic re: the
    registry — verify runs before any registry write). The foreign-key guard
    itself is covered at the restore level in test_session_continue."""
    import json
    rlr = str(tmp_path / "reg")
    old = tmp_path / "old_ws"; old.mkdir()
    WR.add_known_workspace(str(old), label="Old", log_root=rlr)
    before = _reg_paths(rlr)

    b = _bundle(tmp_path, ["a"])
    obj = json.loads(b["workspaces"][0]["chain"]["log_lines"][0])
    obj["extra"]["kind"] = "HACKED"                             # tamper -> broken_chain
    b["workspaces"][0]["chain"]["log_lines"][0] = json.dumps(obj)

    out = M.session_adopt(b, str(tmp_path / "dest"),
                          log_root_for={"a": str(tmp_path / "d/a")}, registry_log_root=rlr)
    assert out["ok"] is False and out["report"]["refusal"]["reason"] == S.REFUSAL_BROKEN_CHAIN
    assert _reg_paths(rlr) == before                            # registry untouched
