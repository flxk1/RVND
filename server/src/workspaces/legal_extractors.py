# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Legal mental-model extractors (Phase M1).

Three new ND dispatchers that populate the KG with kinds of mental model
beyond the deontic-rule extractor:

- :class:`DefinitionExtractor` — catches ``"X means Y"`` patterns and
  produces ``kind=definition`` pairs. The defined term + the definition
  body land as structured solution fields so the cloud LLM sees them in
  the safe-context triples (lock-scrubbed).

- :class:`ArticleReferenceExtractor` — catches ``"Article N"``,
  ``"Recital N"``, ``"Annex IV"`` references in normative content and
  produces ``kind=article-reference`` pairs that connect content to its
  formal location in a regulation. Useful for chat questions like
  "what does Article 5 of the AI Act prohibit?".

- :class:`DocumentSummaryExtractor` — runs ONCE per ingested document
  (not per rule) and produces a single ``kind=doc-summary`` pair
  carrying a structured summary: regulation name, scope, recital 1,
  first operative paragraph. This is what the chat needs to answer
  "what is the AI Act?".

Mental-model schema, on each pair these extractors produce:

    problem.kind:        "definition" | "article-reference" | "doc-summary"
    problem.scope:       "gdpr" | "ai-act" | "regulation" | "case-law" | ...
    problem.summary:     human-readable label of the model
    problem.context:     when this knowledge applies (jurisdictions, domains)

    solution.body:       canonical text rendering (dirty side; never
                         exposed by safe-context post-Phase-A)
    solution.<typed>:    kind-specific structured fields that DO surface
                         in safe-context triples after lock scrubs them

The structured solution fields are the mechanism by which the LLM sees
actual knowledge (not just taxonomy) without seeing raw bodies.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .nd_routing import BaseNDDispatcher
# document kind/identifier/instrument + host inference are the legal plane's;
# consumed through the single adapters.legal seam (retiring the parallels below).
from .adapters.legal import (
    summarize_document as _summarize_document,
    infer_host_instrument as _legal_infer_host_instrument,
)


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _hash_pair(content: str, nd_id: str, source: str | None) -> str:
    h = hashlib.sha256()
    h.update(nd_id.encode("utf-8"))
    h.update(b"|")
    h.update((source or "inline").encode("utf-8"))
    h.update(b"|")
    h.update(content.encode("utf-8"))
    return "sha256:" + h.hexdigest()[:32]


# ---------------------------------------------------------------------------
# DefinitionExtractor — "X means Y"
# ---------------------------------------------------------------------------

# Patterns for "X means Y" style definitions in legal text. EU regulations
# follow a canonical shape: Article 4 GDPR, Article 3 AI Act, etc., all
# start with "For the purposes of this Regulation:" + a numbered list of
# definitions "(1) 'X' means Y;".
_DEF_PATTERNS = [
    # "'X' means Y." — strict EU regulation form
    re.compile(
        r"['‘](?P<term>[A-Za-z][A-Za-z \-]{1,80})['’]\s+means\s+(?P<defn>[^;.]{15,500})[;.]",
        re.IGNORECASE,
    ),
    # `"X" means Y.` — double-quoted variant
    re.compile(
        r"[\"“](?P<term>[A-Za-z][A-Za-z \-]{1,80})[\"”]\s+means\s+(?P<defn>[^;.]{15,500})[;.]",
        re.IGNORECASE,
    ),
    # "X is defined as Y" — narrative form
    re.compile(
        r"\b(?P<term>[A-Za-z][A-Za-z \-]{2,80})\s+is\s+defined\s+as\s+(?P<defn>[^;.]{15,500})[;.]",
        re.IGNORECASE,
    ),
    # "For the purposes of this Regulation, 'X' means Y" — broadest opener
    re.compile(
        r"[Ff]or the purposes? of th(?:is|e following)\s+\w+,?\s*"
        r"['‘\"“](?P<term>[A-Za-z][A-Za-z \-]{1,80})['’\"”]"
        r"\s+(?:means|shall mean|refers to)\s+(?P<defn>[^;.]{15,500})[;.]",
    ),
]


