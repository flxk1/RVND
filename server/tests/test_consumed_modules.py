# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Standing gate: the norm-runtime plane is CONSUMED, not re-grown.

RVND's eight norm-runtime twins (rule extraction, obligation state, subsumption,
the span-norm registry, the obligation scheduler) are retired in favour of
``loomground-norm``. Each live twin is now a thin re-export shim over
``workspaces.adapters.norm``; the original implementations are quarantined
(dead-on-arrival, never imported). This gate makes that permanent:

  (a) no live ``workspaces`` module imports from ``_quarantine/``;
  (b) each norm-runtime twin is a shim over ``adapters.norm`` — it imports from
      there and does NOT re-grow the retired behavior locally;
  (c) every ``loomground-*`` distribution RVND consumes is either declared in
      ``pyproject.toml`` or on the explicit ``_PIN_PENDING`` allowlist (for
      packages consumed-but-undeclared until they are released) — so a NEW
      orphan fails, while today is green.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACES_ROOT = REPO_ROOT / "server" / "src" / "workspaces"

# The eight norm-runtime twins now consumed from loomground-norm through the seam.
NORM_TWINS = (
    "rule_extractor.py",
    "rule_extractor_llm.py",
    "subsumption_path.py",
    "subsumption_validator.py",
    "obligation_runtime.py",
    "hohfeld.py",
    "rule_registry.py",
    "obligation_scheduler.py",
)

# Definitions whose presence in a twin file would mean the retired behavior was
# re-grown locally rather than consumed through the seam.
FORBIDDEN_LOCAL_DEFS = (
    r"def\s+extract_rules\s*\(",
    r"def\s+extract_rules_llm\s*\(",
    r"def\s+attach_incidents\s*\(",
    r"def\s+classify_incident\s*\(",
    r"def\s+validate\s*\(",
    r"def\s+build\s*\(",
    r"def\s+tick\s*\(",
    r"def\s+place_span\s*\(",
    r"def\s+place_into_registry\s*\(",
    r"class\s+RuleRegistry\b",
    r"class\s+ObligationRegistry\b",
    r"class\s+ObligationScheduler\b",
    r"class\s+RuleFacet\b",
    r"class\s+Subsumption\b",
)

# loomground distributions consumed-but-undeclared until release (git deps not
# yet pinned in pyproject). A new orphan NOT on this list (and not declared)
# fails check (c). Empty now: loomground-norm graduated to a declared git pin.
_PIN_PENDING: tuple[str, ...] = ()

# The opposite category: dists RVND pins to PROVIDE the installable git artifact
# for a plane that imports them transitively but does not declare them
# (loomground-ingest imports loomground_factual at module scope and
# loomground_epistemic lazily, yet declares neither). They are not on any package
# index, so pip cannot resolve them from ingest's abstract requirement — RVND must
# carry the direct-URL pin. RVND does NOT consume them directly, so they are
# exempt from the declared-must-be-consumed direction (check below). Proper
# long-term home: ingest declaring these deps; until then RVND provides them.
_TRANSITIVE_PROVIDER = ("loomground-factual", "loomground-epistemic")

# import-name -> distribution-name for the loomground packages RVND consumes.
_IMPORT_TO_DIST = {
    "loomground_solver": "loomground-solver",
    "loomground_versum": "loomground-versum",
    "versum": "loomground-versum",
    "loomground_governance": "loomground-governance",
    "loomground_deontic": "loomground-deontic",
    "deontic": "loomground-deontic",
    "loomground_ingest": "loomground-ingest",
    "loomground_legal": "loomground-legal",
    "loomground_norm": "loomground-norm",
}


def _live_py_files() -> list[Path]:
    return [p for p in WORKSPACES_ROOT.rglob("*.py")
            if "__pycache__" not in p.parts and "_quarantine" not in p.parts]


def _all_py_files() -> list[Path]:
    return [p for p in WORKSPACES_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


# ── (a) ─────────────────────────────────────────────────────────────────────

def test_no_live_module_imports_from_quarantine() -> None:
    """The quarantined originals are dead code — no live module may import them."""
    pattern = re.compile(r"^\s*(?:from|import)\s+[.\w]*_quarantine\b", re.MULTILINE)
    offenders: list[str] = []
    for path in _live_py_files():
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(WORKSPACES_ROOT)))
    assert not offenders, f"live module imports from _quarantine/: {offenders}"


