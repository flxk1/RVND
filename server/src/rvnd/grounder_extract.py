# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Grounder source extraction — metadata + references from a fetched page.

Local-LLM integrations #2 and #4 from EVAL_2026-06-04_grounder-local-llms.md,
built deterministic-first:

  * **metadata** — scholarly pages carry their bibliography in Highwire /
    Dublin Core meta tags (``citation_title``, ``citation_author``,
    ``citation_doi``, ``DC.title``, …) and ``<title>``; those are parsed
    deterministically. A model (injected ``model_fn(prompt) -> str``, the
    same seam as ``rule_extractor_llm``) is only consulted for plain text
    where tags don't exist — and every model-proposed field value must occur
    verbatim in the source or it is dropped with a note (extraction copies,
    never composes: the never-invent rule as a grounding check).
  * **references** — DOIs, arXiv ids, and URLs found in the text, extracted
    by regex. These are what the research swarm follows; sloppy duplicates
    collapse in the ledger's idempotent ``register_work`` and the doi/url
    identity cross-match.
  * **ingest_source** — the swarm's whole per-page step in one call:
    register the page as a work (with sha256 fixity from the content in
    hand), register every reference found inside it, and record one
    ``cites`` provenance edge per reference.

Pure stdlib. The model is optional everywhere; without it the extractors
degrade to the deterministic layer exactly.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

ModelFn = Callable[[str], str]

