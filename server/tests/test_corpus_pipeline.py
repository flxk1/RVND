# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The corpus ingest bridge (extraction → registry) and the validation pass."""

from __future__ import annotations

import json

from workspaces.corpus import ingest as corpus_ingest
from workspaces.corpus import validate as corpus_validate
from workspaces import legal_corpus
from workspaces.graph_export import export_graph


# ── CELEX → ELI synthesis ─────────────────────────────────────────────────────

def test_celex_to_eli():
    assert corpus_ingest.celex_to_eli("32016R0679") == \
        "https://eur-lex.europa.eu/eli/reg/2016/679/oj"
    assert corpus_ingest.celex_to_eli("32022L2555") == \
        "https://eur-lex.europa.eu/eli/dir/2022/2555/oj"
    assert corpus_ingest.celex_to_eli("garbage") is None


# ── extraction → corpus ───────────────────────────────────────────────────────

_DOC = """
This Regulation (the AI Act, Regulation (EU) 2024/1689) applies without prejudice
to Regulation (EU) 2016/679 (GDPR). It also interacts with the Digital Services
Act and CELEX 32022R1925.
"""


def test_candidates_from_text_recognises_instruments_with_urls():
    cands = {c["code"]: c for c in corpus_ingest.candidates_from_text(_DOC)}
    assert "gdpr" in cands and "ai-act" in cands
    assert cands["gdpr"]["url"] == "https://eur-lex.europa.eu/eli/reg/2016/679/oj"
    assert cands["gdpr"]["kind"] == "instrument"
    assert "data" in cands["gdpr"]["domains"]