def test_quarantine_dir_is_present_but_inert() -> None:
    """The quarantine package exists (originals kept for verification) and is
    marked as not-imported-by-live-code."""
    q = WORKSPACES_ROOT / "_quarantine"
    assert q.is_dir(), "expected the _quarantine/ package"
    init = (q / "__init__.py").read_text(encoding="utf-8")
    assert "dead-on-arrival" in init.lower() or "not imported" in init.lower()
    present = sorted(p.name for p in q.glob("*.py") if p.name != "__init__.py")
    assert set(NORM_TWINS) <= set(present), (
        f"quarantine is missing some retired originals: "
        f"{set(NORM_TWINS) - set(present)}")


# ── (b) ─────────────────────────────────────────────────────────────────────

def test_norm_twins_are_shims_over_adapters_norm() -> None:
    """Each norm-runtime twin imports from ``adapters.norm`` and re-grows none of
    the retired behavior locally."""
    seam = re.compile(r"from\s+\.adapters\.norm\s+import|from\s+workspaces\.adapters\.norm\s+import")
    not_shims: list[str] = []
    regrown: list[str] = []
    for name in NORM_TWINS:
        text = (WORKSPACES_ROOT / name).read_text(encoding="utf-8")
        if not seam.search(text):
            not_shims.append(name)
        for pat in FORBIDDEN_LOCAL_DEFS:
            if re.search(pat, text):
                regrown.append(f"{name}: {pat}")
    assert not not_shims, f"twins not consuming adapters.norm: {not_shims}"
    assert not regrown, f"twins re-grew retired norm behavior locally: {regrown}"


def test_adapters_norm_is_the_only_norm_import_site() -> None:
    """``loomground_norm`` is imported in exactly one place — the seam."""
    importers: list[str] = []
    for path in _all_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name == "loomground_norm" or a.name.startswith("loomground_norm.")
                       for a in node.names):
                    importers.append(str(path.relative_to(WORKSPACES_ROOT)))
            elif isinstance(node, ast.ImportFrom) and not node.level:
                if node.module and (node.module == "loomground_norm"
                                    or node.module.startswith("loomground_norm.")):
                    importers.append(str(path.relative_to(WORKSPACES_ROOT)))
    assert set(importers) == {"adapters/norm.py"}, (
        f"loomground_norm must be imported only in adapters/norm.py; got {sorted(set(importers))}")


# ── (c) ─────────────────────────────────────────────────────────────────────

def _declared_loomground_dists() -> set[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data.get("project", {}).get("dependencies", [])
    out: set[str] = set()
    for dep in deps:
        # e.g. "loomground-solver @ git+https://..." → "loomground-solver"
        name = re.split(r"[\s@<>=!~;\[]", dep.strip(), maxsplit=1)[0]
        if name.startswith("loomground-"):
            out.add(name)
    return out


def _consumed_loomground_dists() -> set[str]:
    consumed: set[str] = set()
    for path in _all_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module]
            for mod in names:
                top = mod.split(".", 1)[0]
                if top in _IMPORT_TO_DIST:
                    consumed.add(_IMPORT_TO_DIST[top])
                elif top.startswith("loomground_"):
                    # an unmapped loomground_* import — treat as its dist name so
                    # a NEW orphan surfaces here rather than passing silently.
                    consumed.add(top.replace("_", "-"))
    return consumed


def test_every_consumed_loomground_dist_is_declared_or_pinned() -> None:
    declared = _declared_loomground_dists()
    allowed = declared | set(_PIN_PENDING)
    consumed = _consumed_loomground_dists()
    orphans = sorted(consumed - allowed)
    assert not orphans, (
        f"loomground distributions consumed but neither declared in pyproject "
        f"nor on _PIN_PENDING: {orphans}\n"
        f"  declared: {sorted(declared)}\n"
        f"  pin-pending: {sorted(_PIN_PENDING)}\n"
        f"  consumed: {sorted(consumed)}")


