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
# fails check (c).
_PIN_PENDING = ("loomground-legal", "loomground-norm")

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


def test_legal_quarantine_originals_present() -> None:
    """The retired legal originals are kept in _quarantine (for verification
    before deletion) and remain inert (no live import — fenced above)."""
    q = WORKSPACES_ROOT / "_quarantine"
    present = sorted(p.name for p in q.glob("*.py") if p.name != "__init__.py")
    assert set(LEGAL_QUARANTINE) <= set(present), (
        f"quarantine is missing some retired legal originals: "
        f"{set(LEGAL_QUARANTINE) - set(present)}")
