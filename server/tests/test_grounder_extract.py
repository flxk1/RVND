# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Grounder source extraction — metadata tags, model fallback with the
never-invent grounding check, reference parsing, and the swarm's per-page
ingest_source step (local-LLM integrations #2 and #4)."""

from __future__ import annotations

import json

import pytest

from workspaces.workspace_grounder import GroundingLedger
from workspaces.grounder_extract import (
    extract_metadata,
    extract_metadata_tags,
    extract_references,
    ingest_source,
)

PAGE = """<html><head>
<title>Fallback page title</title>
<meta name="citation_title" content="Grounding Language Models">
<meta name="citation_author" content="Doe, Jane">
<meta name="citation_author" content="Roe, Richard">
<meta name="citation_journal_title" content="Journal of Attribution">
<meta name="citation_publisher" content="Open Press">
<meta name="citation_publication_date" content="2024/03/01">
<meta name="citation_doi" content="10.1234/jattr.2024.001">
</head><body>
<p>As shown in prior work (doi:10.5555/earlier.work) and arXiv:1706.03762,
grounding matters. See also https://example.org/related-note for context.</p>
<p>Our own page lives at https://x.test/self/page.</p>
</body></html>"""


# ── deterministic metadata layer ─────────────────────────────────────────────

def test_meta_tags_extracted():
    meta = extract_metadata_tags(PAGE)
    assert meta["title"] == "Grounding Language Models"     # tag beats <title>
    assert [c["name"] for c in meta["creators"]] == ["Doe, Jane",
                                                     "Roe, Richard"]
    assert meta["container"] == "Journal of Attribution"
    assert meta["publisher"] == "Open Press"
    assert meta["date"] == "2024/03/01"
    assert meta["doi"] == "10.1234/jattr.2024.001"


def test_title_tag_fallback():
    meta = extract_metadata_tags("<html><head><title>Only Title</title>"
                                 "</head><body>x</body></html>")
    assert meta["title"] == "Only Title"
    assert "creators" not in meta


def test_no_model_means_pure_deterministic():
    assert extract_metadata(PAGE) == extract_metadata_tags(PAGE)


# ── model fallback with grounding check ──────────────────────────────────────

PLAIN = ("Technical report TR-77: Provenance at Scale. "
         "Written by Ada Lovelace for the Analytical Society, 1843.")


def test_model_fills_only_missing_and_grounded():
    def model_fn(prompt):
        return json.dumps({
            "title": "Provenance at Scale",
            "creators": ["Ada Lovelace", "Charles Babbage"],   # Babbage absent
            "publisher": "Analytical Society",
            "date": "1843",
            "doi": "10.9999/invented"})                        # invented
    meta = extract_metadata(PLAIN, model_fn=model_fn)
    assert meta["title"] == "Provenance at Scale"
    assert meta["creators"] == [{"name": "Ada Lovelace"}]
    assert meta["publisher"] == "Analytical Society"
    assert meta["date"] == "1843"
    assert "doi" not in meta                                   # never invented
    dropped = {d["value"] for d in meta["_dropped"]}
    assert "Charles Babbage" in dropped
    assert "10.9999/invented" in dropped


def test_model_never_overrides_tags():
    def model_fn(prompt):
        return json.dumps({"title": "Fallback page title"})    # grounded, but…
    meta = extract_metadata(PAGE, model_fn=model_fn)
    assert meta["title"] == "Grounding Language Models"        # tags win


def test_model_failure_degrades_to_deterministic():
    def broken(prompt):
        raise RuntimeError("endpoint down")
    meta = extract_metadata(PLAIN, model_fn=broken)
    assert "model_fn failed" in meta["_dropped"][0]["why"]
    assert "doi" not in meta


def test_model_garbage_reply_degrades():
    meta = extract_metadata(PLAIN, model_fn=lambda p: "no json here")
    assert isinstance(meta, dict)
    assert "title" not in meta or meta.get("title")


# ── reference extraction ─────────────────────────────────────────────────────

def test_references_doi_arxiv_url():
    refs = extract_references(PAGE, own_url="https://x.test/self/page")
    dois = {r.get("doi") for r in refs if r.get("doi")}
    urls = {r.get("url") for r in refs if r.get("url")}
    assert "10.5555/earlier.work" in dois
    assert "10.1234/jattr.2024.001" in dois          # the page's own DOI too
    assert "https://arxiv.org/abs/1706.03762" in urls
    assert "https://example.org/related-note" in urls
    assert not any("x.test/self" in (r.get("url") or "") for r in refs)


def test_references_deduplicate():
    text = ("See doi:10.1000/x and again 10.1000/x and "
            "https://a.test https://a.test")
    refs = extract_references(text)
    assert len([r for r in refs if r.get("doi") == "10.1000/x"]) == 1
    assert len([r for r in refs if r.get("url") == "https://a.test"]) == 1


def test_doi_org_urls_not_double_counted():
    refs = extract_references("https://doi.org/10.7000/abc")
    assert sum(1 for r in refs if "10.7000/abc" in (r.get("doi") or "")) == 1
    assert not any((r.get("url") or "").startswith("https://doi.org")
                   for r in refs)


@pytest.mark.parametrize("url", [
    "https://evil.example/path/doi.org/10.7000/abc",
    "https://doi.org.evil.example/10.7000/abc",
    "https://doi.org" + "@" + "evil.example/10.7000/abc",
])
def test_doi_substring_on_untrusted_origin_remains_a_url(url):
    refs = extract_references(url)
    assert any(r.get("url") == url for r in refs)


def test_short_invalid_doi_prefixes_ignored():
    """Real DOI prefixes are 10.<4+ digits>; '10.1/x' is not a DOI."""
    assert not any(r.get("doi")
                   for r in extract_references("compare 10.1/x and 10.12/y"))


# ── the swarm step end-to-end ────────────────────────────────────────────────

def test_ingest_source_registers_work_refs_provenance(tmp_path):
    res = ingest_source(str(tmp_path), PAGE, url="https://x.test/self/page",
                        log_root=str(tmp_path / "log"))
    assert res["status"] == "ok"
    # earlier.work DOI + arXiv + example.org; the page's own DOI must NOT
    # appear as a self-citation
    assert len(res["references"]) == 3
    assert res["work"]["id"] not in {r["id"] for r in res["references"]}
    led = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    work = led.works[res["work"]["id"]]
    assert work["title"] == "Grounding Language Models"
    assert work["identifiers"]["sha256"]                       # fixity
    assert [c["name"] for c in work["creators"]] == ["Doe, Jane",
                                                     "Roe, Richard"]
    out_edges = [e for e in led.provenance.values()
                 if e["from"] == work["id"] and e["relation"] == "cites"]
    assert len(out_edges) == len(res["references"])
    # the ingested page is now traced; its references are the new frontier
    frontier = {r["id"] for r in led.frontier()["frontier"]}
    assert work["id"] not in frontier
    assert {r["id"] for r in res["references"]} <= frontier


def test_ingest_source_idempotent(tmp_path):
    a = ingest_source(str(tmp_path), PAGE, url="https://x.test/self/page",
                      log_root=str(tmp_path / "log"))
    b = ingest_source(str(tmp_path), PAGE, url="https://x.test/self/page",
                      log_root=str(tmp_path / "log"))
    assert b["work"]["id"] == a["work"]["id"]
    assert b["work"]["status"] == "updated"
    led = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    assert len(led.works) == 1 + len(a["references"])          # no duplicates


def test_ingest_source_without_references(tmp_path):
    res = ingest_source(str(tmp_path), "<title>Bare</title>",
                        url="https://x.test/bare",
                        follow_references=False,
                        log_root=str(tmp_path / "log"))
    assert res["references"] == []
    assert res["work"]["title"] == "Bare"


def test_ingest_source_grounded_claim_roundtrip(tmp_path):
    """The point of it all: ingest a page, then ground a claim on it and get
    a citation with the creators the page itself declared."""
    from workspaces.workspace_grounder import format_citation
    res = ingest_source(str(tmp_path), PAGE, url="https://x.test/self/page",
                        log_root=str(tmp_path / "log"))
    led = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    c = led.ground_claim("Grounding matters for language models.",
                         [res["work"]["id"]], quote="grounding matters",
                         method="swarm", agent="swarm-1")
    assert c["status"] == "created"
    cite = format_citation(led.works[res["work"]["id"]], "apa")
    assert "Doe, J." in cite and "Roe, R." in cite
    assert "10.1234/jattr.2024.001" in cite