def test_every_declared_loomground_dist_is_consumed() -> None:
    """The reverse of the orphan check above, and the direction it was missing: a
    loomground-* distribution DECLARED as a dependency must actually be imported
    somewhere under server/src.

    A declared-but-unconsumed dependency is the ``orphan = reimplemented`` signal
    the standing consume-vs-regrow gate exists to catch: RVND paid the dependency
    cost but grew its own runtime instead of consuming the module (the historical
    ``loomground_norm`` failure — declared/vendored yet imported nowhere while a
    parallel twin carried the work). Together with
    :func:`test_every_consumed_loomground_dist_is_declared_or_pinned` this makes the
    manifest↔usage relationship bidirectional: nothing consumed-but-undeclared, and
    nothing declared-but-unconsumed.

    (_PIN_PENDING dists are consumed-but-undeclared by construction, so they never
    appear in ``declared`` and cannot trip this check.)"""
    declared = _declared_loomground_dists()
    consumed = _consumed_loomground_dists()
    # _TRANSITIVE_PROVIDER dists are pinned to provide a transitive plane artifact,
    # not consumed by RVND directly — they are declared-but-unconsumed by design.
    unconsumed = sorted(declared - consumed - set(_TRANSITIVE_PROVIDER))
    assert not unconsumed, (
        f"loomground distributions declared in pyproject but imported NOWHERE under "
        f"server/src — declared-but-unconsumed means the capability was reimplemented "
        f"as a parallel structure instead of consumed. Consume the module through its "
        f"adapter seam, or drop the dependency: {unconsumed}\n"
        f"  declared: {sorted(declared)}\n"
        f"  consumed: {sorted(consumed)}")


def test_pin_pending_entries_are_actually_consumed() -> None:
    """Guard against _PIN_PENDING going stale: every pinned-pending dist must
    actually be consumed (else drop it), and must NOT also be declared."""
    declared = _declared_loomground_dists()
    consumed = _consumed_loomground_dists()
    for dist in _PIN_PENDING:
        assert dist in consumed, f"_PIN_PENDING lists {dist} but it is not consumed"
        assert dist not in declared, (
            f"{dist} is declared in pyproject — remove it from _PIN_PENDING")


# ════════════════════════════════════════════════════════════════════════════
# The legal-world stack is CONSUMED, not re-grown.
#
# RVND's world-map stack (the entity model + graph container + reach, the seed
# corpus, the md-table loader, the curated relational enrichment, the instrument
# catalogue, corpus validation, the contract model) is retired in favour of
# ``loomground-legal``, consumed through ``workspaces.adapters.legal``. Unlike
# the norm twins (uniform shims), these are a *parallel-by-role* set: some are
# pure re-export shims (legal_world, corpus/validate, world_corpus_loader,
# world_relations) and some are SPLITs that KEEP their folder-runtime local
# (regulatory_population, contracts/instance, legal_corpus). The fence therefore
# checks, per file: (b1) it consumes the surface through ``adapters.legal``, and
# (b2) it re-grows none of the symbols that MOVED to the package (while the
# kept-local runtime — EntityRegistry, ContractRegistry, populate_*,
# build_enriched_world, default_csv/_default_refdir, the RVND-KG project() — is
# untouched).
# ════════════════════════════════════════════════════════════════════════════

