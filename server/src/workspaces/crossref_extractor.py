# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Cross-document reference extraction — the inter-instrument link layer.

Detects when one instrument cites another ("without prejudice to Regulation (EU)
2016/679") and emits one ``kind=cross-reference`` pair per distinct target,
carrying the relation verb (lex-specialis / without-prejudice / amends / …) and a
cross-document edge ``(host) -[relation]-> (target)``.

The instrument registry, citation resolution, relation-verb typing and host
inference are **not** implemented here any more — they are the legal plane's, and
this module CONSUMES them through the single ``adapters.legal`` seam
(:func:`loomground_legal.extract_cross_references` / ``infer_host_instrument`` /
``INSTRUMENTS``). What stays here is the ND-dispatcher wrapping: projecting a
resolved cross-reference into a mental-model pair + edge — RVND's own orchestration.

The DTO shapes below (``Instrument`` with ``.key``, ``CrossReference`` with
``.target_key``) preserve the exact surface RVND consumers already read. They carry
no data or logic of their own: the registry is *derived* from the plane's
``INSTRUMENTS`` and results are *mapped* from the plane's ``CrossReference``.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from .adapters.legal import (
    INSTRUMENTS as _LEGAL_INSTRUMENTS,
    extract_cross_references as _legal_extract_cross_references,
    infer_host_instrument as _legal_infer_host_instrument,
)
from .nd_routing import BaseNDDispatcher


# ── DTO shapes preserving the RVND-consumer surface (no data/logic of their own) ─

@dataclass(frozen=True)
class Instrument:
    """The RVND-facing instrument shape (``.key`` is the plane's ``.code``).

    Derived from the legal plane's ``INSTRUMENTS`` — not a hand-maintained registry."""

    key: str
    canonical: str
    celex: str = ""
    short_names: tuple[str, ...] = ()


# The registry, projected from the legal plane (the single source of instrument
# identity). ``corpus/ingest`` and the dispatcher read ``.key`` / ``.celex`` /
# ``.canonical`` off these — preserved verbatim.
_INSTRUMENTS: tuple[Instrument, ...] = tuple(
    Instrument(key=i.code, canonical=i.canonical, celex=i.celex,
               short_names=tuple(i.short_names))
    for i in _LEGAL_INSTRUMENTS)
_BY_KEY: dict[str, Instrument] = {i.key: i for i in _INSTRUMENTS}


@dataclass
class CrossReference:
    """The RVND-facing cross-reference shape (``.target_key`` is the plane's
    ``.target_code``). Populated by mapping the plane's ``CrossReference``."""

    target_key: str
    target_canonical: str
    target_celex: str = ""
    relation: str = "refers-to"
    dimension: str = "relational"
    matched_text: str = ""
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def infer_host_instrument(content: str) -> str:
    """The instrument the document *is* (its ``key``/code, ``""`` if unknown).

    Consumed verbatim from the legal plane — RVND no longer infers this itself,
    so its two former twins of this collapse onto the plane's one."""
    return _legal_infer_host_instrument(content)


def extract_cross_references(content: str, *,
                            host_key: str | None = None) -> list[CrossReference]:
    """References to OTHER instruments. Resolution + relation-typing are consumed
    from the legal plane; results are mapped to the RVND DTO (the plane's
    ``target_code`` becomes ``target_key``)."""
    return [
        CrossReference(
            target_key=r.target_code, target_canonical=r.target_canonical,
            target_celex=r.target_celex, relation=r.relation, dimension=r.dimension,
            matched_text=r.matched_text, count=r.count)
        for r in _legal_extract_cross_references(content, host_code=host_key)
    ]


# ---------------------------------------------------------------------------
# ND dispatcher — RVND-owned orchestration (unchanged)
# ---------------------------------------------------------------------------

def _hash_pair(content: str, nd_id: str, source: str | None) -> str:
    h = hashlib.sha256()
    h.update(nd_id.encode("utf-8")); h.update(b"|")
    h.update((source or "inline").encode("utf-8")); h.update(b"|")
    h.update(content.encode("utf-8"))
    return "sha256:" + h.hexdigest()[:32]


class CrossReferenceExtractor(BaseNDDispatcher):
    """ND that links the host document to the other instruments it cites.

    Fires on normative *and* document content (a recital citing the GDPR is
    not itself operative but the link is still real). Produces one
    ``kind=cross-reference`` pair per distinct target instrument, each
    carrying a cross-document edge.
    """

    nd_id = "nd-crossref"
    handles_types = ["normative", "document"]
    handles_facets: list[str] = []
    confidence_floor = 0.0   # links are useful even on low-confidence docs

    def extract(self, content, classification, *, source_document=None):
        # Prefer the classifier's facet for the host instrument — it is the
        # strongest signal of what the document *is*. A bare in-text citation
        # number (e.g. "without prejudice to 2016/679") otherwise mis-infers
        # the host as the cited instrument and the link is dropped as a
        # self-reference. Fall back to text inference when no facet maps.
        host_key = ""
        for f in getattr(classification, "facets", []) or []:
            if f in _BY_KEY:
                host_key = f
                break
        if not host_key:
            host_key = infer_host_instrument(content)
        refs = extract_cross_references(content, host_key=host_key)
        host_label = _BY_KEY[host_key].canonical if host_key in _BY_KEY else (host_key or "this document")
        out: list[dict[str, Any]] = []
        for idx, ref in enumerate(refs):
            pid = _hash_pair(f"{host_key}->{ref.target_key or ref.matched_text}",
                             self.nd_id, source_document) + f"-x{idx}"
            out.append({
                "id": pid,
                "problem": {
                    "id": f"{pid}-p",
                    "kind": "cross-reference",
                    "scope": host_key or "regulation",
                    "type": "mental-model",
                    "summary": f"{host_label} {ref.relation} {ref.target_canonical}",
                    "facets": {
                        "host": host_key,
                        "target": ref.target_key,
                        "relation": ref.relation,
                        "target_celex": ref.target_celex,
                        "citations": ref.count,
                    },
                    "context": {"kind_of_model": "cross-document-reference"},
                },
                "solution": {
                    "id": pid,
                    "problem_id": f"{pid}-p",
                    "host_instrument": host_key,
                    "target_instrument": ref.target_key,
                    "target_canonical": ref.target_canonical,
                    "target_celex": ref.target_celex,
                    "relation": ref.relation,
                    "body": (f"CROSS-REFERENCE\n{host_label}\n"
                             f"  -[{ref.relation}]->\n  {ref.target_canonical}"
                             + (f"  (CELEX {ref.target_celex})" if ref.target_celex else "")),
                    "body_format": "structured-crossref",
                    "authority_tier": 1,
                    "confidence": 0.9 if ref.target_key else 0.6,
                },
                "edges": [{
                    "subject": host_key or "this-document",
                    "predicate": ref.relation,
                    "object": ref.target_key or ref.matched_text,
                    "dimension": ref.dimension,
                }],
            })
        return out


def register_crossref_nd(router) -> None:
    """Register the cross-reference ND on a router."""
    router.register(CrossReferenceExtractor())
