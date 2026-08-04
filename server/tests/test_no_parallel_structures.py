# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Conformance gate: RVND is a consumer, not a re-implementer.

RVND must have NO parallel structures of the consumed loomground tools
(languages = deontic + governance + the 5D; ingest; versum; solver). It wires
and adapts them; it never re-grows them. This gate fences that invariant:

* the ingest registry carries ONLY the consumed grammar ingester;
* the 5D dimension model is never re-declared locally;
* each parallel structure that has been RETIRED stays gone — deleted and
  unimported — so it can never silently return.

As each retirement slice (S1-S4) lands, move its modules from the tracking
comment below into ``RETIRED`` and this gate makes the removal permanent.
"""
from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "workspaces"

# Parallel structures already retired — must stay absent (deleted + unimported).
# Grows as S1-S4 land. (Empty until the first stack retirement completes.)
RETIRED: tuple[str, ...] = ()

# Known parallel structures still being retired (tracked debt, not yet fenced):
#   languages/ingest: deontic.py, hohfeld.py, rule_extractor.py,
#     rule_extractor_llm.py, policy_ingest.py, legal_norm_splitter.py,
#     legal_extractors.py, crossref_extractor.py, instrument_obligation_extractor.py
#   versum stores: memory.py (WorkspaceMemory), legal_corpus.py,
#     legal_world.py, world_corpus_loader.py, world_relations.py
#   solver composition: governance_kg.py (path), legal_connection.py (compose*)


def _py_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def test_ingest_registry_is_consumed_only() -> None:
    """The ingest registry must register the consumed DeonticIngester and must
    NOT register the retired RVND-grown PolicyIngester. RVND has no ingest of
    its own."""
    reg_src = (SRC / "ingest" / "__init__.py").read_text(encoding="utf-8")
    assert "DeonticIngester" in reg_src, "consumed DeonticIngester must be registered"
    assert "reg.register(PolicyIngester" not in reg_src, (
        "PolicyIngester (RVND-grown ingest) must not be registered — "
        "the parallel ingester came back"
    )


def test_five_d_is_consumed_never_redeclared() -> None:
    """The fixed 5D model is owned by versum/solver. RVND must never declare a
    local Dimension enum or composition table outside the thin adapters seam."""
    offenders: list[str] = []
    for path in _py_files():
        if "adapters" in path.parts:
            continue  # the sanctioned re-export seam
        text = path.read_text(encoding="utf-8")
        if "class Dimension" in text or "COMPOSITION_TABLE =" in text:
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"local 5D re-declaration (parallel structure): {offenders}"


def test_retired_parallel_structures_stay_gone() -> None:
    """Every retired parallel structure must be absent and unimported."""
    present = [rel for rel in RETIRED if (SRC / rel).exists()]
    assert not present, f"retired parallel structure reappeared as a file: {present}"

    stems = {Path(rel).stem for rel in RETIRED}
    reimported: list[str] = []
    for path in _py_files():
        text = path.read_text(encoding="utf-8")
        for stem in stems:
            if f"import {stem}" in text or f"from .{stem} import" in text or f"from workspaces.{stem} import" in text:
                reimported.append(f"{path.relative_to(SRC)} -> {stem}")
    assert not reimported, f"retired parallel structure re-imported: {reimported}"
