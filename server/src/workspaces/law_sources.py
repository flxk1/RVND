# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Law-source adapters — feed the corpus and the currency registry from real law.

Today the currency registry is hand-curated (`instruments.csv`). This module is
the seam that lets a live source populate it instead. It has three parts:

  1. a REGISTRY of law sources per jurisdiction (EU / DE / UK / US), each with the
     identifier scheme it uses and a connector hint — so "which sources exist for
     this legal system" is data, not tribal knowledge;
  2. a ``LawSourceConnector`` protocol — the contract any fetcher (an MCP
     connector like CourtListener / Legal Data Hunter, or a direct API client)
     implements; the substrate consumes its output and never the API directly;
  3. an EUR-Lex **adapter** that *normalises* EUR-Lex-shaped instrument metadata
     into the exact rows ``workspaces.currency.CurrencyRegistry`` expects, and into
     corpus documents. The normalisation is the testable, deterministic core; the
     network fetch is injected (a connector), so this module needs no network and
     never guesses a date — it only maps the source's own dates.

Pure stdlib; no network here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Protocol


@dataclass(frozen=True)
class LawSource:
    id: str
    jurisdiction: str
    name: str
    kind: str               # "primary-legislation" | "case-law" | "gazette"
    id_scheme: str          # "CELEX" | "ELI" | "national" | "neutral-citation"
    base_url: str
    connector_hint: str = ""    # an MCP/connector that can fetch this source


# Sources per jurisdiction — matches the legal_systems packs (EU/DE/UK/US).
_SOURCES: dict[str, list[LawSource]] = {
    "EU": [
        LawSource("eur-lex", "EU", "EUR-Lex (Official Journal, consolidated law)",
                  "primary-legislation", "CELEX", "https://eur-lex.europa.eu",
                  "EUR-Lex SPARQL/REST, or a legal MCP with EU coverage"),
        LawSource("curia", "EU", "CURIA (CJEU case law)", "case-law", "ECLI",
                  "https://curia.europa.eu", "Legal Data Hunter / CourtListener-style MCP"),
    ],
    "DE": [
        LawSource("gesetze-im-internet", "DE", "Gesetze im Internet (Bundesrecht)",
                  "primary-legislation", "national", "https://www.gesetze-im-internet.de",
                  "openlegaldata.io / a DE legal MCP"),
        LawSource("rechtsprechung-im-internet", "DE", "Rechtsprechung im Internet",
                  "case-law", "national", "https://www.rechtsprechung-im-internet.de",
                  "openlegaldata.io"),
        LawSource("bgbl", "DE", "Bundesgesetzblatt", "gazette", "national",
                  "https://www.recht.bund.de", ""),
    ],
    "UK": [
        LawSource("legislation-gov-uk", "UK", "legislation.gov.uk",
                  "primary-legislation", "national", "https://www.legislation.gov.uk",
                  "legislation.gov.uk API"),
        LawSource("bailii", "UK", "BAILII (case law)", "case-law", "neutral-citation",
                  "https://www.bailii.org", "CourtListener-style MCP"),
    ],
    "US": [
        LawSource("govinfo", "US", "govinfo (U.S.C., C.F.R., Public Laws)",
                  "primary-legislation", "national", "https://www.govinfo.gov",
                  "govinfo API"),
        LawSource("courtlistener", "US", "CourtListener (federal + state case law)",
                  "case-law", "neutral-citation", "https://www.courtlistener.com",
                  "CourtListener MCP"),
    ],
}


def sources_for(jurisdiction: str) -> list[LawSource]:
    return list(_SOURCES.get((jurisdiction or "").upper(), []))


def available() -> list[str]:
    return sorted(_SOURCES)


def all_sources() -> list[LawSource]:
    return [s for js in _SOURCES.values() for s in js]


# ── The connector contract (implemented by an MCP/API client elsewhere) ──────

@dataclass
class InstrumentRecord:
    """Normalised instrument metadata — exactly what the currency registry needs."""
    celex: str
    in_force_from: Optional[str] = None
    superseded_by: Optional[str] = None
    superseded_from: Optional[str] = None
    consolidation_version: Optional[str] = None
    title: str = ""

    def to_currency_row(self) -> dict[str, Any]:
        return {"celex": self.celex, "in_force_from": self.in_force_from,
                "superseded_by": self.superseded_by, "superseded_from": self.superseded_from,
                "consolidation_version": self.consolidation_version}