class DefinitionExtractor(BaseNDDispatcher):
    """Extracts ``X means Y`` definitions and produces mental-model pairs
    with structured ``term`` + ``defined_as`` solution fields."""

    nd_id = "nd-definition"
    handles_types = ["normative", "document"]
    handles_facets: list[str] = []   # fires on any normative content
    confidence_floor = 0.5

    def extract(self, content, classification, *, source_document=None):
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pattern in _DEF_PATTERNS:
            for m in pattern.finditer(content):
                term = (m.group("term") or "").strip()
                defn = (m.group("defn") or "").strip()
                if not term or not defn:
                    continue
                if len(term) < 2 or len(defn) < 15:
                    continue
                key = term.lower()
                if key in seen:
                    continue
                seen.add(key)
                pair_id = _hash_pair(f"{term}::{defn}", self.nd_id, source_document)
                # Domain inference from classification facets.
                domain = "definition"
                facets = classification.metadata.get("facets", []) if hasattr(classification, "metadata") else []
                for d in ("gdpr", "ai-act", "music-rights", "contracts"):
                    if d in facets:
                        domain = d
                        break
                out.append({
                    "id": pair_id,
                    "problem": {
                        "id": f"{pair_id}-p",
                        "kind": "definition",
                        "scope": domain,
                        "type": "mental-model",
                        "summary": f"definition: {term}",
                        "facets": {
                            "domain": domain,
                            "term": term,
                            "language": "en",
                        },
                        "context": {
                            "domains": [domain],
                            "kind_of_model": "legal-definition",
                        },
                    },
                    "solution": {
                        "id": pair_id,
                        "problem_id": f"{pair_id}-p",
                        # Structured content fields — these surface in triples
                        # (lock-scrubbed) so the LLM sees actual knowledge.
                        "term": term,
                        "defined_as": defn,
                        # Canonical text body (dirty side; not exposed).
                        "body": f"DEFINITION\nterm: {term}\nmeans: {defn}",
                        "body_format": "structured-definition",
                        "authority_tier": 1,
                        "confidence": 0.85,
                    },
                })
        return out


# ---------------------------------------------------------------------------
# ArticleReferenceExtractor — Article N / Recital N / Annex N
# ---------------------------------------------------------------------------

_ARTICLE_RE = re.compile(
    r"\b(?:Article|Art\.|Artikel)\s+(?P<num>\d+[a-z]?)"
    r"(?:\s*\(\s*\d+\s*\))?"   # optional paragraph (1), (2) — captured loosely
    r"(?:\s+(?:of|GDPR|AI Act|Regulation|the\s+[A-Z][A-Za-z\s]+?\sAct|Directive\s+\d+/\d+))?",
    re.IGNORECASE,
)
_RECITAL_RE = re.compile(r"\bRecital\s+(?P<num>\d+)\b", re.IGNORECASE)
_ANNEX_RE   = re.compile(r"\b(?:Annex|Anhang)\s+(?P<num>[IVXLCDM]+|\d+)\b", re.IGNORECASE)


def _infer_regulation(content: str) -> str:
    """Which instrument the content is from ('unknown' if none).

    Consumed from the legal plane's single host inference — this module no longer
    carries its own, so the twin of ``crossref.infer_host_instrument`` collapses
    onto the plane's one. The '' the plane returns for 'no instrument' is preserved
    as 'unknown' for the mental-model scope fields."""
    return _legal_infer_host_instrument(content) or "unknown"


class ArticleReferenceExtractor(BaseNDDispatcher):
    """Extracts Article / Recital / Annex references from legal content
    and produces mental-model pairs with structured ``article_number`` +
    ``regulation`` solution fields."""

    nd_id = "nd-article-ref"
    handles_types = ["normative", "document"]
    handles_facets: list[str] = []
    confidence_floor = 0.4

    def extract(self, content, classification, *, source_document=None):
        regulation = _infer_regulation(content)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ref_kind, pattern in (
            ("article", _ARTICLE_RE),
            ("recital", _RECITAL_RE),
            ("annex",   _ANNEX_RE),
        ):
            for m in pattern.finditer(content):
                num = m.group("num")
                if not num:
                    continue
                key = f"{ref_kind}::{num}::{regulation}"
                if key in seen:
                    continue
                seen.add(key)
                pair_id = _hash_pair(key, self.nd_id, source_document)
                out.append({
                    "id": pair_id,
                    "problem": {
                        "id": f"{pair_id}-p",
                        "kind": "article-reference",
                        "scope": regulation,
                        "type": "mental-model",
                        "summary": f"{ref_kind} {num} of {regulation}",
                        "facets": {
                            "domain": regulation,
                            "ref_kind": ref_kind,
                            "ref_number": num,
                            "language": "en",
                        },
                        "context": {
                            "regulation": regulation,
                            "kind_of_model": "legal-reference",
                        },
                    },
                    "solution": {
                        "id": pair_id,
                        "problem_id": f"{pair_id}-p",
                        "ref_kind": ref_kind,
                        "ref_number": num,
                        "regulation": regulation,
                        "body": f"REFERENCE\n{ref_kind} {num} of {regulation}",
                        "body_format": "structured-reference",
                        "authority_tier": 1,
                        "confidence": 0.7,
                    },
                })
        return out


