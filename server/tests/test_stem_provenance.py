# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Stem provenance round-trip (concept § 1.7, design § 1): chain events in,
authorship evidence out; unknown stems reported, never guessed."""
from __future__ import annotations

import os

import pytest

from workspaces.stem_provenance import assemble_work, authorship_evidence, ingest_stem

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "studio"
    ws.mkdir()
    return {"ws": str(ws), "log_root": str(tmp_path / "logs"),
            "dir": tmp_path}


def _stem(env, name, content):
    f = env["dir"] / name
    f.write_bytes(content)
    return str(f)


def test_round_trip_two_stems_half_and_half(env):
    a = ingest_stem(env["ws"], _stem(env, "drums.wav", b"\x00drums"),
                    "played", actor="alex", log_root=env["log_root"])
    b = ingest_stem(env["ws"], _stem(env, "pad.wav", b"\x00pad"),
                    "generated", tool_id="synthgen-2",
                    actor="alex", log_root=env["log_root"])
    assemble_work(env["ws"], "work-001", [a["stem_hash"], b["stem_hash"]],
                  actor="alex", log_root=env["log_root"])

    ev = authorship_evidence(env["ws"], "work-001",
                             log_root=env["log_root"])
    assert ev["ok"] is True
    assert ev["shares"]["played"] == 0.5
    assert ev["shares"]["generated"] == 0.5
    gen = [s for s in ev["stems"] if s["origin"] == "generated"][0]
    assert gen["tool_id"] == "synthgen-2"
    assert "Not a determination" in ev["statement"]


def test_unknown_stem_reported_not_guessed(env):
    a = ingest_stem(env["ws"], _stem(env, "keys.wav", b"\x00keys"),
                    "played", log_root=env["log_root"])
    assemble_work(env["ws"], "work-002",
                  [a["stem_hash"], "deadbeef" * 8],
                  log_root=env["log_root"])
    ev = authorship_evidence(env["ws"], "work-002",
                             log_root=env["log_root"])
    assert ev["shares"]["unknown"] == 0.5


def test_invalid_origin_refused(env):
    with pytest.raises(ValueError):
        ingest_stem(env["ws"], _stem(env, "x.wav", b"x"), "ai-ish",
                    log_root=env["log_root"])


def test_missing_work_reports_not_found(env):
    ev = authorship_evidence(env["ws"], "nope", log_root=env["log_root"])
    assert ev["ok"] is False and "no WorkAssembled" in ev["reason"]


def test_facade_ops_route(env):
    from workspaces.mcp_server import workspace_conformity, workspace_ingest

    f = _stem(env, "bass.wav", b"\x00bass")
    r = workspace_ingest("stem", {"folder_context": env["ws"], "file_path": f,
                             "origin": "hybrid", "tool_id": "bassbot",
                             "log_root": env["log_root"]})
    assert r["ok"] and r["origin"] == "hybrid"
    r2 = workspace_ingest("assemble_work", {"folder_context": env["ws"],
                                       "work_id": "work-003",
                                       "stem_hashes": [r["stem_hash"]],
                                       "log_root": env["log_root"]})
    assert r2["ok"]
    ev = workspace_conformity("authorship_evidence",
                         {"folder_context": env["ws"],
                          "work_id": "work-003",
                          "log_root": env["log_root"]})
    assert ev["ok"] and ev["shares"]["hybrid"] == 1.0