class LawSourceConnector(Protocol):
    source_id: str
    def fetch_instrument(self, ref: str) -> dict[str, Any]: ...
    def search(self, query: str, *, jurisdiction: str = "", as_of: str = "") -> list[dict[str, Any]]: ...


# ── EUR-Lex adapter: normalise EUR-Lex records → registry rows / corpus docs ──

class EurLexAdapter:
    """Maps EUR-Lex-shaped records into the Workspace's currency + corpus shapes.

    ``fetch`` is an injected connector callable (an MCP/REST client). With no
    fetcher attached, the adapter still normalises records you already hold — the
    transform is the deterministic, tested part; fetching is the external part.
    """

    source_id = "eur-lex"

    # EUR-Lex field aliases → our fields. Real EUR-Lex/ELI metadata varies; this
    # maps the common keys without ever inventing a value.
    _DATE_KEYS = {
        "in_force_from": ("dateOfEffect", "date_force", "first_date_entry_in_force", "inForce"),
        "superseded_from": ("dateEndValidity", "date_end", "dateNoLongerInForce"),
        "consolidation_version": ("dateOfConsolidation", "consolidation"),
    }

    def __init__(self, fetch: Optional[Callable[[str], dict[str, Any]]] = None):
        self._fetch = fetch

    @staticmethod
    def _first(raw: dict[str, Any], keys: Iterable[str]) -> Optional[str]:
        for k in keys:
            v = raw.get(k)
            if v:
                return str(v)
        return None

    def normalise(self, raw: dict[str, Any]) -> InstrumentRecord:
        celex = str(raw.get("celex") or raw.get("CELEX") or raw.get("id") or "").strip()
        rec = InstrumentRecord(celex=celex, title=str(raw.get("title", "")))
        rec.in_force_from = self._first(raw, self._DATE_KEYS["in_force_from"])
        rec.superseded_from = self._first(raw, self._DATE_KEYS["superseded_from"])
        rec.consolidation_version = self._first(raw, self._DATE_KEYS["consolidation_version"])
        # repeal relationship (the model never infers this — the source states it)
        rep = raw.get("repealedBy") or raw.get("superseded_by")
        if rep:
            rec.superseded_by = str(rep)
        return rec

    def to_currency_rows(self, records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.normalise(r).to_currency_row() for r in records]

    def to_corpus_docs(self, records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in records:
            rec = self.normalise(r)
            out.append({"id": rec.celex, "celex": rec.celex,
                        "text": str(r.get("text") or r.get("body") or rec.title),
                        "source": "eur-lex", "title": rec.title})
        return out

    def fetch_instrument(self, ref: str) -> dict[str, Any]:
        if self._fetch is None:
            raise NotImplementedError(
                "EurLexAdapter has no fetcher attached — inject an MCP/REST connector. "
                "Normalisation (normalise/to_currency_rows) works on records you already hold.")
        return self._fetch(ref)


def build_registry_from_records(records: Iterable[dict[str, Any]],
                                adapter: Optional[EurLexAdapter] = None):
    """Convenience: EUR-Lex records → a live CurrencyRegistry (closing the loop to
    `workspaces.currency`)."""
    from .currency import CurrencyRegistry
    adapter = adapter or EurLexAdapter()
    return CurrencyRegistry.from_rows(adapter.to_currency_rows(records))


def populate_from_connector(refs: Iterable[str], connector: LawSourceConnector,
                            *, adapter: Optional[EurLexAdapter] = None):
    """Attach a live source: fetch each instrument via ``connector`` (an MCP/REST
    client implementing :class:`LawSourceConnector`) and build a live
    CurrencyRegistry. The connector is the ONLY network-touching part; the
    normalisation and dating downstream are deterministic and never guess. Plug a
    real connector (e.g. a EUR-Lex / Legal-Data-Hunter MCP) in place of the stub
    and the registry self-populates from source."""
    records = [connector.fetch_instrument(ref) for ref in refs]
    return build_registry_from_records(records, adapter)