# ---------------------------------------------------------------------------
# DocumentSummaryExtractor — one mental model per document
# ---------------------------------------------------------------------------

# Document-kind detection (regulation / directive / case-law header patterns) is
# the legal plane's — consumed via legal.summarize_document (the _REG_NAME_RE /
# _DIR_NAME_RE / _CASE_NAME_RE parallels that used to live here are retired).


class DocumentSummaryExtractor(BaseNDDispatcher):
    """Runs once per document and produces one ``kind=doc-summary``
    mental model carrying structured fields the chat can use to answer
    "what is this document about?".

    Strategy:
    - Detect document kind (regulation / directive / case-law / contract /
      lecture / other) from leading text patterns.
    - Extract the document identifier (regulation number, case citation).
    - Pull the first 2-3 paragraphs as the canonical summary, redacted
      via lock at ingest.
    - Capture domain inference for the LLM to ground from.
    """

    nd_id = "nd-doc-summary"
    handles_types = ["normative", "document"]
    handles_facets: list[str] = []
    confidence_floor = 0.0    # always fires — one per doc

    def extract(self, content, classification, *, source_document=None):
        # Document kind, identifier and instrument are the legal plane's — consume
        # its document summary (retiring the _REG_NAME_RE / _DIR_NAME_RE /
        # _CASE_NAME_RE + _infer_regulation parallels this module used to carry).
        summary = _summarize_document(content, source_name=source_document or "")
        doc_kind = summary.doc_kind
        doc_id = summary.doc_id
        regulation = summary.instrument or "unknown"
        # The excerpt is a general PDF-text concern (skip page markers / short
        # paragraphs) — kept local, not a legal-domain parallel.
        summary_excerpt = _first_summary_excerpt(content)

        # ONE pair per document.
        pair_id = _hash_pair(
            f"summary::{doc_kind}::{doc_id}", self.nd_id, source_document
        )
        return [{
            "id": pair_id,
            "problem": {
                "id": f"{pair_id}-p",
                "kind": "doc-summary",
                "scope": regulation,
                "type": "mental-model",
                "summary": f"{doc_kind} {doc_id} — overview",
                "facets": {
                    "domain":      regulation,
                    "doc_kind":    doc_kind,
                    "doc_id":      doc_id,
                    "language":    "en",
                },
                "context": {
                    "regulation":     regulation,
                    "kind_of_model":  "document-summary",
                    "doc_kind":       doc_kind,
                },
            },
            "solution": {
                "id": pair_id,
                "problem_id": f"{pair_id}-p",
                "doc_kind":   doc_kind,
                "doc_id":     doc_id,
                "regulation": regulation,
                "summary_excerpt": summary_excerpt,
                "body": (
                    f"DOCUMENT SUMMARY\n"
                    f"kind:       {doc_kind}\n"
                    f"identifier: {doc_id}\n"
                    f"regulation: {regulation}\n"
                    f"excerpt:    {summary_excerpt}"
                ),
                "body_format": "structured-summary",
                "authority_tier": 2,
                "confidence": 0.8,
            },
        }]


def _first_summary_excerpt(content: str, max_chars: int = 600) -> str:
    """Return the first 600 chars of meaningful content. Skips empty
    paragraphs and page markers from the PDF extractor."""
    out_chars: list[str] = []
    for paragraph in re.split(r"\n\s*\n", content):
        p = paragraph.strip()
        if not p:
            continue
        if p.startswith("--- page") and p.endswith("---"):
            continue
        if len(p) < 40:
            continue   # skip headers / page numbers
        out_chars.append(p)
        if sum(len(x) for x in out_chars) >= max_chars:
            break
    joined = " ".join(out_chars)
    return joined[:max_chars]


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_legal_mental_model_extractors(router) -> None:
    """Register all three legal mental-model extractors on a router.

    Composes with ``register_default_domain_nds`` — the domain NDs
    pull deontic rules; these extractors pull definitions, references,
    and document summaries. Together they form the M1 mental-model layer.
    """
    router.register(DefinitionExtractor())
    router.register(ArticleReferenceExtractor())
    router.register(DocumentSummaryExtractor())
