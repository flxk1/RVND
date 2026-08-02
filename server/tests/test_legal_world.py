# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The worldwide legal-entity map: the connection algebra, the reach computation,
the digital-law seed corpus, the ingest path, and the 5D projection.
"""

from __future__ import annotations

from workspaces import legal_connection as lc
from workspaces.legal_connection import Connection, ESCALATE
from workspaces import legal_world as lw
from workspaces.legal_world import Entity, EntityKind
from workspaces import legal_corpus
from workspaces.dimensions import Dimension
from workspaces import reasoning


# ── the algebra ───────────────────────────────────────────────────────────────

def test_incorporated_in_member_state_yields_subject_to_union():
    assert lc.compose(Connection.INCORPORATED_IN, Connection.MEMBER_OF) is Connection.SUBJECT_TO


def test_targeting_a_market_yields_subjection():
    # GDPR Art 3(2): a non-EU person targeting an EU market is subject to EU law
    assert lc.compose(Connection.TARGETS, Connection.MEMBER_OF) is Connection.SUBJECT_TO


def test_treaty_party_does_not_auto_reach_a_private_party():
    assert lc.compose(Connection.INCORPORATED_IN, Connection.PARTY_TO) is ESCALATE


def test_corporate_group_reach_escalates():
    assert lc.compose(Connection.CONTROLS, Connection.SUBJECT_TO) is ESCALATE


def test_path_fold_and_escalation_stickiness():
    res, esc = lc.compose_path([Connection.INCORPORATED_IN, Connection.MEMBER_OF])
    assert res is Connection.SUBJECT_TO and esc is False
    res2, esc2 = lc.compose_path([Connection.CONTROLS, Connection.SUBJECT_TO])
    assert esc2 is True


def test_every_connection_has_a_dimension():
    for c in Connection:
        assert isinstance(lc.dimension(c), Dimension)


# ── reach over the seed ───────────────────────────────────────────────────────

def test_reach_company_in_germany_is_governed_by_eu_with_instruments():
    w = lw.seed_world()
    w.add(Entity("acme-gmbh", "ACME GmbH", EntityKind.LEGAL_PERSON, jurisdiction="DE"))
    w.connect("acme-gmbh", Connection.INCORPORATED_IN, "DE", basis="HRB registration")
    res = w.reach("acme-gmbh")
    govs = {g.jurisdiction: g for g in res.governed_by}
    assert "EU" in govs                                   # reached via DE member_of EU
    assert govs["EU"].relation == "subject_to"
    inst_codes = {i["code"] for i in govs["EU"].instruments}
    assert {"gdpr", "ai-act", "dsa"} <= inst_codes        # the acquis applies
    assert all(i["url"] for i in govs["EU"].instruments)  # retrievable URLs present


def test_reach_us_company_targeting_eu_is_subject_via_targeting():
    w = lw.seed_world()
    w.add(Entity("ustech", "US Tech Inc", EntityKind.LEGAL_PERSON, jurisdiction="US"))
    w.connect("ustech", Connection.TARGETS, "DE", basis="offers services to DE users")
    res = w.reach("ustech")
    govs = {g.jurisdiction: g for g in res.governed_by}
    assert "EU" in govs and govs["EU"].relation == "subject_to"


def test_reach_marks_contested_group_chain_as_escalate():
    w = lw.seed_world()
    w.add(Entity("parentco", "Parent Co", EntityKind.LEGAL_PERSON))
    w.add(Entity("subco", "Sub Co", EntityKind.LEGAL_PERSON, jurisdiction="DE"))
    w.connect("parentco", Connection.CONTROLS, "subco")
    w.connect("subco", Connection.INCORPORATED_IN, "DE")
    res = w.reach("parentco")
    eu = [g for g in res.governed_by if g.jurisdiction == "EU"]
    assert eu and eu[0].escalated      # parent's reach via control is not auto-asserted


# ── the seed corpus shape ─────────────────────────────────────────────────────

def test_seed_covers_the_digital_stack_with_urls():
    w = lw.seed_world()
    insts = {e.code for e in w.search(kind=EntityKind.INSTRUMENT)}
    assert {"gdpr", "ai-act", "dsa", "dma", "nis2", "cra", "data-act"} <= insts
    # every instrument and regulator carries a retrievable URL
    for e in w.search(kind=EntityKind.INSTRUMENT):
        assert e.url and e.url.startswith("http")
    regs = {e.code for e in w.search(kind=EntityKind.REGULATOR)}
    assert {"edpb", "ai-office", "enisa"} <= regs


def test_search_by_domain():
    w = lw.seed_world()
    data_entities = {e.code for e in w.search(domain="data")}
    assert "gdpr" in data_entities and "edpb" in data_entities
    assert "dma" not in data_entities      # dma is digital-markets, not data


def test_regulator_enforces_instrument_edge():
    w = lw.seed_world()
    assert any(ed.subject == "ai-office" and ed.connection is Connection.ENFORCES
               and ed.object == "ai-act" for ed in w.edges)


# ── ingest path + persistence ─────────────────────────────────────────────────

def test_ingest_is_idempotent_and_stamps_provenance(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    r1 = reg.ingest_entity(code="bfdi", name="BfDI", kind="regulator",
                           url="https://www.bfdi.bund.de", jurisdiction="DE",
                           domains=["data"], source="user")
    assert r1["status"] == "created" and r1["source"] == "user"
    r2 = reg.ingest_entity(code="bfdi", name="BfDI", kind="regulator",
                           url="https://www.bfdi.bund.de", jurisdiction="DE",
                           domains=["data"])
    assert r2["status"] == "unchanged"          # no duplicate
    # a second registry reading the same folder sees exactly one bfdi
    reg2 = legal_corpus.EntityRegistry(tmp_path)
    assert len(reg2.search(kind="regulator")) == 1


def test_ingest_updates_fill_blanks_and_widen_domains(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    reg.ingest_entity(code="enisa", name="ENISA", kind="regulator", domains=["cyber"])
    r = reg.ingest_entity(code="enisa", name="ENISA", kind="regulator",
                          url="https://www.enisa.europa.eu", domains=["governance"])
    assert r["status"] == "updated"
    assert r["url"] == "https://www.enisa.europa.eu"
    assert set(r["domains"]) == {"cyber", "governance"}


def test_ingest_from_extraction_hook(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    out = reg.ingest_from_extraction([
        {"code": "cnil", "name": "CNIL", "kind": "regulator",
         "url": "https://www.cnil.fr", "domains": ["data"]},
        {"code": "garbage", "kind": "not_a_kind"},      # rejected, not crash
    ], source="ingest")
    assert out["created"] == 1 and out["rejected"] == 1


def test_unknown_connection_rejected(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    try:
        reg.ingest_edge(subject="a", connection="vibes_with", obj="b")
        assert False, "should have raised"
    except ValueError:
        pass


def test_seed_registry_persists_and_exposes_urls(tmp_path):
    reg = legal_corpus.seed_registry(tmp_path)
    urls = reg.urls()
    codes = {u["code"] for u in urls}
    assert {"gdpr", "ai-act", "edpb", "iso"} <= codes
    assert all(u["url"].startswith("http") for u in urls)
    # the persisted corpus round-trips into a reach-capable WorldMap
    w = reg.to_world_map()
    w.add(Entity("acme", "ACME", EntityKind.LEGAL_PERSON))
    w.connect("acme", Connection.INCORPORATED_IN, "DE")
    assert any(g.jurisdiction == "EU" for g in w.reach("acme").governed_by)


# ── 5D projection ─────────────────────────────────────────────────────────────

def test_projection_is_5d_and_traversable():
    w = lw.seed_world()
    dims = w.dimensions_present()
    # structural (member_of), causal (applies_in), intentional (enforces),
    # temporal (supersedes), relational (equivalent_to)
    assert {Dimension.STRUCTURAL.value, Dimension.CAUSAL.value,
            Dimension.INTENTIONAL.value, Dimension.TEMPORAL.value,
            Dimension.RELATIONAL.value} <= dims
    edges = reasoning.extract_edges(w.project())
    assert edges
    infs = reasoning.compose_paths(edges, start="entity:DE", max_depth=3)
    assert isinstance(infs, list)
