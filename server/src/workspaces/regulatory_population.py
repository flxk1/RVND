# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Populate the legal-entity graph from an instrument registry, in tranches.

The instrument **catalogue** (CELEX → canonical code, domain tags, the ordered
domain tranches) and the CSV **loader** are RETIRED into ``loomground-legal`` and
consumed through the ``adapters/legal`` seam (``CODE`` / ``DOMAIN`` / ``TRANCHES``
/ ``load_instruments``). What STAYS in RVND is the **folder runtime**: the
``populate_*`` writers that ingest each tranche into a folder's
``EntityRegistry`` (with the signed mutation-log audit and per-tranche
validation), the env-configured CSV resolver (``default_csv()`` /
``WORKSPACE_INSTRUMENTS_CSV``), and the ``SourceClass`` derivation — none of which
is legal-domain data.

Bring your own instrument registry CSV (CELEX, dates, supersession, official
source URL): pass a path, or set ``WORKSPACE_INSTRUMENTS_CSV``. The core ships no
corpus. The graph is filled **in domain tranches** — data-protection, then
cybersecurity, then AI-governance — so each tranche can be validated before the
next is added.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .legal_connection import Connection
from .legal_corpus import EntityRegistry
from .corpus.validate import validate_registry
from .source_classes import SourceClass
# The instrument catalogue + CSV loader are the plane's, consumed via the seam.
from .adapters.legal import CODE, DOMAIN, TRANCHES, load_instruments

__all__ = ["CODE", "DOMAIN", "TRANCHES", "load_instruments",
           "default_csv", "WORKSPACE_INSTRUMENTS_CSV",
           "populate_tranche", "populate_in_tranches"]

#: The env var naming the instrument-registry CSV (companion data the core does
#: not ship).
WORKSPACE_INSTRUMENTS_CSV = "WORKSPACE_INSTRUMENTS_CSV"


def default_csv() -> Optional[Path]:
    """The instrument registry CSV: ``WORKSPACE_INSTRUMENTS_CSV`` if set, else
    ``~/.workspace/instruments.csv``. None if absent (the core ships no corpus).
    RVND folder runtime, injected into the consumed ``load_instruments``."""
    import os
    env = os.environ.get(WORKSPACE_INSTRUMENTS_CSV, "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None
    p = Path.home() / ".workspace" / "instruments.csv"
    return p if p.exists() else None


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
