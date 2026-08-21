# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""S13 — a track between Sets: export one workspace, import into another env.

Fail-closed on both hazards of a partial slice: a tampered incoming track is
refused (three checks), and an id collision is refused (import never replaces
— replace is a deliberate load action). The result is a NEW child session
(fork-not-rewind) that verifies and resolves in its own right.
"""
from __future__ import annotations

import json

import pytest

from rvnd import connectors, parties, use_case, session_io as S


def _ws(tmp_path, wid: str) -> dict:
    folder = tmp_path / wid
    folder.mkdir()
    lr = str(tmp_path / "logs" / wid)
    parties.register_party(str(folder), f"bot-{wid}", "agent", log_root=lr)
    use_case.register_use_case(
        str(folder), use_case_id=f"uc-{wid}", name=wid, fingerprint={"issue_type": wid},
        risk="medium", allowed_agents=[f"bot-{wid}"], actor=f"bot-{wid}", log_root=lr)
    connectors.register_connector(str(folder), connector_id=f"out-{wid}",
                                  role="egress", channel="email",
                                  use_cases=[f"uc-{wid}"], log_root=lr)
    return S.capture_workspace(str(folder), workspace_id=wid, name=wid.title(),
                               log_root=lr)


@pytest.fixture
def two_sessions(tmp_path):
    src = S.build_session([_ws(tmp_path, "alpha"), _ws(tmp_path, "beta")],
                          {"order": ["alpha", "beta"], "focused": "alpha"},
                          name="source", created="2026-06-30T00:00:00Z")
    dest = S.build_session([_ws(tmp_path, "gamma")],
                           {"order": ["gamma"], "focused": "gamma"},
                           name="target", created="2026-06-30T00:00:00Z")
    return src, dest


def test_exported_track_is_a_valid_session(two_sessions):
    src, _ = two_sessions
    track = S.export_workspace(src, "beta", created="2026-07-01T00:00:00Z")
    assert S.verify_session(track)["ok"]
    assert S.check_referential_integrity(track)["ok"]     # rail names only beta
    assert track["meta"]["parent_version"] == S.bundle_version(src)  # remembers its Set
    assert [w["id"] for w in track["workspaces"]] == ["beta"]


def test_import_appends_and_forks(two_sessions):
    src, dest = two_sessions
    track = S.export_workspace(src, "beta", created="2026-07-01T00:00:00Z")
    merged = S.import_workspace(dest, track, "beta", created="2026-07-01T01:00:00Z")
    assert [w["id"] for w in merged["workspaces"]] == ["gamma", "beta"]
    assert merged["rail"]["order"] == ["gamma", "beta"]
    assert merged["meta"]["parent_version"] == S.bundle_version(dest)  # child of target
    assert S.verify_session(merged)["ok"]
    assert S.check_referential_integrity(merged)["ok"]
    # the incoming track's chain travels byte-verbatim
    src_beta = next(w for w in src["workspaces"] if w["id"] == "beta")
    new_beta = next(w for w in merged["workspaces"] if w["id"] == "beta")
    assert new_beta["chain"]["log_lines"] == src_beta["chain"]["log_lines"]


def test_import_refuses_tampered_track(two_sessions):
    src, dest = two_sessions
    track = S.export_workspace(src, "beta", created="2026-07-01T00:00:00Z")
    obj = json.loads(track["workspaces"][0]["chain"]["log_lines"][0])
    obj["extra"]["kind"] = "HACKED"
    track["workspaces"][0]["chain"]["log_lines"][0] = json.dumps(obj)
    with pytest.raises(S.SessionIntegrityError) as exc:
        S.import_workspace(dest, track, "beta", created="2026-07-01T01:00:00Z")
    assert exc.value.report["refusal"]["reason"] == S.REFUSAL_BROKEN_CHAIN


def test_import_refuses_id_collision(two_sessions):
    src, dest = two_sessions
    track = S.export_workspace(src, "alpha", created="2026-07-01T00:00:00Z")
    collided = S.import_workspace(dest, track, "alpha", created="2026-07-01T01:00:00Z")
    with pytest.raises(S.SessionIntegrityError) as exc:
        S.import_workspace(collided, track, "alpha", created="2026-07-01T02:00:00Z")
    assert "already exists" in exc.value.report["refusal"]["detail"]


def test_export_unknown_workspace_raises(two_sessions):
    src, _ = two_sessions
    with pytest.raises(KeyError):
        S.export_workspace(src, "nope", created="2026-07-01T00:00:00Z")
