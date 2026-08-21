# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The ingest path that grows the legal-entity corpus from real content.

The seed (`legal_world.seed_world`) is a starting point; the corpus has to grow
when a user drops a regulation into the folder or the ingestor scrapes a page that
cites a law the corpus does not yet hold. This module is the bridge: it reuses the
existing cross-reference extractor (which already recognises GDPR / AI Act / DSA /
CELEX in text) and turns every instrument it finds into a corpus candidate —
``code``, canonical ``name``, a synthesised EUR-Lex **ELI URL** from the CELEX
key, and domain tags — then feeds them through ``legal_corpus.ingest_from_extraction``.

So the same recognition that builds the cross-reference graph also keeps the
retrievable entity corpus current, with no extra parsing. ``ingest_document`` is
the one call a scanner makes per document; ``register_into_ingest`` is the
best-effort hook wired into the folder ingest pipeline.

Pure stdlib; no network (synthesising an ELI URL is string work — *validating*
that it resolves is ``corpus_validate`` / ``source_validator``'s job).
"""

from __future__ import annotations

from typing import Optional

from ..crossref_extractor import (extract_cross_references, infer_host_instrument,
                                 _INSTRUMENTS)
from ..legal_corpus import EntityRegistry


# instrument key → digital-law domain tag(s)
_DOMAINS: dict[str, tuple[str, ...]] = {
    "gdpr": ("data",), "ai-act": ("ai",), "dsa": ("platform",),
    "dma": ("digital-markets",), "nis2": ("cyber",), "cra": ("cyber",),
    "dora": ("cyber", "finance"), "data-act": ("data",),
    "data-governance-act": ("data",), "eidas": ("digital-identity",),
    "dsm-directive": ("copyright",), "software-directive": ("copyright",),
}

# instrument key → canonical short slug used as the corpus code (align with seed).
# Empty since RVND consumes the legal plane's instrument catalogue, whose keys
# ARE the short corpus codes (``dga``, ``ai-act``, ``data-act`` …). The old
# ``{"data-governance-act": "dga"}`` bridged RVND's retired long-form local key
# to the seed code; with the plane's ``InstrumentRef.code`` = the short code,
# key and corpus code are identical and no translation remains. Kept as a named
# (empty) map because ``adapters.norm`` and the quarantined rule_registry import
# it; extend it only if the plane ever reintroduces a long-form key.
_CODE_ALIASES: dict[str, str] = {}
_CODE_TO_KEY = {v: k for k, v in _CODE_ALIASES.items()}


def ids_for_code(code: str) -> dict:
    """External identifiers the instrument catalogue holds for a corpus ``code``.
    Returns whatever the catalogue knows (a CELEX today) as a namespace->value
    map, or ``{}`` when it knows none — a national statute without a CELEX stays
    on its ``source`` key. Neutral: no scheme is assumed; extend as the catalogue
    grows to carry ECLI or national identifiers."""
    key = _CODE_TO_KEY.get(code, code)
    inst = next((i for i in _INSTRUMENTS if i.key == key), None)
    return {"celex": inst.celex} if inst and inst.celex else {}


def celex_to_eli(celex: str) -> Optional[str]:
    """Synthesise the canonical EUR-Lex ELI URL from a CELEX id.

    CELEX = sector(1) + year(4) + type(1 letter) + number(4), e.g. 32016R0679 →
    https://eur-lex.europa.eu/eli/reg/2016/679/oj . Returns None for a CELEX whose
    type isn't a legislative act we map.
    """
    celex = (celex or "").strip()
    if len(celex) != 10 or not celex[:5].isdigit() or not celex[6:].isdigit():
        return None
    year = celex[1:5]
    type_letter = celex[5].upper()
    number = str(int(celex[6:]))           # strip leading zeros
    kind = {"R": "reg", "L": "dir", "D": "dec"}.get(type_letter)
    if kind is None:
        return None
    return f"https://eur-lex.europa.eu/eli/{kind}/{year}/{number}/oj"


def candidates_from_text(content: str) -> list[dict]:
    """Every instrument recognised in ``content`` as corpus-entity candidates —
    EU instruments via the cross-reference extractor (incl. the host instrument
    the document *is*) AND German national statutes via the national-citation
    recogniser. So a clause citing § 286 BGB maps just like one citing the GDPR.
    """
    keys: dict[str, str] = {}      # instrument key → celex
    host = infer_host_instrument(content)
    if host:
        keys[host] = next((i.celex for i in _INSTRUMENTS if i.key == host), "")
    for ref in extract_cross_references(content, host_key=host):
        if ref.target_key:
            keys[ref.target_key] = ref.target_celex or keys.get(ref.target_key, "")

    out: list[dict] = []
    for key, celex in keys.items():
        inst = next((i for i in _INSTRUMENTS if i.key == key), None)
        if inst is None:
            continue
        out.append({
            "code": _CODE_ALIASES.get(key, key),
            "name": inst.canonical,
            "kind": "instrument",
            "url": celex_to_eli(celex) if celex else None,
            "jurisdiction": "EU",
            "domains": _DOMAINS.get(key, ()),
            "celex": celex,
        })
    # national (German) statute citations — § / Art. + abbreviation
    from ..national_citations import to_candidates as _national_candidates
    seen = {c["code"] for c in out}
    for nc in _national_candidates(content):
        if nc["code"] not in seen:
            out.append(nc)
            seen.add(nc["code"])
    return out


def ingest_document(registry: EntityRegistry, content: str, *,
                    source: str = "ingest") -> dict:
    """Scan one document's text and register every legal instrument it cites into
    the corpus. Idempotent (delegates to the registry). Returns the ingest
    summary plus the list of instrument codes found."""
    candidates = candidates_from_text(content)
    summary = registry.ingest_from_extraction(candidates, source=source)
    summary["found"] = [c["code"] for c in candidates]
    return summary


def register_into_corpus(folder: str, content: str, *,
                         log_root: Optional[str] = None,
                         source: str = "ingest") -> dict:
    """Best-effort hook for the folder ingest pipeline: open the folder's corpus,
    register any laws cited in ``content``, never raise into the caller."""
    try:
        reg = EntityRegistry(folder, log_root=log_root)
        return ingest_document(reg, content, source=source)
    except Exception as exc:                                   # noqa: BLE001
        return {"ingested": [], "error": f"{type(exc).__name__}: {exc}"}
