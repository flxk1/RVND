# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""TEST 1 — workspace-side CAPABILITY: does the machinery work?

Exercises the substrate over the companion's data: the populate mechanism, the
SUPERSEDES/APPLIES_IN edges (the algebra's catalogue connections), the currency
validation, the rule-ND anchoring, and the instruments_in join that reach uses.
This is internal validity — the engine is correct — independent of whether the
underlying legal facts are right (that is TEST 2).
"""

from __future__ import annotations

import pytest

from workspaces import regulatory_population as rp
from workspaces import legal_corpus
from workspaces.legal_connection import Connection
from workspaces.rule_registry import RuleRegistry

if rp.default_csv() is None:
    pytest.skip(
        "instrument corpus not installed — set WORKSPACE_INSTRUMENTS_CSV or place "
        "~/.workspace/instruments.csv (ships with the eu-regulatory-companion)",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def instruments():
    return rp.load_instruments()


def test_loads_the_companion_registry(instruments):
    assert {"31995L0046", "32016R0679", "32016L1148",
            "32022L2555", "32024R1689"} <= set(instruments)


def test_population_grows_per_tranche_and_validates(tmp_path, instruments):
    reg = legal_corpus.EntityRegistry(tmp_path)
    recs = rp.populate_in_tranches(reg, instruments)
    assert [r["name"] for r in recs] == [
        "data-protection", "cybersecurity", "ai-governance",
        "platform-content", "digital-markets", "data-economy"]
    # graph grows monotonically across the digital acquis: 3→6→7→8→9→12
    assert [r["cumulative_entities"] for r in recs] == [3, 6, 7, 8, 9, 12]
    # validation runs after each tranche and flags supersession as it appears
    assert "dpd-95" in recs[0]["validation"]["superseded"]      # after data-protection
    assert "nis1" in recs[1]["validation"]["superseded"]        # after cybersecurity
    assert recs[-1]["validation"]["superseded"] == ["dpd-95", "nis1"]


def test_supersedes_and_applies_in_edges_exist(tmp_path, instruments):
    reg = legal_corpus.EntityRegistry(tmp_path)
    rp.populate_in_tranches(reg, instruments)
    edges = {(e["subject"], e["connection"], e["object"]) for e in reg.edges.values()}
    assert ("gdpr", "supersedes", "dpd-95") in edges            # temporal edge
    assert ("nis2", "supersedes", "nis1") in edges
    assert ("gdpr", "applies_in", "EU") in edges                # catalogue edge


def test_every_instrument_is_primary_law_authority(tmp_path, instruments):
    reg = legal_corpus.EntityRegistry(tmp_path)
    rp.populate_in_tranches(reg, instruments)
    from workspaces.corpus import validate as corpus_validate
    summary = corpus_validate.validate_registry(reg)["summary"]
    primary = set(summary["by_authority"]["primary-law"])
    assert {"gdpr", "ai-act", "nis2", "dsa", "dma", "data-act", "cra"} <= primary
    assert summary["unverified_hosts"] == []                    # all on eur-lex


def test_population_is_idempotent(tmp_path, instruments):
    reg = legal_corpus.EntityRegistry(tmp_path)
    rp.populate_in_tranches(reg, instruments)
    rp.populate_in_tranches(reg, instruments)                   # run twice
    assert len(reg.search(kind="instrument")) == 12            # no duplicates


def test_rule_nd_anchors_a_clause_onto_the_populated_graph(tmp_path, instruments):
    reg = legal_corpus.EntityRegistry(tmp_path)
    rp.populate_in_tranches(reg, instruments)
    rules = RuleRegistry(tmp_path, user="alex")
    r = rules.place_span(
        "The controller shall erase personal data under Regulation (EU) 2016/679.",
        source_document="x.md")
    anchors = {(a["entity"], a["relation"]) for a in r["anchors"]}
    assert ("gdpr", "cites") in anchors and ("EU", "governed_by") in anchors


def test_instruments_in_EU_is_the_reach_join(tmp_path, instruments):
    reg = legal_corpus.EntityRegistry(tmp_path)
    rp.populate_in_tranches(reg, instruments)
    world = reg.to_world_map()
    codes = {i.code for i in world.instruments_in("EU")}
    assert {"gdpr", "ai-act", "nis2"} <= codes                 # the governing-law set
