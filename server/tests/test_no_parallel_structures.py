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

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "rvnd"

# Parallel structures already retired — must stay absent (deleted + unimported).
# Grows as S1-S4 land. (``ingest/policy.py`` is fenced separately below: its stem
# ``policy`` collides with the live ``workspaces/policy.py`` policy loader.)
RETIRED: tuple[str, ...] = (
    "policy_ingest.py",       # governance compiler -> loomground_ingest.governance
    "genre_router.py",        # -> loomground_ingest.governance.genre_router
    "legal_norm_splitter.py", # -> loomground_ingest.governance.legal_norm_splitter
    "policy_normalise.py",    # -> loomground_ingest.governance.policy_normalise
)

# Known parallel structures still being retired (tracked debt, not yet fenced):
#   languages/ingest: hohfeld.py, rule_extractor.py,
#     rule_extractor_llm.py, legal_extractors.py, crossref_extractor.py,
#     instrument_obligation_extractor.py
#   (deontic.py is RETIRED — fenced by test_rvnd_deontic_language_is_retired below)
#   (legal_connection.py composition is RETIRED — now a consumer shim over
#    loomground-legal; fenced by test_legal_connection_composition_is_retired)
#   versum stores: memory.py (WorkspaceMemory knowledge role — audit chain stays),
#     legal_corpus.py, legal_world.py, world_corpus_loader.py, world_relations.py
#   (governance_kg.path + knowledge.subgraph traversal is RETIRED — consume the
#    solver graph API; fenced by test_solver_graph_traversal_is_consumed)


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
            # Fence the retired MODULE by its old import paths — a returned
            # parallel structure would be imported as ``(workspaces.).<stem>``
            # or pulled straight from the ``workspaces``/local package. The
            # names are legitimately re-exported through the sanctioned adapter
            # seam (``from .adapters.ingest.governance import <stem> as ...``),
            # so match the module path, not a bare occurrence of the name.
            patterns = (
                rf"^\s*import\s+{stem}\b",                              # import legal_norm_splitter
                rf"^\s*import\s+workspaces\.{stem}\b",                  # import rvnd.legal_norm_splitter
                rf"^\s*from\s+\.?{stem}\s+import\b",                    # from (.)legal_norm_splitter import ...
                rf"^\s*from\s+workspaces\.{stem}\s+import\b",           # from rvnd.legal_norm_splitter import ...
                rf"^\s*from\s+workspaces\s+import\s+[^\n]*\b{stem}\b",  # from rvnd import legal_norm_splitter
                rf"^\s*from\s+\.\s+import\s+[^\n]*\b{stem}\b",          # from . import legal_norm_splitter
            )
            if any(re.search(p, text, re.MULTILINE) for p in patterns):
                reimported.append(f"{path.relative_to(SRC)} -> {stem}")
    assert not reimported, f"retired parallel structure re-imported: {reimported}"


def test_rvnd_deontic_language_is_retired() -> None:
    """RVND's parallel deontic core + its ``DeonticFormulaND`` nD-facet dispatch
    (``deontic.py``) are gone. The deontic GRAMMAR is consumed from the
    ``deontic`` package via ``adapters/deontic.py``; the text→deontic-pair wiring
    lives in ``deontic_facets.py``. Fenced apart from RETIRED because its stem
    ``deontic`` collides with the sanctioned package import ``import deontic``
    (the adapter seam + the mcp health probe) — so we fence the RVND MODULE path
    (``.deontic`` / ``rvnd.deontic``), never the bare package."""
    assert not (SRC / "deontic.py").exists(), (
        "workspaces/deontic.py (RVND-grown deontic language) reappeared")
    # Absolute forms name the retired RVND module wherever they appear.
    absolute = (
        r"^\s*from\s+workspaces\.deontic\s+import\b",             # from rvnd.deontic import ...
        r"^\s*import\s+workspaces\.deontic\b",                    # import rvnd.deontic
        r"^\s*from\s+workspaces\s+import\s+[^\n]*\bdeontic\b",    # from rvnd import deontic
    )
    # Single-dot forms resolve against the importing file's OWN package: inside
    # adapters/ they name adapters.deontic (the sanctioned seam), at the package
    # root they name the retired rvnd.deontic. Same text, opposite meaning — so
    # routing a module THROUGH the seam must not read as re-importing the copy
    # the seam replaced.
    relative = (
        r"^\s*from\s+\.deontic\s+import\b",                       # from .deontic import ...
        r"^\s*from\s+\.\s+import\s+[^\n]*\bdeontic\b",          # from . import deontic
    )
    offenders: list[str] = []
    for path in _py_files():
        text = path.read_text(encoding="utf-8")
        pats = absolute if path.parent.name == "adapters" else absolute + relative
        if any(re.search(p, text, re.MULTILINE) for p in pats):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"retired RVND deontic module re-imported: {offenders}"


