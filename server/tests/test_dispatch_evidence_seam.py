# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Dispatch evidence seam for the problem-solution graph:
`resolve_skills_for_query` consults the case index — pinned skills with
verified solve-evidence rank first; with NO recorded cases the resolution is
IDENTICAL to today's (id-sorted), so the seam cannot regress dispatch.

Invariants (written BEFORE the logic):
  S1  no recorded cases → ordering and content identical to the id-sort,
      every skill annotated evidence=0 (fallback provably unchanged)
  S2  human-closed cases boost: a solver with ratified cases outranks an
      id-earlier skill without; evidence counts are exact
  S3  open cases never boost (evidence = human-closed only, end to end)
  S4  a fingerprint narrows: cases recorded under disjoint rooms do not
      boost for an unrelated problem fingerprint
  S5  the seam is fail-open: an unreadable case chain yields evidence=0,
      never an exception out of skill resolution
"""
from __future__ import annotations

import os

import pytest

from workspaces.case_index import record_case
from workspaces.pinned_skills import pin_skill, resolve_skills_for_query

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    lr = tmp_path / "logs"
    pin_skill(ws, "alpha-skill", log_root=lr)
    pin_skill(ws, "beta-skill", log_root=lr)
    return {"ws": str(ws), "lr": lr}


def _result(rooms=("Art. 33(1)",)):
    return {"case": {"problem": {"text": "q"}, "grounds": [], "chain": [],
                     "gaps": [], "resolution": {"type": "residual"},
                     "profile": "legal-de", "facts": [], "actions": [],
                     "contract": {}, "waivers": []},
            "inputs": {"question": "q", "rooms": list(rooms),
                       "profile": "legal-de"}}


def test_no_cases_resolution_unchanged_and_annotated(env):       # S1
    r = resolve_skills_for_query(env["ws"], log_root=env["lr"])
    ids = [s["id"] for s in r["skills"]]
    assert ids == ["alpha-skill", "beta-skill"]          # today's id-sort
    assert all(s["evidence"] == 0 for s in r["skills"])


def test_ratified_cases_boost_with_exact_counts(env):            # S2
    for _ in range(2):
        record_case(env["ws"], _result(), actor="alex", outcome="ratified",
                    solver="skill:beta-skill", log_root=str(env["lr"]))
    r = resolve_skills_for_query(env["ws"], log_root=env["lr"])
    assert [s["id"] for s in r["skills"]] == ["beta-skill", "alpha-skill"]
    assert r["skills"][0]["evidence"] == 2
    assert r["skills"][1]["evidence"] == 0


def test_open_cases_never_boost(env):                            # S3
    record_case(env["ws"], _result(), actor="alex", outcome="open",
                solver="skill:beta-skill", log_root=str(env["lr"]))
    r = resolve_skills_for_query(env["ws"], log_root=env["lr"])
    assert [s["id"] for s in r["skills"]] == ["alpha-skill", "beta-skill"]
    assert all(s["evidence"] == 0 for s in r["skills"])


def test_fingerprint_narrows_evidence(env):                      # S4
    record_case(env["ws"], _result(rooms=("Art. 33(1)",)), actor="alex",
                outcome="ratified", solver="skill:beta-skill",
                log_root=str(env["lr"]))
    hit = resolve_skills_for_query(env["ws"], log_root=env["lr"],
                                   fingerprint={"rooms": ["Art. 33(1)"]})
    assert hit["skills"][0]["id"] == "beta-skill"
    assert hit["skills"][0]["evidence"] == 1
    miss = resolve_skills_for_query(env["ws"], log_root=env["lr"],
                                    fingerprint={"rooms": ["Art. 99"]})
    assert all(s["evidence"] == 0 for s in miss["skills"])
    assert [s["id"] for s in miss["skills"]] == ["alpha-skill", "beta-skill"]


def test_seam_fails_open_not_loud(env, tmp_path):                # S5
    # a folder with pins but a poisoned chain location must still resolve
    ws2 = tmp_path / "org2"
    ws2.mkdir()
    pin_skill(ws2, "gamma-skill", log_root=env["lr"])
    poisoned = tmp_path / "not-a-dir"
    poisoned.write_text("file, not a log root")
    r = resolve_skills_for_query(ws2, log_root=env["lr"],
                                 case_log_root=str(poisoned))
    assert [s["id"] for s in r["skills"]] == ["gamma-skill"]
    assert r["skills"][0]["evidence"] == 0