# twin file (relative to WORKSPACES_ROOT) -> regexes for MOVED symbols that must
# NOT be defined locally (they now live in the package, behind the seam).
LEGAL_TWINS: dict[str, tuple[str, ...]] = {
    "legal_world.py": (
        r"class\s+WorldMap\b", r"class\s+Entity\b", r"class\s+WorldEdge\b",
        r"class\s+EntityKind\b", r"def\s+seed_world\s*\(", r"def\s+reach\s*\(",
    ),
    "corpus/validate.py": (
        r"def\s+validate_corpus\s*\(", r"class\s+Finding\b", r"def\s+_authority\s*\(",
    ),
    "world_corpus_loader.py": (
        r"def\s+build_world\s*\(", r"def\s+parse_md\s*\(", r"def\s+_slug\s*\(",
    ),
    "regulatory_population.py": (
        r"def\s+load_instruments\s*\(", r"^CODE\s*[:=]", r"^DOMAIN\s*[:=]",
        r"^TRANCHES\s*[:=]",
    ),
    "world_relations.py": (
        r"def\s+enrich\s*\(", r"^_COE\s*[:=]", r"^_REGULATORS\s*[:=]",
    ),
    "contracts/instance.py": (
        r"class\s+PartyRef\b", r"class\s+ContractInstance\b",
        r"def\s+_lei_checksum_ok\s*\(",
    ),
    "legal_corpus.py": (
        r"class\s+WorldMap\b", r"class\s+Entity\b", r"class\s+EntityKind\b",
        r"def\s+seed_world\s*\(",
    ),
    # crossref_extractor: fully consumes legal.extract_cross_references /
    # infer_host_instrument / INSTRUMENTS — the instrument registry, relation-verb
    # typing, citation regexes and resolution must not regrow here.
    "crossref_extractor.py": (
        r"^_RELATIONS\s*[:=]", r"^_REG_CITE_RE\s*[:=]", r"^_CELEX_RE\s*[:=]",
        r"def\s+_resolve_by_celex\s*\(", r"def\s+_nearest_relation\s*\(",
    ),
    # legal_extractors: its DocumentSummaryExtractor consumes legal.summarize_document
    # and _infer_regulation consumes infer_host_instrument — the doc-kind header
    # regexes and the hardcoded host-inference heuristic must not regrow.
    # (Definition/ArticleReference extractors are NOT yet retired — not fenced here.)
    "legal_extractors.py": (
        r"^_REG_NAME_RE\s*[:=]", r"^_DIR_NAME_RE\s*[:=]", r"^_CASE_NAME_RE\s*[:=]",
        r'2024/1689"\s+in',
    ),
    # source_classes: the universal source-class map (taxonomy of source KINDS,
    # effect ceilings, relation vocabulary, SC-2/SC-3 invariants) is consumed from
    # loomground_legal.source_classes; none of it may regrow here. (Patterns match
    # real definitions only, never the shim's `X = _sc.X` rebinds.)
    "source_classes.py": (
        r"class\s+SourceClass\b", r"class\s+Effect\b", r"class\s+Relation\b",
        r"^_MAX_EFFECT\s*[:=]", r"^_SELF_EXECUTING\s*[:=]",
        r"def\s+max_effect\s*\(", r"def\s+check_source\s*\(",
    ),
    # legal_systems: the jurisdiction-family packs (DE/EU/UK/US) + applicable-law
    # resolver is consumed from loomground_legal.legal_systems; the registry, the
    # equivalence clusters, and the resolver functions must not regrow here.
    "legal_systems.py": (
        r"class\s+LegalSystem\b", r"class\s+ApplicableLaw\b", r"class\s+SourceEntry\b",
        r"^_REGISTRY\s*[:=]", r"^_DE_CLUSTERS\s*[:=]", r"^_EN_CLUSTERS\s*[:=]",
        r"def\s+applicable_law\s*\(", r"def\s+applicable_systems\s*\(",
    ),
}

# quarantined legal originals kept for verification before deletion (the MOVE
# files whole; the SPLIT files as their migrated model/data only).
LEGAL_QUARANTINE = (
    "legal_world.py", "validate.py", "world_corpus_loader.py",
    "world_relations.py", "regulatory_population.py", "instance.py",
)


def test_legal_twins_consume_adapters_legal() -> None:
    """Each legal-stack twin consumes the plane through ``adapters.legal`` and
    re-grows none of the symbols that moved to the package."""
    seam = re.compile(
        r"from\s+\.+adapters\.legal\s+import|from\s+workspaces\.adapters\.legal\s+import")
    not_shims: list[str] = []
    regrown: list[str] = []
    for name, forbidden in LEGAL_TWINS.items():
        text = (WORKSPACES_ROOT / name).read_text(encoding="utf-8")
        if not seam.search(text):
            not_shims.append(name)
        for pat in forbidden:
            if re.search(pat, text, re.MULTILINE):
                regrown.append(f"{name}: {pat}")
    assert not not_shims, f"legal twins not consuming adapters.legal: {not_shims}"
    assert not regrown, f"legal twins re-grew moved behavior locally: {regrown}"


