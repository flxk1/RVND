# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Case index for the problem-solution graph: walker cases as chain
events, solves-edges as a pure replay projection, deterministic facet
retrieval.

Invariants under test (written BEFORE the logic):
  I1  recording never mutates the case payload
  I2  outcome vocabulary is closed — {ratified, decided, open}; anything
      else is refused and NOTHING is appended
  I3  only human-closed outcomes (ratified/decided) become solves-edges;
      open cases are recorded but yield no edge
  I4  facet matching is conservative (applicability doctrine): an UNSET
      facet on a stored edge never excludes; a set-and-disjoint facet does
  I5  retrieval ranks by verified evidence count per configuration
  I6  the projection is pure and additive — same chain, same result;
      appending changes results only additively
  I7  every solves-edge carries its receipt (the chain audit id)
  I8  the REAL walker in deterministic mode (no model) produces an OPEN
      case that records but yields no edge
"""
from __future__ import annotations

import copy
import os

import pytest

from rvnd.case_index import (
    case_fingerprint, record_case, retrieve, solves_edges,
)

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "org"
    ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def _result(rooms=("Art. 33(1)",), profile="legal-de", restype="residual",
            question="breach notification deadline?"):
    """A walker-shaped result dict (case already to_dict()-ed)."""
    return {
        "case": {
            "problem": {"text": question, "document": "", "pinpoint": ""},
            "grounds": [], "chain": [], "gaps": [],
            "resolution": {"type": restype},
            "coverage": 1.0, "facts": [], "actions": [],
            "profile": profile, "contract": {}, "waivers": [],
        },
        "inputs": {"question": question, "rooms": list(rooms),
                   "profile": profile, "stake": False, "personal": False},
    }


# ── I1 + I7: record is non-destructive and receipted ─────────────────────────

def test_record_returns_receipt_and_never_mutates_case(env):
    res = _result()
    before = copy.deepcopy(res)
    audit_id = record_case(env["ws"], res, actor="alex", outcome="ratified",
                           solver="walker:legal-de", log_root=env["lr"])
    assert audit_id
    assert res == before                                   # I1
    edges = solves_edges(env["ws"], log_root=env["lr"])
    assert len(edges) == 1 and edges[0]["receipt"] == audit_id   # I7


# ── I2: closed outcome vocabulary, refusal appends nothing ───────────────────

def test_outcome_vocabulary_closed_and_failclosed(env):
    with pytest.raises(ValueError):
        record_case(env["ws"], _result(), actor="alex", outcome="solved",
                    log_root=env["lr"])
    with pytest.raises(ValueError):
        record_case(env["ws"], _result(), actor="alex", outcome="",
                    log_root=env["lr"])
    assert solves_edges(env["ws"], log_root=env["lr"]) == []


# ── I3: evidence = human-closed only ─────────────────────────────────────────

def test_open_case_records_but_yields_no_edge(env):
    record_case(env["ws"], _result(), actor="alex", outcome="open",
                log_root=env["lr"])
    assert solves_edges(env["ws"], log_root=env["lr"]) == []
    record_case(env["ws"], _result(), actor="alex", outcome="decided",
                log_root=env["lr"])
    assert len(solves_edges(env["ws"], log_root=env["lr"])) == 1


# ── fingerprint: deterministic, conservative ─────────────────────────────────

def test_fingerprint_is_deterministic_and_conservative():
    res = _result(rooms=("Art. 17(1)", "Art. 17(3)"))
    fp1, fp2 = case_fingerprint(res), case_fingerprint(res)
    assert fp1 == fp2
    assert fp1["profile"] == "legal-de"
    assert fp1["rooms"] == ["Art. 17(1)", "Art. 17(3)"]    # sorted
    # facets it cannot read stay UNSET, never guessed
    bare = case_fingerprint({"case": {}, "inputs": {}})
    assert "rooms" not in bare or bare["rooms"] == []


# ── I4: conservative matching ────────────────────────────────────────────────

def test_unset_stored_facet_never_excludes_disjoint_set_does(env):
    record_case(env["ws"], _result(rooms=()), actor="alex",
                outcome="ratified", solver="generalist", log_root=env["lr"])
    record_case(env["ws"], _result(rooms=("Art. 33(1)",)), actor="alex",
                outcome="ratified", solver="breach-nd", log_root=env["lr"])
    record_case(env["ws"], _result(rooms=("Art. 99",), profile="legal-de"),
                actor="alex", outcome="ratified", solver="ai-act-nd",
                log_root=env["lr"])

    hits = retrieve(env["ws"], {"profile": "legal-de",
                                "rooms": ["Art. 33(1)"]}, log_root=env["lr"])
    solvers = [h["solver"] for h in hits]
    assert "breach-nd" in solvers          # set + overlapping → match
    assert "generalist" in solvers         # stored unset → never excluded
    assert "ai-act-nd" not in solvers      # set + disjoint → excluded


# ── I5: evidence-ranked retrieval ────────────────────────────────────────────

def test_retrieval_ranks_by_verified_evidence(env):
    for _ in range(2):
        record_case(env["ws"], _result(), actor="alex", outcome="ratified",
                    solver="veteran", log_root=env["lr"])
    record_case(env["ws"], _result(), actor="alex", outcome="ratified",
                solver="newcomer", log_root=env["lr"])
    record_case(env["ws"], _result(), actor="alex", outcome="open",
                solver="newcomer", log_root=env["lr"])   # open ≠ evidence

    hits = retrieve(env["ws"], {"profile": "legal-de",
                                "rooms": ["Art. 33(1)"]}, log_root=env["lr"])
    assert hits[0]["solver"] == "veteran" and hits[0]["evidence"] == 2
    assert hits[1]["solver"] == "newcomer" and hits[1]["evidence"] == 1


# ── I6: pure + additive projection ───────────────────────────────────────────

def test_projection_pure_and_additive(env):
    record_case(env["ws"], _result(), actor="alex", outcome="ratified",
                log_root=env["lr"])
    a = solves_edges(env["ws"], log_root=env["lr"])
    b = solves_edges(env["ws"], log_root=env["lr"])
    assert a == b                                          # pure
    record_case(env["ws"], _result(rooms=("Art. 17(1)",)), actor="alex",
                outcome="decided", log_root=env["lr"])
    c = solves_edges(env["ws"], log_root=env["lr"])
    assert len(c) == 2 and c[0] == a[0]                    # additive


# ── I8: the real walker, deterministic mode ──────────────────────────────────

def test_real_walker_open_case_records_without_edge(env, tmp_path):
    from rvnd import legal_corpus, reasoning_walker as rw
    from rvnd.rule_registry import RuleRegistry

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    legal_corpus.seed_registry(corpus_dir)
    reg = RuleRegistry(corpus_dir, user="alex")
    reg.place_legal_text(
        "REGULATION (EU) 2016/679 (General Data Protection Regulation)\n"
        "Article 33\n1. The controller shall notify the personal data breach "
        "to the supervisory authority within 72 hours.", "gdpr",
        source_document="gdpr.txt")

    result = rw.walk("When must we notify a data breach?", registry=reg)
    assert result["case"] is not None
    assert result["case"].resolution["type"] != "determinate"   # no judge

    payload = {"case": result["case"].to_dict(), "inputs": result["inputs"]}
    fp = case_fingerprint(payload)
    assert fp["profile"] == "legal-de"

    record_case(env["ws"], payload, actor="alex", outcome="open",
                log_root=env["lr"])
    assert solves_edges(env["ws"], log_root=env["lr"]) == []    # I3 end-to-end
