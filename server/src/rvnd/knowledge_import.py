# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Import an external source registry into the grounding layer.

A source registry (the digital-law knowledge-index CSV shape) lists one document
per row with its identity, bibliographic metadata, and a confidence marker. This
module registers each row as a grounding **work** on the shared URN spine, so an
imported corpus becomes addressable and citable without touching the legal-entity
corpus: documents are grounding, entities stay separate, and they meet on the URN.

Neutral: a row's identity is read from its own ``canonical_urn`` namespace
(``celex`` / ``doi`` / ``arxiv`` / ... or a ``source`` fallback) and re-minted
under the tool's ``lg`` root by ``register_work`` — no scheme is privileged.
Deduplicated: rows resolving to the same work identity collapse to one work.

Metadata + identity only. The document bodies never move, so a private corpus
stays private; indexing bodies for retrieval is a separate, opt-in step.

Internal by design: a batch connector invoked by a host or pipeline, not a
standalone UI op.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from .urn import mint_canonical
from .grounder import GroundingLedger


# source ``document_type`` -> grounding work type (WORK_TYPES); "other" when unmapped
_TYPE_MAP = {
    "legal_act": "statute", "legislation": "statute", "regulation": "statute",
    "directive": "statute", "statute": "statute",
    "case": "case", "judgment": "case", "ruling": "case", "case_law": "case",
    "article": "article", "paper": "article", "journal_article": "article",
    "book": "book", "chapter": "chapter", "standard": "standard",
    "report": "report", "preprint": "preprint", "thesis": "thesis",
    "dataset": "dataset",
}


def _work_type(document_type: str) -> str:
    return _TYPE_MAP.get((document_type or "").strip().lower(), "other")


def _tags_from_row(row: dict) -> list:
    """Categorical facets for retrieval: jurisdiction and topics, each prefixed so
    they stay distinguishable in one flat tag list."""
    tags = []
    juris = (row.get("jurisdiction") or "").strip()
    if juris:
        tags.append("jurisdiction:" + juris)
    topics = row.get("topics") or row.get("primary_topic") or ""
    for t in topics.split(";"):
        t = t.strip()
        if t:
            tags.append("topic:" + t)
    return tags


def _ids_from_urn(canonical_urn: str) -> dict:
    """The addressing identifiers a registry URN carries, as a namespace->value
    map. ``urn:<root>:<ns>:<id>`` -> ``{ns: id}`` for any external namespace; a
    ``source`` (no external id) URN yields ``{}`` so the work keys on its title."""
    parts = (canonical_urn or "").split(":")
    if len(parts) >= 4 and parts[0] == "urn" and parts[3] and parts[2] != "source":
        return {parts[2]: parts[3]}
    return {}


def import_source_registry(folder, csv_path, *,
                           log_root: Optional[Path] = None) -> dict:
    """Register every row of a source-registry CSV as a grounding work. Returns a
    summary with ``imported`` (works registered), ``deduped`` (rows folding into
    an already-registered work), and ``skipped`` (rows with no usable identity)."""
    ledger = GroundingLedger(folder, log_root=log_root)
    imported = deduped = skipped = 0
    seen: set[str] = set()
    # One batch for the whole registry: per-row register_work would otherwise
    # reload and rewrite the JSONL stores each row — O(n^2) over thousands.
    with Path(csv_path).open(encoding="utf-8", newline="") as fh, ledger.batch():
        for row in csv.DictReader(fh):
            title = (row.get("title_guess") or "").strip()
            ids = _ids_from_urn(row.get("canonical_urn", ""))
            try:
                target = mint_canonical("", ids=ids, title=title)
            except ValueError:
                skipped += 1                # no identifier and no title to key on
                continue
            if target in seen:
                deduped += 1
                continue
            seen.add(target)
            author = (row.get("author_or_institution_guess") or "").strip()
            creators = [{"name": author, "role": "author"}] if author else []
            ledger.register_work(
                title=title or row.get("canonical_urn", ""),
                type=_work_type(row.get("document_type", "")),
                creators=creators,
                date=(row.get("detected_year") or "").strip(),
                identifiers=ids,
                tags=_tags_from_row(row),
                confidence=(row.get("inference_level") or "").strip())
            imported += 1
    return {"imported": imported, "deduped": deduped, "skipped": skipped,
            "works": len(seen)}
