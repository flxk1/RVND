# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace Grounder — stress + adversarial tests.

Pins the scale envelope of the rewrite-on-flush ledger with explicit time
budgets, exercises deep/wide/cyclic provenance graphs, and feeds the
formatter and ledger adversarial inputs. Budgets are deliberately loose
(CI machines vary); the point is catching order-of-magnitude regressions,
not micro-benchmarks.

The 1k-op budgets are sized for the SLOWEST plausible target (a passively
cooled laptop on a fresh Python release was observed at ~56s), with headroom
so the test never false-alarms on hardware — yet still trips on a true
order-of-magnitude regression. Override with ``WORKSPACE_STRESS_BUDGET_S`` if you
want a tighter wall on known-fast hardware.
"""

from __future__ import annotations


import pytest

# Stress + scale tests: minutes on slow hardware, and they self-police their own
# time budgets (WORKSPACE_STRESS_BUDGET_S). Kept out of the bounded fast subset,
# and the global per-test timeout is disabled here so it cannot pre-empt a run
# that is legitimately long.
pytestmark = [pytest.mark.slow, pytest.mark.timeout(0)]
import os
import time

import pytest

# 1k-op wall-clock budget. Loose by design (see module docstring): catches an
# order-of-magnitude regression, tolerates slow/passively-cooled hardware.
_BUDGET_1K_S = float(os.environ.get("WORKSPACE_STRESS_BUDGET_S", "240"))

from rvnd.workspace_grounder import (
    CITATION_STYLES,
    GroundingLedger,
    format_citation,
)


@pytest.fixture()
def ledger(tmp_path):
    return GroundingLedger(tmp_path, log_root=tmp_path / "log")


# ── scale: rewrite-on-flush envelope ─────────────────────────────────────────

def test_1k_works_register_and_reload(ledger, tmp_path):
    t0 = time.monotonic()
    ids = []
    for i in range(1000):
        r = ledger.register_work(title=f"Work {i}",
                                 creators=[{"name": f"Author{i}, A."}],
                                 date="2024", url=f"https://x.test/{i}")
        ids.append(r["id"])
    elapsed = time.monotonic() - t0
    assert len(ledger.works) == 1000
    assert len(set(ids)) == 1000
    assert elapsed < _BUDGET_1K_S, f"1k registers took {elapsed:.1f}s (budget {_BUDGET_1K_S:.0f}s)"

    t0 = time.monotonic()
    led2 = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    reload_s = time.monotonic() - t0
    assert len(led2.works) == 1000
    assert reload_s < 2.0, f"reload took {reload_s:.2f}s"


def test_1k_claims_against_shared_works(ledger):
    works = [ledger.register_work(title=f"W{i}", url=f"https://x.test/w{i}")["id"]
             for i in range(20)]
    t0 = time.monotonic()
    for i in range(1000):
        res = ledger.ground_claim(f"Claim number {i}.",
                                  [works[i % len(works)]],
                                  confidence=0.5)
        assert res["status"] == "created"
    elapsed = time.monotonic() - t0
    assert len(ledger.claims) == 1000
    assert elapsed < _BUDGET_1K_S, f"1k claims took {elapsed:.1f}s (budget {_BUDGET_1K_S:.0f}s)"


def test_coverage_and_bibliography_on_large_ledger(ledger):
    for i in range(500):
        ledger.register_work(title=f"W{i}",
                             creators=[{"name": f"Zed{i}, Q."}],
                             date="2023", url=f"https://x.test/{i}")
    t0 = time.monotonic()
    cov = ledger.coverage()
    bib = ledger.bibliography(style="ieee")
    elapsed = time.monotonic() - t0
    assert cov["works"] == 500
    assert bib["count"] == 500
    assert elapsed < 5.0, f"coverage+bibliography took {elapsed:.1f}s"


# ── provenance graphs: deep, wide, cyclic ────────────────────────────────────

def test_deep_chain_trace_bounded(ledger):
    n = 300
    ids = [ledger.register_work(title=f"D{i}", url=f"https://x.test/d{i}")["id"]
           for i in range(n)]
    for i in range(n - 1):
        ledger.add_provenance(ids[i], "cites", ids[i + 1])
    t0 = time.monotonic()
    res = ledger.trace(ids[0], max_depth=50)
    elapsed = time.monotonic() - t0
    assert res["status"] == "ok"
    # depth budget respected: chains stop at max_depth, no blow-up
    assert all(len(c) <= 50 for c in res["chains"])
    assert elapsed < 5.0, f"deep trace took {elapsed:.1f}s"


def test_wide_fanout_trace(ledger):
    root = ledger.register_work(title="Survey", url="https://x.test/root")["id"]
    leaves = [ledger.register_work(title=f"L{i}",
                                   url=f"https://x.test/l{i}")["id"]
              for i in range(200)]
    for leaf in leaves:
        ledger.add_provenance(root, "cites", leaf)
    t0 = time.monotonic()
    res = ledger.trace(root)
    elapsed = time.monotonic() - t0
    assert res["status"] == "ok"
    assert len(res["roots"]) == 200
    assert elapsed < 5.0, f"wide trace took {elapsed:.1f}s"


def test_dense_cyclic_mesh_terminates(ledger):
    n = 30
    ids = [ledger.register_work(title=f"M{i}", url=f"https://x.test/m{i}")["id"]
           for i in range(n)]
    # every node cites the next three, modulo — many cycles
    for i in range(n):
        for j in (1, 2, 3):
            ledger.add_provenance(ids[i], "cites", ids[(i + j) % n])
    t0 = time.monotonic()
    res = ledger.trace(ids[0], max_depth=10)
    elapsed = time.monotonic() - t0
    assert res["status"] == "ok"
    assert elapsed < 10.0, f"cyclic mesh trace took {elapsed:.1f}s"


def test_frontier_on_large_graph(ledger):
    ids = [ledger.register_work(title=f"F{i}", url=f"https://x.test/f{i}")["id"]
           for i in range(500)]
    for i in range(0, 400):
        ledger.add_provenance(ids[i], "cites", ids[i + 100])
    res = ledger.frontier()
    assert res["count"] == 100                       # the last 100 are untraced
    assert res["total_works"] == 500


# ── adversarial inputs ────────────────────────────────────────────────────────

ADVERSARIAL_NAMES = [
    "Œuvre d'Aŭtoro, Ĵan-Pierre",
    "山田 太郎",
    "O'Brien-Søren, Łukasz",
    "A" * 500,
    "  ,  ",
    "Robert'); DROP TABLE works;--",
    "name\nwith\nnewlines",
    "🎵 DJ Provenance 🎵",
]


@pytest.mark.parametrize("name", ADVERSARIAL_NAMES)
def test_adversarial_creator_names_roundtrip(ledger, name):
    r = ledger.register_work(title="T-" + name[:20],
                             creators=[{"name": name}],
                             url=f"https://x.test/{abs(hash(name))}")
    assert r["status"] == "created"
    for style in CITATION_STYLES:
        out = format_citation(ledger.works[r["id"]], style)
        assert isinstance(out, str) and out          # never raises, never empty


def test_huge_claim_text_and_unicode(ledger):
    w = ledger.register_work(title="W", url="https://x.test/w")
    text = ("Ω≈ç√∫ " * 2000) + "end."                # ~12k chars
    res = ledger.ground_claim(text, [w["id"]])
    assert res["status"] == "created"
    led2 = GroundingLedger(ledger.folder, log_root=ledger.log_root)
    assert led2.claims[res["id"]]["text"].endswith("end.")


def test_store_survives_hostile_values_after_stress(ledger, tmp_path):
    # every write persists through the versum sink and reloads intact, even with
    # quote/backslash-injection in titles and creator names (the store is now the
    # folder's versum sink, not a local JSONL file).
    for i in range(50):
        ledger.register_work(title=f'W"{i}\\', url=f"https://x.test/q{i}",
                             creators=[{"name": 'Quote", "injection'}])
    led2 = GroundingLedger(ledger.folder, log_root=ledger.log_root)
    assert len(led2.works) == 50                       # every write reloads


def test_empty_and_whitespace_claims_still_keyed(ledger):
    w = ledger.register_work(title="W", url="https://x.test/w")
    a = ledger.ground_claim("   padded claim   ", [w["id"]])
    b = ledger.ground_claim("padded claim", [w["id"]])
    assert a["id"] == b["id"]                         # whitespace-stable id


# ── ledger invariants under stress ───────────────────────────────────────────

def test_doi_url_cross_match_no_fork(ledger):
    """Same work by URL then by DOI must not fork."""
    a = ledger.register_work(title="Paper", url="https://arxiv.org/abs/1",
                             creators=[{"name": "Doe, J."}])
    b = ledger.register_work(title="Paper (publisher version)",
                             url="https://arxiv.org/abs/1",
                             doi="10.1/xyz")
    assert b["id"] == a["id"]
    c = ledger.register_work(title="Paper, again", doi="10.1/XYZ")  # case-insensitive doi
    assert c["id"] == a["id"]
    assert len(ledger.works) == 1
    assert ledger.works[a["id"]]["doi"] == "10.1/xyz"  # blank filled, not overwritten


def test_evidence_gaps_surface_in_coverage(ledger):
    w = ledger.register_work(title="W", url="https://x.test/w")
    bare = ledger.ground_claim("Bare claim.", [w["id"]])
    ledger.ground_claim("Evidenced claim.", [w["id"]],
                        quote="short quote", locator="p. 3")
    long_q = ledger.ground_claim("Hoarded claim.", [w["id"]],
                                 quote="x" * 400)
    cov = ledger.coverage()
    assert bare["id"] in cov["claims_without_evidence"]
    assert long_q["id"] in cov["overlong_quotes"]
    assert w["id"] in cov["web_works_missing_fixity"]


def test_fixity_clears_coverage_flag(ledger):
    w = ledger.register_work(title="W", url="https://x.test/w",
                             identifiers={"sha256": "ab" * 32})
    assert w["id"] not in ledger.coverage()["web_works_missing_fixity"]


def test_verified_without_evidence_flagged(ledger):
    w = ledger.register_work(title="W", url="https://x.test/w")
    a = ledger.ground_claim("Twin claim.", [w["id"]], method="twin", agent="a")
    ledger.ground_claim("Twin claim.", [w["id"]], method="twin", agent="b")
    rec = ledger.claims[a["id"]]
    assert rec["status"] == "verified"
    assert rec["evidence_at_promotion"] is False
    assert a["id"] in ledger.coverage()["verified_without_evidence"]


def test_twin_promotion_with_evidence_not_flagged(ledger):
    w = ledger.register_work(title="W", url="https://x.test/w")
    a = ledger.ground_claim("Twin claim 2.", [w["id"]], method="twin",
                            agent="a", quote="supporting passage")
    ledger.ground_claim("Twin claim 2.", [w["id"]], method="twin", agent="b")
    rec = ledger.claims[a["id"]]
    assert rec["status"] == "verified"
    assert rec["evidence_at_promotion"] is True
    assert a["id"] not in ledger.coverage()["verified_without_evidence"]


def test_single_writer_rule_documented():
    """The single-writer rule must be stated in the skill."""
    from pathlib import Path
    skill = Path(__file__).resolve().parents[2] / "plugin" / "skills" \
        / "workspace-grounder" / "SKILL.md"
    if not skill.exists():
        import pytest
        pytest.skip("plugin/ ships as a separate companion; SKILL.md not in the core repo")
    assert "single-writer" in skill.read_text(encoding="utf-8")
