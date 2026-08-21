# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Problem-solution memory: the recall wire closing the CBR loop.
Detecting an issue token recalls how its SHAPE was solved before — same
local, append-only memory the dispatch seam already reads.

Invariants (written BEFORE the logic):
  R1  recording a solved token persists its fingerprint INCLUDING issue_type,
      and only human-closed outcomes become evidence (inherits case_index)
  R2  recall for a token returns prior solvers ranked by verified evidence;
      issue_type narrows — a different issue type with the same rooms does
      NOT match
  R3  an unseen token recalls nothing (cold start is empty, never an error)
  R4  the loop is real: detect → record(solve) → detect again → recall
      returns the recorded solver
  R5  the excerpt is minimised before persistence (alignment requirement):
      a recorded token carries its fingerprint, but the stored question is
      run through the lock minimiser, not stored raw
"""
from __future__ import annotations

import os

import pytest

from rvnd.issue_token import IssueToken, Span, detect_issues
from rvnd.case_index import record_token_case, recall_for_token

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def _tok(itype="liability_cap", rooms=("§ 309 Nr. 7 BGB",), text="cap clause"):
    return IssueToken(issue_id=f"{itype}@0", issue_type=itype, modality="text",
                      span=Span("text", start=0, end=10),
                      norm_anchors=list(rooms), source="c.txt", text=text)


def test_record_token_persists_fingerprint_with_issue_type(env):   # R1
    rid = record_token_case(env["ws"], _tok(), outcome="ratified",
                            actor="alex", rationale="cap is enforceable",
                            solver="skill:liability-nd", log_root=env["lr"])
    assert rid
    hits = recall_for_token(env["ws"], _tok(), log_root=env["lr"])
    assert hits and hits[0]["solver"] == "skill:liability-nd"


def test_recall_narrows_by_issue_type(env):                        # R2
    # same rooms, different issue type → must NOT be recalled
    record_token_case(env["ws"], _tok(itype="warranty",
                                      rooms=("§ 309 Nr. 7 BGB",)),
                      outcome="ratified", actor="alex", rationale="r",
                      solver="skill:warranty-nd", log_root=env["lr"])
    hits = recall_for_token(env["ws"], _tok(itype="liability_cap",
                                            rooms=("§ 309 Nr. 7 BGB",)),
                            log_root=env["lr"])
    assert all(h["solver"] != "skill:warranty-nd" for h in hits)


def test_cold_start_recalls_nothing(env):                          # R3
    assert recall_for_token(env["ws"], _tok(), log_root=env["lr"]) == []


def test_open_token_records_but_is_not_recalled_as_evidence(env):  # R1 cont.
    record_token_case(env["ws"], _tok(), outcome="open", actor="alex",
                      solver="skill:x", log_root=env["lr"])
    assert recall_for_token(env["ws"], _tok(), log_root=env["lr"]) == []


def test_detect_record_detect_recall_loop(env):                    # R4
    snippet = ("5. Liability. The Provider's total liability shall be capped "
               "at the fees paid in the preceding 12 months.\n")
    first = detect_issues(snippet, domain="contract-de")
    assert first and first[0].issue_type == "liability_cap"
    # nothing known yet
    assert recall_for_token(env["ws"], first[0], log_root=env["lr"]) == []
    # solve + retain
    record_token_case(env["ws"], first[0], outcome="ratified", actor="alex",
                      rationale="reviewed, cap acceptable",
                      solver="skill:liability-nd", log_root=env["lr"])
    # a fresh detection of the same shape now recalls the solution
    again = detect_issues(snippet, domain="contract-de")[0]
    hits = recall_for_token(env["ws"], again, log_root=env["lr"])
    assert hits and hits[0]["solver"] == "skill:liability-nd"
    assert hits[0]["evidence"] == 1


def test_excerpt_is_minimised_before_persistence(env):             # R5
    tok = _tok(text="Liability of Herr Jürgen Weber, jw\x40acme.de, is capped")
    record_token_case(env["ws"], tok, outcome="ratified", actor="alex",
                      rationale="r", solver="skill:liability-nd",
                      log_root=env["lr"])
    from rvnd.case_index import solves_edges
    edge = solves_edges(env["ws"], log_root=env["lr"])[0]
    stored = edge.get("question", "")
    # the raw e-mail must not survive into local memory verbatim
    assert "jw\x40acme.de" not in stored