def test_adapters_legal_is_the_only_legal_import_site() -> None:
    """``loomground_legal`` is imported in exactly one place — the seam."""
    importers: list[str] = []
    for path in _all_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name == "loomground_legal" or a.name.startswith("loomground_legal.")
                       for a in node.names):
                    importers.append(str(path.relative_to(WORKSPACES_ROOT)))
            elif isinstance(node, ast.ImportFrom) and not node.level:
                if node.module and (node.module == "loomground_legal"
                                    or node.module.startswith("loomground_legal.")):
                    importers.append(str(path.relative_to(WORKSPACES_ROOT)))
    assert set(importers) == {"adapters/legal.py"}, (
        f"loomground_legal must be imported only in adapters/legal.py; got {sorted(set(importers))}")


# The required-artifact catalogue is CONSUMED from loomground-ingest, not re-grown.
# (A single-import-site rule is NOT used here — loomground_ingest is legitimately
# imported by workspaces/ingest/* and adapters/ingest/governance.py — so this is the
# twin-consume + no-regrow shape, like LEGAL_TWINS.)
INGEST_TWINS: dict[str, tuple[str, ...]] = {
    # instrument_obligation_extractor: consumes ingest.extract_required_artifacts /
    # RequiredArtifact — the catalogue, ArtifactSpec schema, obligation-cue regex and
    # the scan must not regrow here. Only the ND-dispatcher wrapping stays local.
    "instrument_obligation_extractor.py": (
        r"^_ARTIFACTS\s*[:=]", r"^_OBLIGATION_CUE\s*[:=]", r"^_OBLIGATION_WINDOW\s*[:=]",
        r"class\s+ArtifactSpec\b", r"class\s+RequiredArtifact\b",
        r"def\s+extract_required_artifacts\s*\(",
    ),
}


def test_ingest_twins_consume_adapters_ingest() -> None:
    """The required-artifact catalogue is consumed from the ingest plane through
    ``adapters.ingest``; its catalogue, schema, obligation-cue regex and scan must
    not regrow in the ND-dispatcher module."""
    seam = re.compile(
        r"from\s+\.+adapters\.ingest\b|from\s+workspaces\.adapters\.ingest\b")
    not_shims: list[str] = []
    regrown: list[str] = []
    for name, forbidden in INGEST_TWINS.items():
        text = (WORKSPACES_ROOT / name).read_text(encoding="utf-8")
        if not seam.search(text):
            not_shims.append(name)
        for pat in forbidden:
            if re.search(pat, text, re.MULTILINE):
                regrown.append(f"{name}: {pat}")
    assert not not_shims, f"ingest twins not consuming adapters.ingest: {not_shims}"
    assert not regrown, f"ingest twins re-grew the catalogue locally: {regrown}"


def test_legal_quarantine_originals_present() -> None:
    """The retired legal originals are kept in _quarantine (for verification
    before deletion) and remain inert (no live import — fenced above)."""
    q = WORKSPACES_ROOT / "_quarantine"
    present = sorted(p.name for p in q.glob("*.py") if p.name != "__init__.py")
    assert set(LEGAL_QUARANTINE) <= set(present), (
        f"quarantine is missing some retired legal originals: "
        f"{set(LEGAL_QUARANTINE) - set(present)}")


# ════════════════════════════════════════════════════════════════════════════
# (d) No shadow parallels of consumed surface.
#
# The checks above key off DECLARED upstream imports, so a module that re-grows
# an engine while importing NOTHING upstream is invisible to them — which is
# exactly how ``problem_kg``'s copy of solver.case's Ground/Fact/CaseRecord/
# project_pairs hid for so long. This check closes that hole by looking at
# DEFINITIONS, not imports: a live workspaces module (outside the adapters/
# seam) must not DEFINE a symbol that duplicates a consumed package's
# distinctive public surface. Genuine name-collisions are allow-listed with a
# reason.
# ════════════════════════════════════════════════════════════════════════════

# Distinctive symbols owned by a consumed loomground package. Generic names
# (check, plan, gate, derive, holds, neg, audit) are deliberately excluded —
# only names specific enough that a local definition means a parallel, not a
# coincidence.
CONSUMED_SURFACE_SYMBOLS = {
    # loomground-solver — reasoning / case / algebra
    "CaseRecord", "Ground", "project_pairs", "_norm_spans_for",
    "Subsumption", "subsume", "Scenario", "DecisionSpace", "decision_space",
    "grounded_labels", "RelationAlgebra", "Edge", "Inference", "InferenceList",
    "Neighborhood", "neighborhood", "compose_paths", "extract_edges",
    "fingerprint", "derive_solution", "ContractReport", "Norm", "Rule",
    # shared premise / verdict vocab
    "Fact", "Finding",
}