def test_ingest_document_grows_the_corpus(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    out = corpus_ingest.ingest_document(reg, _DOC)
    assert "gdpr" in out["found"] and out["created"] >= 2
    # persisted + retrievable
    reg2 = legal_corpus.EntityRegistry(tmp_path)
    urls = {u["code"]: u["url"] for u in reg2.urls()}
    assert urls.get("gdpr", "").startswith("https://eur-lex.europa.eu/eli/")


def test_ingest_document_is_idempotent(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    corpus_ingest.ingest_document(reg, _DOC)
    second = corpus_ingest.ingest_document(reg, _DOC)
    assert second["created"] == 0          # nothing new the second time


# ── canonical URN spine ───────────────────────────────────────────────────────

def _by_code(reg, code):
    return next(r for r in reg.entities.values() if r["code"] == code)


def test_ingest_stamps_a_celex_canonical_urn(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    corpus_ingest.ingest_document(reg, _DOC)
    # the recognised instrument carries its CELEX, so the canonical URN is CELEX-rooted
    assert _by_code(reg, "gdpr")["canonical_urn"] == "urn:lg:celex:32016r0679"


def test_entity_without_a_strong_id_gets_a_source_urn(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    reg.ingest_entity(code="mystery", name="Mystery Body", kind="regulator")
    assert _by_code(reg, "mystery")["canonical_urn"] == "urn:lg:source:mystery"


def test_a_stronger_identifier_upgrades_the_canonical_urn(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    reg.ingest_entity(code="ai-act", name="AI Act", kind="instrument")
    assert _by_code(reg, "ai-act")["canonical_urn"] == "urn:lg:source:ai-act"
    out = reg.ingest_entity(code="ai-act", name="AI Act", kind="instrument",
                            ids={"celex": "32024R1689"})
    assert out["status"] == "updated"
    assert _by_code(reg, "ai-act")["canonical_urn"] == "urn:lg:celex:32024r1689"


def test_a_non_celex_namespace_is_first_class(tmp_path):
    # CELEX is one namespace, not exclusive: a case-law (ECLI) identifier keys
    # an entity just the same, with no code path special to any scheme
    reg = legal_corpus.EntityRegistry(tmp_path)
    reg.ingest_entity(code="bgh-2024-1", name="BGH ruling", kind="instrument",
                      ids={"ecli": "ECLI:DE:BGH:2024:0101"})
    assert _by_code(reg, "bgh-2024-1")["canonical_urn"] == "urn:lg:ecli:ecli-de-bgh-2024-0101"


def test_a_legacy_record_gains_its_canonical_urn_on_load(tmp_path):
    corpus = tmp_path / "legal-corpus"
    corpus.mkdir(parents=True)
    (corpus / "entities.jsonl").write_text(json.dumps({
        "code": "gdpr", "name": "GDPR", "kind": "instrument",
        "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
        "celex": "32016R0679", "domains": [], "source": "seed",
        "facets": {}, "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-01-01T00:00:00+00:00"}) + "\n", encoding="utf-8")
    reg = legal_corpus.EntityRegistry(tmp_path)
    assert _by_code(reg, "gdpr")["canonical_urn"] == "urn:lg:celex:32016r0679"
    # the heal persisted: a fresh open finds it already current
    assert legal_corpus.EntityRegistry(tmp_path).entities and \
        "urn:lg:celex:32016r0679" in (tmp_path / "legal-corpus" / "entities.jsonl").read_text()


def test_canonical_urn_reaches_the_graph_export(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    reg.ingest_entity(code="gdpr", name="GDPR", kind="instrument",
                      ids={"celex": "32016R0679"})
    g = export_graph(tmp_path)
    gdpr = next(n for n in g["nodes"] if n["id"] == "gdpr")
    assert gdpr["canonical_urn"] == "urn:lg:celex:32016r0679"


def test_ids_for_code_resolves_known_instruments_only():
    assert corpus_ingest.ids_for_code("gdpr") == {"celex": "32016R0679"}
    assert "celex" in corpus_ingest.ids_for_code("dga")   # via the code alias
    assert corpus_ingest.ids_for_code("eprivacy") == {}   # not in the catalogue
    assert corpus_ingest.ids_for_code("bdsg") == {}       # in catalogue, no CELEX


def test_seed_stamps_known_instruments_from_the_start(tmp_path):
    reg = legal_corpus.seed_registry(tmp_path, enriched=False)
    # a seeded work the catalogue holds an identifier for is namespace-addressed
    assert _by_code(reg, "gdpr")["canonical_urn"] == "urn:lg:celex:32016r0679"
    # a work it knows no identifier for stays on its neutral source key
    assert _by_code(reg, "eprivacy")["canonical_urn"] == "urn:lg:source:eprivacy"


def test_register_into_corpus_never_raises(tmp_path):
    # even on nonsense input it returns a dict, never throws into the caller
    out = corpus_ingest.register_into_corpus(str(tmp_path), "no laws here")
    assert isinstance(out, dict)


# ── validation pass ───────────────────────────────────────────────────────────

def test_validate_flags_superseded_and_tiers_authority(tmp_path):
    reg = legal_corpus.seed_registry(tmp_path)
    report = corpus_validate.validate_registry(reg)
    s = report["summary"]
    # the 1995 Directive and NIS1 are superseded but still in the corpus
    assert "dpd-95" in s["superseded"] and "nis1" in s["superseded"]
    # instruments on EUR-Lex are primary law; standards bodies are supporting
    assert "gdpr" in s["by_authority"]["primary-law"]
    assert "iso" in s["by_authority"]["supporting"]
    assert "edpb" in s["by_authority"]["institutional"]


def test_validate_flags_missing_url_and_unverified_host(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    reg.ingest_entity(code="mystery", name="Mystery Body", kind="regulator")  # no url
    reg.ingest_entity(code="sketchy", name="Sketchy", kind="instrument",
                      url="https://random-blog.example/law")
    report = corpus_validate.validate_registry(reg)
    s = report["summary"]
    assert "mystery" in s["missing_url"]
    assert "sketchy" in s["unverified_hosts"]


def test_live_probe_hook_marks_unreachable(tmp_path):
    reg = legal_corpus.EntityRegistry(tmp_path)
    reg.ingest_entity(code="gdpr", name="GDPR", kind="instrument",
                      url="https://eur-lex.europa.eu/eli/reg/2016/679/oj")
    # injected probe that says everything is down → reachability=unreachable
    report = corpus_validate.validate_registry(reg, probe=lambda url: False)
    f = next(f for f in report["findings"] if f["code"] == "gdpr")
    assert f["reachability"] == "unreachable"
    # and a probe that says up → reachable
    report2 = corpus_validate.validate_registry(reg, probe=lambda url: True)
    f2 = next(f for f in report2["findings"] if f["code"] == "gdpr")
    assert f2["reachability"] == "reachable"
