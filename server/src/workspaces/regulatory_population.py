# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Populate the legal-entity graph from an instrument registry, in tranches.

Bring your own instrument registry CSV (CELEX, dates, supersession, official
source URL): pass a path, or set ``WORKSPACE_INSTRUMENTS_CSV``. The core ships no
corpus. This module loads it and fills a folder's
corpus **in domain tranches** — data-protection, then cybersecurity, then
AI-governance — so the graph is built incrementally and each tranche can be
validated before the next is added (the population-in-batches discipline the
companion's ingest-pipeline reference describes).

Each instrument becomes a corpus entity (URL, source class derived from the CELEX
act-type, domain tags), wired with an ``applies_in EU`` edge and the
intra-tranche ``supersedes`` temporal edges read straight from the CSV's
``superseded_by`` column. Mechanism only — the data is the companion's; the
populator is substrate.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from .legal_connection import Connection
from .legal_corpus import EntityRegistry
from .corpus.validate import validate_registry
from .source_classes import SourceClass


# CELEX → canonical corpus code (aligned with the seed + crossref registries)
CODE: dict[str, str] = {
    "31995L0046": "dpd-95", "32016R0679": "gdpr", "32016L1148": "nis1",
    "32022L2555": "nis2", "32024R1689": "ai-act",
    "32022R2065": "dsa", "32022R1925": "dma", "32022R0868": "dga",
    "32023R2854": "data-act", "32024R2847": "cra", "32014R0910": "eidas",
    "32002L0058": "eprivacy",
}
DOMAIN: dict[str, tuple[str, ...]] = {
    "dpd-95": ("data",), "gdpr": ("data",), "eprivacy": ("data",),
    "nis1": ("cyber",), "nis2": ("cyber",), "cra": ("cyber",),
    "ai-act": ("ai",), "dsa": ("platform",), "dma": ("digital-markets",),
    "dga": ("data",), "data-act": ("data",), "eidas": ("digital-identity",),
}

# Ordered tranches, mirroring the companion's domain skills.
TRANCHES: list[tuple[str, list[str]]] = [
    ("data-protection", ["31995L0046", "32016R0679", "32002L0058"]),
    ("cybersecurity",   ["32016L1148", "32022L2555", "32024R2847"]),
    ("ai-governance",   ["32024R1689"]),
    ("platform-content", ["32022R2065"]),
    ("digital-markets", ["32022R1925"]),
    ("data-economy",    ["32022R0868", "32023R2854", "32014R0910"]),
]


def default_csv() -> Optional[Path]:
    """The instrument registry CSV: ``WORKSPACE_INSTRUMENTS_CSV`` if set, else
    ``~/.workspace/instruments.csv``. None if absent (the core ships no corpus)."""
    import os
    env = os.environ.get("WORKSPACE_INSTRUMENTS_CSV", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None
    p = Path.home() / ".workspace" / "instruments.csv"
    return p if p.exists() else None


def load_instruments(csv_path: Optional[str | Path] = None) -> dict[str, dict]:
    """CELEX → row dict, from the companion's instrument registry."""
    path = Path(csv_path) if csv_path else default_csv()
    if path is None or not Path(path).exists():
        raise FileNotFoundError("instruments.csv not found; pass csv_path explicitly")
    with open(path, encoding="utf-8") as fh:
        return {r["celex"]: r for r in csv.DictReader(fh)}


def _source_class(celex: str) -> SourceClass:
    return (SourceClass.SUPRANATIONAL_REGULATION if celex[5].upper() == "R"
            else SourceClass.SUPRANATIONAL_DIRECTIVE)


def populate_tranche(reg: EntityRegistry, instruments: dict[str, dict],
                     celex_list: list[str], *,
                     source: str = "regulatory-companion") -> dict:
    """Ingest one tranche's instruments + edges into the corpus. Idempotent."""
    added: list[str] = []
    edges = 0
    for celex in celex_list:
        row = instruments[celex]
        code = CODE.get(celex, celex.lower())
        reg.ingest_entity(
            code=code, name=row["short"], kind="instrument",
            url=row.get("source") or None, jurisdiction="EU",
            domains=DOMAIN.get(code, ()), source=source,
            facets={"celex": celex, "source_class": _source_class(celex).value,
                    "in_force_from": row.get("in_force_from", "")})
        reg.ingest_edge(subject=code, connection=Connection.APPLIES_IN.value,
                        obj="EU", basis="directly applicable / transposed", source=source)
        edges += 1
        added.append(code)
    # supersession edges (newer supersedes older) where both ends are known
    for celex in celex_list:
        sup = instruments[celex].get("superseded_by")
        if sup and sup in CODE:
            reg.ingest_edge(subject=CODE[sup], connection=Connection.SUPERSEDES.value,
                            obj=CODE[celex], basis=instruments[celex].get("note", ""),
                            source=source)
            edges += 1
    return {"tranche": celex_list, "added": added, "edges": edges}


def populate_in_tranches(reg: EntityRegistry, instruments: dict[str, dict],
                         *, source: str = "regulatory-companion") -> list[dict]:
    """Run every tranche in order, validating the corpus after each. Returns a
    per-tranche record: what was added + the validation summary at that point."""
    out: list[dict] = []
    for name, celex_list in TRANCHES:
        added = populate_tranche(reg, instruments, celex_list, source=source)
        report = validate_registry(reg)
        out.append({"name": name, **added,
                    "cumulative_entities": report["summary"]["entities"],
                    "validation": report["summary"]})
    return out
