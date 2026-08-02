# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Cross-document reference extraction — the inter-instrument link layer.

:mod:`.legal_extractors` (``ArticleReferenceExtractor``) catches references to
locations *inside the document being read* — "Article 5", "Recital 1",
"Annex III". This module catches references to **other instruments**: when the
AI Act says "without prejudice to Regulation (EU) 2016/679", that is a link
from the AI Act to the GDPR, and it is the spine of a NotebookLM-grade
analysis ("this document points at these other documents").

What it detects
---------------
1. **Named EU instruments** by their canonical citation — "Regulation (EU)
   2016/679", "Directive 2009/24/EC", "Regulation (EU) 2024/1689" — and by
   their common short names (GDPR, DSA, DMA, NIS2, CRA, DORA, …).
2. **CELEX identifiers** — "32016R0679", "32024R1689" — the stable EUR-Lex key.
3. **German statutes** — "BDSG", "UrhG", "§ 4 BDSG".

What it emits
-------------
One ``kind=cross-reference`` pair per *distinct target instrument* (deduped),
carrying the target's canonical id + CELEX (when resolvable) + the relation
verb that introduced it (lex-specialis / without-prejudice / in-accordance-
with / amends / repeals / refers-to). Each pair carries a **cross-document
edge**: ``(host_instrument) -[relation]-> (target_instrument)``.

Self-references are dropped: a GDPR document referencing "this Regulation" or
"Regulation (EU) 2016/679" is not a cross-document link. The host instrument
is inferred the same way :func:`legal_extractors._infer_regulation` does, and
exported here so both modules agree.

Dimensions
----------
- ``without-prejudice`` / ``in-accordance-with`` / ``refers-to`` → RELATIONAL
  (the documents are linked; no hierarchy asserted).
- ``lex-specialis-to`` / ``amends`` / ``repeals`` / ``supersedes`` →
  STRUCTURAL when it states a norm-hierarchy relationship; the *date* of a
  supersession is NOT set here (per the locked decision "date from the
  currency pipeline, relationship from the extractor").

This module reads only; it does not fetch the target document. Resolving a
target to its actual obligation pairs is the interaction layer's job
(:mod:`.interaction_extractor`), which runs over the pairs both documents
produced once both are ingested.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from workspaces.adapters.solver.dimensions import Dimension
from .nd_routing import BaseNDDispatcher


# ---------------------------------------------------------------------------
# Instrument registry — short name ↔ canonical id ↔ CELEX
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Instrument:
    key: str                 # stable internal id, e.g. "gdpr"
    canonical: str           # human label, e.g. "Regulation (EU) 2016/679 (GDPR)"
    celex: str = ""          # CELEX number, e.g. "32016R0679"
    short_names: tuple[str, ...] = ()   # GDPR, DS-GVO, …


# Curated registry of the instruments the target domains actually cite. Add rows
# here; the matchers below are derived from it. CELEX numbers are the stable
# EUR-Lex keys (sector 3 = legislation; R = regulation, L = directive).
_INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument("gdpr", "Regulation (EU) 2016/679 (GDPR)", "32016R0679",
               ("GDPR", "DSGVO", "DS-GVO", "General Data Protection Regulation")),
    Instrument("ai-act", "Regulation (EU) 2024/1689 (AI Act)", "32024R1689",
               ("AI Act", "AIA", "Artificial Intelligence Act")),
    Instrument("dsa", "Regulation (EU) 2022/2065 (DSA)", "32022R2065",
               ("DSA", "Digital Services Act")),
    Instrument("dma", "Regulation (EU) 2022/1925 (DMA)", "32022R1925",
               ("DMA", "Digital Markets Act")),
    Instrument("nis2", "Directive (EU) 2022/2555 (NIS2)", "32022L2555",
               ("NIS2", "NIS 2", "NIS2 Directive")),
    Instrument("cra", "Regulation (EU) 2024/2847 (Cyber Resilience Act)", "32024R2847",
               ("CRA", "Cyber Resilience Act")),
    Instrument("dora", "Regulation (EU) 2022/2554 (DORA)", "32022R2554",
               ("DORA", "Digital Operational Resilience Act")),
    Instrument("data-act", "Regulation (EU) 2023/2854 (Data Act)", "32023R2854",
               ("Data Act",)),
    Instrument("data-governance-act", "Regulation (EU) 2022/868 (Data Governance Act)",
               "32022R0868", ("Data Governance Act", "DGA")),
    Instrument("software-directive", "Directive 2009/24/EC (Software Directive)",
               "32009L0024", ("Software Directive", "Computer Programs Directive")),
    Instrument("dsm-directive", "Directive (EU) 2019/790 (DSM Directive)", "32019L0790",
               ("DSM Directive", "Copyright Directive", "CDSM")),
    Instrument("eidas", "Regulation (EU) 910/2014 (eIDAS)", "32014R0910", ("eIDAS",)),
    Instrument("bdsg", "Bundesdatenschutzgesetz (BDSG)", "", ("BDSG",)),
    Instrument("urhg", "Urheberrechtsgesetz (UrhG)", "", ("UrhG",)),
)