META_FIELDS = ("title", "creators", "container", "publisher", "date", "doi",
               "language")

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>)\];,]+)", re.IGNORECASE)
_ARXIV_RE = re.compile(r"\barXiv:\s?(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

# Highwire / PRISM / Dublin Core names → work fields
_META_MAP = {
    "citation_title": "title",
    "dc.title": "title",
    "og:title": "title",
    "citation_journal_title": "container",
    "citation_conference_title": "container",
    "prism.publicationname": "container",
    "citation_publisher": "publisher",
    "dc.publisher": "publisher",
    "citation_publication_date": "date",
    "citation_date": "date",
    "dc.date": "date",
    "citation_doi": "doi",
    "dc.identifier.doi": "doi",
    "citation_language": "language",
    "dc.language": "language",
}
_AUTHOR_KEYS = ("citation_author", "dc.creator", "dc.contributor")


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.authors: list[str] = []
        self._in_title = False
        self.page_title = ""

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag != "meta":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        key = (a.get("name") or a.get("property") or "").strip().lower()
        content = a.get("content", "").strip()
        if not key or not content:
            return
        if key in _AUTHOR_KEYS:
            self.authors.append(content)
        elif key in _META_MAP and _META_MAP[key] not in self.meta:
            self.meta[_META_MAP[key]] = content

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and not self.page_title:
            self.page_title = data.strip()


def extract_metadata_tags(content: str) -> dict[str, Any]:
    """Deterministic layer: bibliographic meta tags + ``<title>``."""
    p = _MetaParser()
    try:
        p.feed(content)
    except Exception:                                   # noqa: BLE001
        pass
    out: dict[str, Any] = dict(p.meta)
    if p.authors:
        out["creators"] = [{"name": n} for n in p.authors]
    if "title" not in out and p.page_title:
        out["title"] = p.page_title
    return out


def _grounded(value: str, source: str) -> bool:
    return bool(value) and value.strip().lower() in source.lower()


def extract_metadata(content: str, *, model_fn: Optional[ModelFn] = None
                     ) -> dict[str, Any]:
    """Metadata for ``register_work``: deterministic tags first; a model only
    fills fields the tags didn't, and every model value must occur verbatim
    in the source (never-invent as a grounding check). Returns the field
    dict plus ``_dropped`` notes for anything the check rejected."""
    out = extract_metadata_tags(content)
    if model_fn is None:
        return out
    missing = [f for f in META_FIELDS if f not in out]
    if not missing:
        return out
    prompt = (
        "Extract bibliographic metadata from the text below. Reply with ONE "
        "JSON object, keys among " + json.dumps(missing) + ". 'creators' is "
        "a list of name strings exactly as written in the text. Use only "
        "values that appear verbatim in the text; omit anything not stated. "
        "No prose.\n\nTEXT:\n" + content[:6000])
    try:
        reply = model_fn(prompt)
        m = re.search(r"\{.*\}", reply, re.DOTALL)
        proposed = json.loads(m.group(0)) if m else {}
    except Exception as exc:                            # noqa: BLE001
        out["_dropped"] = [{"why": f"model_fn failed: {exc}"}]
        return out
    dropped: list[dict] = []
    for field, value in proposed.items():
        if field not in missing:
            continue
        if field == "creators":
            names = [v if isinstance(v, str) else v.get("name", "")
                     for v in (value or [])]
            kept = [{"name": n} for n in names if _grounded(n, content)]
            for n in names:
                if not _grounded(n, content):
                    dropped.append({"field": "creators", "value": n,
                                    "why": "not found verbatim in source"})
            if kept:
                out["creators"] = kept
        elif isinstance(value, str) and _grounded(value, content):
            out[field] = value.strip()
        elif value:
            dropped.append({"field": field, "value": value,
                            "why": "not found verbatim in source"})
    if dropped:
        out["_dropped"] = dropped
    return out


def extract_references(content: str, *, own_url: str = "") -> list[dict]:
    """Deterministic reference candidates the swarm can follow: DOIs, arXiv
    ids, and URLs (excluding the page's own URL). Each candidate is a partial
    work dict for ``register_work``; duplicates collapse downstream."""
    refs: dict[str, dict] = {}
    for m in _DOI_RE.finditer(content):
        doi = m.group(1).rstrip(".")
        refs.setdefault("doi:" + doi.lower(),
                        {"title": "Referenced work (DOI " + doi + ")",
                         "doi": doi, "type": "article"})
    for m in _ARXIV_RE.finditer(content):
        aid = m.group(1)
        url = "https://arxiv.org/abs/" + aid
        refs.setdefault("url:" + url,
                        {"title": "Referenced work (arXiv:" + aid + ")",
                         "url": url, "type": "preprint"})
    own = own_url.strip().rstrip("/")
    for m in _URL_RE.finditer(content):
        url = m.group(0).rstrip(".,;)")
        # Hostname comparison, never substring sanitisation: an attacker can
        # place ``doi.org/`` in a path or userinfo on an unrelated origin.
        if (urlsplit(url).hostname or "").lower() in {"doi.org", "dx.doi.org"}:
            continue
        if own and url.rstrip("/").startswith(own):
            continue
        refs.setdefault("url:" + url,
                        {"title": "Referenced work (" + url + ")",
                         "url": url, "type": "web"})
    return list(refs.values())


def ingest_source(folder: str, content: str, *, url: str = "",
                  title: str = "", model_fn: Optional[ModelFn] = None,
                  retrieved_by: str = "swarm", follow_references: bool = True,
                  log_root: Optional[str] = None) -> dict:
    """The swarm's per-page step: register the fetched page as a work
    (metadata from tags/model, fixity hash from the content in hand),
    register every reference found in it, and record ``cites`` provenance
    edges. Returns the work id, reference ids, and extraction notes."""
    from .workspace_grounder import GroundingLedger
    ledger = GroundingLedger(folder, log_root=log_root)
    meta = extract_metadata(content, model_fn=model_fn)
    dropped = meta.pop("_dropped", [])
    if title:
        meta["title"] = title
    meta.setdefault("title", url or "Untitled source")
    work = ledger.register_work(
        **{k: v for k, v in meta.items() if k in META_FIELDS},
        type="web", url=url, content=content, retrieved_by=retrieved_by)
    references: list[dict] = []
    if follow_references:
        for ref in extract_references(content, own_url=url):
            rw = ledger.register_work(**ref, retrieved_by=retrieved_by)
            if rw["id"] == work["id"]:
                continue        # the page citing its own DOI is not provenance
            edge = ledger.add_provenance(work["id"], "cites", rw["id"],
                                         basis="reference found in source")
            references.append({"id": rw["id"], "status": rw["status"],
                               "edge": edge.get("status")})
    return {"status": "ok", "work": {"id": work["id"],
                                     "status": work["status"],
                                     "title": work.get("title", "")},
            "metadata_fields": sorted(k for k in meta if meta.get(k)),
            "dropped": dropped,
            "references": references,
            "frontier_hint": ledger.frontier()["count"]}