# Legitimate name-collisions: a DIFFERENT concept in a different domain, not a
# re-grown engine. Keyed by (module relpath, symbol), each with its reason.
SHADOW_ALLOWLIST = {
    ("lock/core.py", "Finding"):
        "egress-lock scan finding — unrelated to solver's contract Finding",
    ("fact_source.py", "Fact"):
        "a measurement observation (subject_ref/value/unit) — not solver's premise Fact",
    ("use_case_nd.py", "subsume"):
        "AI-Act use-case -> duty join — a domain op, not solver's Tatbestand subsumption",
}


def test_no_shadow_parallel_of_consumed_surface() -> None:
    """A live workspaces module must not DEFINE (top-level) a symbol that
    duplicates a consumed package's distinctive surface, outside the adapters/
    seam. Catches parallels that import nothing upstream. Legitimate
    name-collisions are allow-listed above with a reason."""
    offenders: list[str] = []
    for path in _live_py_files():
        rel = path.relative_to(WORKSPACES_ROOT).as_posix()
        if rel.startswith("adapters/"):
            continue  # the seam is where re-exports legitimately live
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:  # top-level definitions only
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in CONSUMED_SURFACE_SYMBOLS \
                        and (rel, node.name) not in SHADOW_ALLOWLIST:
                    offenders.append(f"{rel}: defines {node.name!r}")
    assert not offenders, (
        "shadow parallels of consumed-package surface — consume via the "
        "adapters/ seam instead, or allow-list a genuine name-collision:\n  "
        + "\n  ".join(sorted(offenders)))


def test_shadow_allowlist_is_not_stale() -> None:
    """Every allow-listed collision must still exist (a top-level def of that
    name in that file) — else drop it, so the allowlist can't rot into a
    silent escape hatch."""
    stale: list[str] = []
    for (rel, name) in SHADOW_ALLOWLIST:
        path = WORKSPACES_ROOT / rel
        if not path.exists():
            stale.append(f"{rel} (file gone)")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found = any(
            isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == name
            for n in tree.body)
        if not found:
            stale.append(f"{rel}:{name} (no longer defined)")
    assert not stale, f"stale SHADOW_ALLOWLIST entries — remove them: {stale}"


# ════════════════════════════════════════════════════════════════════════════
# (e) The matcher consumes the Solver subsumption engine, never re-grows it.
#
# matcher.py answers the genus/species question — does THIS subject trigger THIS
# obligation — by structural is-a reachability. That reachability belongs to
# Solver (``cross_subsumption.subsume_across``, reached through the
# ``adapters.solver.subsumption`` seam), NOT to a local transitive taxonomy walk.
# The historic parallel was matcher computing the is-a closure itself via
# ``DomainVocabulary.ancestors()``. This fence keeps it retired: matcher must
# consume ``subsume_across`` from the seam, and must not walk ``vocab.ancestors``
# for matching. (``use_case_nd.subsume`` is a *different* concern — a domain
# use-case→duty join, allow-listed above as a non-parallel; it is not fenced
# here because it consumes no reachability engine to begin with.)
# ════════════════════════════════════════════════════════════════════════════

def test_matcher_consumes_solver_subsumption() -> None:
    """matcher.py routes is-a matching through Solver's ``subsume_across`` and
    does not re-grow the transitive taxonomy walk (``vocab.ancestors``)."""
    src = (WORKSPACES_ROOT / "matcher.py").read_text(encoding="utf-8")
    assert ("from .adapters.solver.subsumption import" in src
            and "subsume_across" in src), (
        "matcher.py must consume Solver's subsumption engine (subsume_across) "
        "through the adapters.solver.subsumption seam")
    assert ".ancestors(" not in src, (
        "matcher.py re-grows the transitive is-a walk (vocab.ancestors) — that "
        "reachability is Solver's; route it through subsume_across instead")