_BY_KEY = {i.key: i for i in _INSTRUMENTS}


# ---------------------------------------------------------------------------
# Matchers
# ---------------------------------------------------------------------------

# "Regulation (EU) 2016/679" / "Regulation (EC) No 45/2001" / "Directive 2009/24/EC"
_REG_CITE_RE = re.compile(
    r"\b(?P<kind>Regulation|Directive)\s*\((?:EU|EC|EEC)\)\s*(?:No\.?\s*)?"
    r"(?P<num>\d{1,4}/\d{2,4})",
    re.IGNORECASE,
)
# "Directive 2009/24/EC" — kind then number then /EC suffix, no parenthetical.
_DIR_CITE_RE = re.compile(
    r"\b(?P<kind>Directive|Regulation)\s+(?P<num>\d{1,4}/\d{1,4})(?:/(?:EU|EC|EEC))\b",
    re.IGNORECASE,
)
# CELEX: sector(1) + year(4) + type(1 letter) + number(4). e.g. 32016R0679.
_CELEX_RE = re.compile(r"\b(?P<celex>3\d{4}[A-Z]\d{4})\b")

# Relation verbs that introduce a cross-reference, mapped to (relation, dim).
_RELATIONS: tuple[tuple[re.Pattern[str], str, Dimension], ...] = (
    (re.compile(r"without\s+prejudice\s+to", re.I), "without-prejudice", Dimension.RELATIONAL),
    (re.compile(r"in\s+accordance\s+with", re.I), "in-accordance-with", Dimension.RELATIONAL),
    (re.compile(r"as\s+(?:defined|referred\s+to|laid\s+down)\s+in", re.I), "refers-to", Dimension.RELATIONAL),
    (re.compile(r"pursuant\s+to", re.I), "pursuant-to", Dimension.RELATIONAL),
    (re.compile(r"lex\s+specialis", re.I), "lex-specialis-to", Dimension.STRUCTURAL),
    (re.compile(r"\bamend(?:s|ing|ment\s+to)?\b", re.I), "amends", Dimension.STRUCTURAL),
    (re.compile(r"\brepeal(?:s|ing|ed)?\b", re.I), "repeals", Dimension.STRUCTURAL),
    (re.compile(r"\bsupersed(?:es|ing|ed)\b", re.I), "supersedes", Dimension.STRUCTURAL),
    (re.compile(r"complement(?:s|ary\s+to|ing)?", re.I), "complements", Dimension.RELATIONAL),
)

# Window (chars) around a citation in which we look for a relation verb.
_RELATION_WINDOW = 80


@dataclass
class CrossReference:
    target_key: str                  # instrument key, or "" if unresolved
    target_canonical: str            # best human label of the target
    target_celex: str = ""
    relation: str = "refers-to"
    dimension: str = Dimension.RELATIONAL.value
    matched_text: str = ""           # the citation as it appeared
    count: int = 1                   # how many times this target was cited

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def infer_host_instrument(content: str) -> str:
    """Best-effort detection of which instrument the document *is*.

    Kept consistent with legal_extractors._infer_regulation but returns an
    instrument *key* from this module's registry (or "" if unknown), so a
    self-reference can be filtered out.
    """
    snippet = content[:8000]
    # Prefer a CELEX self-id near the top (regulations carry it in the header).
    for m in _CELEX_RE.finditer(snippet):
        for inst in _INSTRUMENTS:
            if inst.celex and inst.celex == m.group("celex"):
                return inst.key
    low = snippet.lower()
    if "2024/1689" in low or "artificial intelligence act" in low:
        return "ai-act"
    if "2016/679" in low or "general data protection" in low:
        return "gdpr"
    if "2022/2065" in low or "digital services act" in low:
        return "dsa"
    if "2022/1925" in low or "digital markets act" in low:
        return "dma"
    if "2022/2555" in low:
        return "nis2"
    return ""


