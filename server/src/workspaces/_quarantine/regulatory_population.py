# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""QUARANTINED — the MIGRATED portion of the original regulatory_population.

This is the instrument catalogue (``CODE`` / ``DOMAIN`` / ``TRANCHES``) and the
CSV ``load_instruments`` loader as they stood in RVND before the world-stack cut,
now consumed from ``loomground-legal`` via ``workspaces.adapters.legal``. Kept
verbatim ONLY so the retirement can be verified against the original before
deletion; dead-on-arrival, never imported by live code (fenced by
``tests/test_consumed_modules.py``). The folder-runtime writers
(``populate_*``), the env resolver ``default_csv()`` and ``SourceClass``
derivation STAYED in the live module.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional


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


def load_instruments(csv_path: Optional[str | Path] = None) -> dict[str, dict]:
    """CELEX → row dict, from the companion's instrument registry."""
    path = Path(csv_path) if csv_path else None
    if path is None or not Path(path).exists():
        raise FileNotFoundError("instruments.csv not found; pass csv_path explicitly")
    with open(path, encoding="utf-8") as fh:
        return {r["celex"]: r for r in csv.DictReader(fh)}