def test_legal_connection_composition_is_retired() -> None:
    """RVND's parallel legal-connection composition ENGINE is retired.

    ``legal_connection.py`` is now a consumer shim over loomground-legal's
    connection algebra (the solver ``RelationAlgebra`` built from that package's
    ``connections.json``). It must carry NO composition table or hand-rolled
    fold of its own, and must source the algebra + vocabulary from
    ``loomground_legal`` — so the parallel engine cannot silently return."""
    src = (SRC / "legal_connection.py").read_text(encoding="utf-8")
    # consumes loomground-legal's algebra THROUGH the adapters/ seam (the
    # workspaces boundary rule: no direct upstream import outside adapters/).
    assert "adapters.legal" in src, (
        "legal_connection must consume loomground-legal via the adapters/legal seam")
    seam = (SRC / "adapters" / "legal.py").read_text(encoding="utf-8")
    assert "from loomground_legal" in seam, (
        "adapters/legal must re-export loomground-legal's connection algebra")
    assert "_connection_algebra()" in src, (
        "legal_connection must build on the consumed RelationAlgebra")
    # the retired engine: a local composition table + its left-fold
    assert "_COMPOSE" not in src, (
        "legal_connection re-grew a local composition table (_COMPOSE)")
    # composition must delegate to the consumed algebra, not re-implement a fold
    assert "_ALG.compose_path" in src and "_ALG.compose" in src, (
        "legal_connection.compose/compose_path must delegate to the algebra")


def test_solver_graph_traversal_is_consumed() -> None:
    """Graph traversal is the solver's, not RVND's. ``governance_kg.path``
    composes over the solver's ``to_undirected`` + ``compose_paths`` (min_hops),
    and the versum knowledge adapter's ``subgraph`` delegates to the solver's
    ``neighborhood`` — neither hand-rolls a BFS/frontier of its own."""
    gk = (SRC / "governance_kg.py").read_text(encoding="utf-8")
    assert "to_undirected(" in gk and "compose_paths(" in gk, (
        "governance_kg.path must consume the solver's to_undirected + compose_paths")
    kn = (SRC / "adapters" / "versum" / "knowledge.py").read_text(encoding="utf-8")
    assert "neighborhood(" in kn, (
        "knowledge.subgraph must consume the solver's neighborhood")
    for name, text in (("governance_kg.py", gk),
                       ("adapters/versum/knowledge.py", kn)):
        assert "frontier" not in text, f"{name} re-grew a local frontier BFS walk"


def test_rvnd_grown_policy_ingester_is_retired() -> None:
    """The RVND-grown ``ingest/policy.py`` (PolicyIngester) is gone; the consumed
    GovernanceIngester replaces it. Fenced apart from RETIRED because its stem
    ``policy`` collides with the live ``workspaces/policy.py`` policy loader."""
    assert not (SRC / "ingest" / "policy.py").exists(), (
        "ingest/policy.py (RVND-grown PolicyIngester) reappeared")
    offenders: list[str] = []
    for path in _py_files():
        text = path.read_text(encoding="utf-8")
        if "ingest.policy import" in text or "from .policy import PolicyIngester" in text:
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"retired PolicyIngester re-imported: {offenders}"