def _resolve_by_number(num: str) -> Optional[Instrument]:
    """Map a 'YYYY/NN' or 'NN/YYYY' citation number to a registry instrument."""
    norm = num.strip()
    for inst in _INSTRUMENTS:
        # canonical strings embed the number; cheap containment check.
        if norm in inst.canonical:
            return inst
    return None


def _resolve_by_celex(celex: str) -> Optional[Instrument]:
    for inst in _INSTRUMENTS:
        if inst.celex and inst.celex == celex:
            return inst
    return None


def _nearest_relation(content: str, pos: int) -> tuple[str, Dimension]:
    """Find the relation verb closest *before* a citation at ``pos``."""
    start = max(0, pos - _RELATION_WINDOW)
    window = content[start:pos]
    best: tuple[int, str, Dimension] | None = None
    for pat, rel, dim in _RELATIONS:
        for m in pat.finditer(window):
            # distance from the end of the window (closest to the citation)
            dist = len(window) - m.end()
            if best is None or dist < best[0]:
                best = (dist, rel, dim)
    if best is None:
        return ("refers-to", Dimension.RELATIONAL)
    return (best[1], best[2])


def extract_cross_references(content: str, *, host_key: str | None = None) -> list[CrossReference]:
    """Find references to OTHER instruments in ``content``.

    Deduplicates by target instrument key (or by raw citation when the target
    can't be resolved to the registry). The strongest/closest relation verb
    seen for each target wins; counts accumulate.
    """
    if host_key is None:
        host_key = infer_host_instrument(content)

    found: dict[str, CrossReference] = {}

    def _add(inst: Optional[Instrument], matched: str, pos: int, raw_celex: str = "") -> None:
        # Resolve identity + dedup key.
        if inst is not None:
            key = inst.key
            canonical = inst.canonical
            celex = inst.celex or raw_celex
        else:
            key = f"raw:{matched.lower()}"
            canonical = matched
            celex = raw_celex
        # Drop self-references.
        if inst is not None and host_key and inst.key == host_key:
            return
        rel, dim = _nearest_relation(content, pos)
        existing = found.get(key)
        if existing is None:
            found[key] = CrossReference(
                target_key=(inst.key if inst else ""),
                target_canonical=canonical,
                target_celex=celex,
                relation=rel,
                dimension=dim.value,
                matched_text=matched,
                count=1,
            )
        else:
            existing.count += 1
            # Prefer a structural relation over a relational one if seen.
            if dim == Dimension.STRUCTURAL and existing.dimension == Dimension.RELATIONAL.value:
                existing.relation = rel
                existing.dimension = dim.value

    # 1. Full "Regulation (EU) NNNN/NN" / "Directive NNNN/NN/EC" citations.
    for pat in (_REG_CITE_RE, _DIR_CITE_RE):
        for m in pat.finditer(content):
            inst = _resolve_by_number(m.group("num"))
            _add(inst, m.group(0), m.start())

    # 2. CELEX identifiers.
    for m in _CELEX_RE.finditer(content):
        inst = _resolve_by_celex(m.group("celex"))
        _add(inst, m.group("celex"), m.start(), raw_celex=m.group("celex"))

    # 3. Short names (GDPR, NIS2, …) — only as whole words.
    for inst in _INSTRUMENTS:
        if inst.key == host_key:
            continue
        for name in inst.short_names:
            for m in re.finditer(r"(?<!\w)%s(?!\w)" % re.escape(name), content):
                _add(inst, name, m.start())
                break  # one hit per short name is enough to register the target

    return list(found.values())


# ---------------------------------------------------------------------------
# ND dispatcher
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
