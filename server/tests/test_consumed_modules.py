# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Standing gate: the norm-runtime plane is CONSUMED, not re-grown.

RVND's eight norm-runtime twins (rule extraction, obligation state, subsumption,
the span-norm registry, the obligation scheduler) are retired in favour of
``loomground-norm``. Each live twin is now a thin re-export shim over
``adapters.norm``; the original implementations are quarantined
(dead-on-arrival, never imported). This gate makes that permanent:

  (a) no live module in the package imports from ``_quarantine/``;
  (b) each norm-runtime twin is a shim over ``adapters.norm`` — it imports from
      there and does NOT re-grow the retired behavior locally;
  (c) every FIRST-PARTY distribution RVND consumes is either declared in
      ``pyproject.toml`` (main dependencies or an extra) or on the explicit
      ``_PIN_PENDING`` allowlist, and every first-party distribution declared
      there is actually consumed — so a NEW orphan fails, while today is green.

"First-party" is not a name pattern. This gate used to read it off the
``loomground-`` prefix, which made three real first-party pins —
``enforcement-posture``, ``effect-reconciliation``, ``oversight-certificate`` —
invisible to it: an orphan among them would have passed silently, which is the
exact failure class the gate exists to prevent. The set now comes from
``VCS_FIRST_PARTY`` in ``scripts/release_dependency_artifacts.py``, the same
list the release tooling uses to decide which artifacts must carry an exact git
commit instead of a PyPI digest. One source, so the two cannot drift.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

# The only path assumption in this file: it lives at <repo>/server/tests/.
# Everything below is derived from pyproject.toml, so the imminent rename of the
# import package (server/src/workspaces -> server/src/rvnd) needs no edit here.
REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _package_root() -> Path:
    """The import package's source directory, read off the build config."""
    where = _PYPROJECT.get("tool", {}).get("setuptools", {}) \
        .get("package-dir", {}).get("", "server/src")
    src = REPO_ROOT / where
    found = sorted(d for d in src.iterdir()
                   if d.is_dir() and (d / "__init__.py").is_file())
    assert found, f"no import package found under {src}"
    if len(found) == 1:
        return found[0]
    # More than one: the rename ships a compat alias alongside the real package
    # (server/src/workspaces re-points sys.modules at rvnd). Ask the build config
    # which one is canonical rather than guessing by name -- the same source the
    # wheel uses, so this cannot disagree with what actually gets shipped.
    attr = _PYPROJECT.get("tool", {}).get("setuptools", {}) \
        .get("dynamic", {}).get("version", {}).get("attr", "")
    canonical = attr.split(".")[0] if attr else ""
    match = [d for d in found if d.name == canonical]
    assert len(match) == 1, (
        f"{len(found)} import packages under {src} ({[d.name for d in found]}) and "
        f"the version attr names {canonical!r}, which matches {len(match)} of them - "
        f"point tool.setuptools.dynamic.version.attr at the canonical package")
    return match[0]


PACKAGE_ROOT = _package_root()
# The package's import name (``workspaces`` today, ``rvnd`` after the rename).
# The seam regexes below are built from it rather than spelling it out.
PKG = PACKAGE_ROOT.name


def _load_vcs_first_party() -> frozenset[str]:
    """The authoritative first-party set, taken from the release tooling.

    ``scripts/release_dependency_artifacts.py`` already decides, per package,
    whether its release artifact must carry an exact git commit (first-party) or
    a PyPI sha256 (third-party). That is the same question this gate asks, so it
    reads the same constant instead of keeping a second list that would drift.
    The module is imported, not re-parsed, so a rename of the constant fails
    loudly here rather than quietly shrinking the gate's coverage to nothing.
    """
    script = REPO_ROOT / "scripts" / "release_dependency_artifacts.py"
    spec = importlib.util.spec_from_file_location("_rvnd_release_artifacts", script)
    assert spec is not None and spec.loader is not None, f"cannot load {script}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = getattr(module, "VCS_FIRST_PARTY", None)
    assert isinstance(names, (set, frozenset)) and names, (
        f"{script.name} must define a non-empty VCS_FIRST_PARTY set — this gate "
        f"derives the first-party set from it; got {names!r}")
    return frozenset(names)


FIRST_PARTY = _load_vcs_first_party()

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

# First-party distributions consumed-but-undeclared until release (git deps not
# yet pinned in pyproject). A new orphan NOT on this list (and not declared)
# fails check (c). Empty now: loomground-norm graduated to a declared git pin.
_PIN_PENDING: tuple[str, ...] = ()

# The opposite category: dists RVND pins to PROVIDE the installable git artifact
# for a plane that imports them transitively without carrying an installable pin
# of its own. loomground-ingest imports loomground_factual at module scope
# (ingest/deontic.py) and loomground_epistemic lazily (ingest/compose.py), while
# its metadata lists both only behind its ``compose`` extra, which RVND does not
# request — and neither package is on any index, so pip could not resolve them
# from an abstract requirement anyway. RVND must carry the direct-URL pin. RVND
# does NOT consume them directly, so they are exempt from the
# declared-must-be-consumed direction (check below). Proper long-term home:
# ingest carrying its own direct-URL pins; until then RVND provides them.
# ``test_transitive_provider_pins_are_still_needed`` asserts that reason still
# holds, so the pins get dropped when it stops being true.
_TRANSITIVE_PROVIDER = ("loomground-factual", "loomground-epistemic")

# ── import name != distribution name ────────────────────────────────────────
# Handled by two explicit rules rather than one hand-maintained table:
#
#   1. DEFAULT — a distribution imports under its own name with '-' -> '_'.
#      Applied to every member of FIRST_PARTY, so a newly pinned plane is
#      covered the moment it is added to VCS_FIRST_PARTY, with no edit here:
#      loomground-solver -> loomground_solver, enforcement-posture ->
#      enforcement_posture, effect-reconciliation -> effect_reconciliation,
#      oversight-certificate -> oversight_certificate.
#
#   2. EXCEPTIONS — two planes publish an import package that drops the
#      ``loomground-`` prefix. These are ADDITIONAL spellings, not replacements:
#      both ``loomground_versum`` and ``versum`` map to loomground-versum.
#
# ``test_import_name_exceptions_are_not_stale`` keeps rule 2 honest.
_IMPORT_NAME_EXCEPTIONS = {
    "versum": "loomground-versum",
    "deontic": "loomground-deontic",
    "loomground_ingest": "loomground-ingest",
    "loomground_legal": "loomground-legal",
    "loomground_norm": "loomground-norm",
    "loomground_workspace": "loomground-workspace",
}

_IMPORT_TO_DIST: dict[str, str] = {d.replace("-", "_"): d for d in FIRST_PARTY}
_IMPORT_TO_DIST.update(_IMPORT_NAME_EXCEPTIONS)


def _live_py_files() -> list[Path]:
    return [p for p in PACKAGE_ROOT.rglob("*.py")
            if "__pycache__" not in p.parts and "_quarantine" not in p.parts]


def _all_py_files() -> list[Path]:
    return [p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


# ── (a) ─────────────────────────────────────────────────────────────────────

def test_no_live_module_imports_from_quarantine() -> None:
    """The quarantined originals are dead code — no live module may import them."""
    pattern = re.compile(r"^\s*(?:from|import)\s+[.\w]*_quarantine\b", re.MULTILINE)
    offenders: list[str] = []
    for path in _live_py_files():
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(PACKAGE_ROOT)))
    assert not offenders, f"live module imports from _quarantine/: {offenders}"


def test_quarantine_dir_is_present_but_inert() -> None:
    """The quarantine package exists (originals kept for verification) and is
    marked as not-imported-by-live-code."""
    q = PACKAGE_ROOT / "_quarantine"
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
    seam = re.compile(
        rf"from\s+\.adapters\.norm\s+import|from\s+{PKG}\.adapters\.norm\s+import")
    not_shims: list[str] = []
    regrown: list[str] = []
    for name in NORM_TWINS:
        text = (PACKAGE_ROOT / name).read_text(encoding="utf-8")
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
                    importers.append(str(path.relative_to(PACKAGE_ROOT)))
            elif isinstance(node, ast.ImportFrom) and not node.level:
                if node.module and (node.module == "loomground_norm"
                                    or node.module.startswith("loomground_norm.")):
                    importers.append(str(path.relative_to(PACKAGE_ROOT)))
    assert set(importers) == {"adapters/norm.py"}, (
        f"loomground_norm must be imported only in adapters/norm.py; got {sorted(set(importers))}")


def _importers_of(package: str, roots: list[Path]) -> set[str]:
    """Every file under ``roots`` that imports ``package`` (or a submodule),
    as a repo-relative path string."""
    found: set[str] = set()
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:  # pragma: no cover - not our file to fix
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(a.name == package or a.name.startswith(package + ".")
                           for a in node.names):
                        found.add(str(path.relative_to(REPO_ROOT)))
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    if node.module and (node.module == package
                                        or node.module.startswith(package + ".")):
                        found.add(str(path.relative_to(REPO_ROOT)))
    return found


def test_adapters_workspace_is_the_only_workspace_plane_import_site() -> None:
    """``loomground_workspace`` is imported in exactly one place — the seam.

    This gate is load-bearing for the EGRESS LOCK, not just for tidiness.

    Upstream's ``list_known_workspaces`` returns the WHOLE registry unless the
    host injects a ``scope=`` filter; RVND's retired copy scoped it internally
    to the request principal, fail-closed. ``adapters/workspace.py`` restores
    that by defaulting the filter, so every caller is scoped without knowing it.
    A second importer anywhere would be able to call the unscoped upstream
    function directly and get the full registry back — and the caller that
    matters is ``app/serve.py``, which uses the list as the bridge's
    trusted-path allowlist immediately upstream of the proxy-proof and
    session-token checks that authorize egress. That failure is silent: the
    happy path still works, the list is merely bigger.

    So the scan covers ``app/`` as well as the import package — the
    adapter-boundary gate only walks the latter, and the bridge lives in the
    former.
    """
    importers = _importers_of(
        "loomground_workspace",
        [PACKAGE_ROOT, REPO_ROOT / "app"],
    )
    assert importers == {f"server/src/{PKG}/adapters/workspace.py"}, (
        "loomground_workspace must be imported only in "
        "adapters/workspace.py (it is where the per-principal read scope is "
        f"defaulted); got {sorted(importers)}")


def test_workspace_seam_defaults_the_principal_scope() -> None:
    """The seam's ``list_known_workspaces`` must pass a scope by default.

    A structural check to go with the behavioural one in
    ``server/tests/security/test_attack_folder_context_traversal.py``: if this
    wrapper ever degrades to a bare re-export of the upstream name, the
    behavioural test is the only thing left, and a re-export would still pass
    every happy-path test in the suite.
    """
    from workspaces.adapters import workspace as seam

    src = (PACKAGE_ROOT / "adapters" / "workspace.py").read_text(encoding="utf-8")
    assert "scope=_principal_scope if scope is None else scope" in src, (
        "adapters.workspace.list_known_workspaces must default the principal "
        "scope filter; a caller must not be able to get the unscoped registry "
        "by omitting an argument")
    # and it is genuinely RVND's wrapper, not upstream's function re-exported
    import loomground_workspace as _lw
    assert seam.list_known_workspaces is not _lw.list_known_workspaces
    assert seam.list_known_workspaces is not _lw.workspace_registry.list_known_workspaces


def test_workspace_concept_modules_are_shims_over_the_seam() -> None:
    """The three retired workspace-concept modules carry no definitions.

    ``folder_context.py``, ``registry.py`` and ``_storage_paths.py`` stay as
    module paths (~60 import sites address them) but must hold zero
    ``def``/``class`` of their own, so the retired copies cannot grow back and
    drift from the package.

    ``registry.py`` was ``workspace_registry.py`` until the engine stopped being
    named after the folders it governs — a forwarding shim carrying the name of
    a concept it no longer owns was the clearest case for renaming.
    """
    seam = re.compile(r"from\s+\.adapters\.workspace\s+import")
    offenders: list[str] = []
    for name in ("folder_context.py", "registry.py", "_storage_paths.py"):
        text = (PACKAGE_ROOT / name).read_text(encoding="utf-8")
        if not seam.search(text):
            offenders.append(f"{name}: does not consume adapters.workspace")
        tree = ast.parse(text, filename=name)
        local = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        if local:
            offenders.append(f"{name}: re-grew local definitions {local}")
    assert not offenders, offenders


# ── (c) ─────────────────────────────────────────────────────────────────────

def _declared_first_party_dists() -> dict[str, str]:
    """First-party dists declared in pyproject -> where they are declared.

    Reads BOTH ``project.dependencies`` and every ``project.optional-dependencies``
    extra: ``oversight-certificate`` is pinned only in the ``[oversight-cert]``
    extra, so a main-dependencies-only read would have called it undeclared.
    Membership is decided by :data:`FIRST_PARTY`, never by a name prefix.
    """
    project = _PYPROJECT.get("project", {})
    groups: list[tuple[str, list[str]]] = [("dependencies", list(project.get("dependencies", [])))]
    for extra, deps in project.get("optional-dependencies", {}).items():
        groups.append((f"extra:{extra}", list(deps)))
    out: dict[str, str] = {}
    for where, deps in groups:
        for dep in deps:
            # e.g. "loomground-solver @ git+https://..." -> "loomground-solver"
            name = re.split(r"[\s@<>=!~;\[]", dep.strip(), maxsplit=1)[0]
            if name in FIRST_PARTY:
                out.setdefault(name, where)
    return out


def _imported_top_level_names() -> set[str]:
    """Every top-level module name imported anywhere under the package root,
    including imports nested inside functions (``ast.walk``, not ``tree.body``) —
    the first-party planes are routinely imported lazily."""
    tops: set[str] = set()
    for path in _all_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module]
            for mod in names:
                tops.add(mod.split(".", 1)[0])
    return tops


def _consumed_first_party_dists() -> set[str]:
    consumed: set[str] = set()
    for top in _imported_top_level_names():
        if top in _IMPORT_TO_DIST:
            consumed.add(_IMPORT_TO_DIST[top])
        elif top.startswith("loomground_"):
            # A loomground_* import with no mapping — a plane pinned but not yet
            # added to VCS_FIRST_PARTY. Treat it as its dist name so a NEW orphan
            # surfaces here rather than passing silently. (No such heuristic is
            # possible for non-loomground first-party names, which is precisely
            # why VCS_FIRST_PARTY, not a prefix, is the source of truth.)
            consumed.add(top.replace("_", "-"))
    return consumed


def test_import_name_exceptions_are_not_stale() -> None:
    """Rule 2 of the import-name mapping must stay tied to reality: each
    bare-name exception must name a real first-party dist AND still be imported
    somewhere — else drop it, so the map cannot rot into dead entries that
    quietly mask what they claim to cover."""
    imported = _imported_top_level_names()
    bogus = sorted(f"{k} -> {v}" for k, v in _IMPORT_NAME_EXCEPTIONS.items()
                   if v not in FIRST_PARTY)
    assert not bogus, (
        f"_IMPORT_NAME_EXCEPTIONS maps to distributions that are not first-party "
        f"(not in VCS_FIRST_PARTY): {bogus}")
    unused = sorted(k for k in _IMPORT_NAME_EXCEPTIONS if k not in imported)
    assert not unused, (
        f"_IMPORT_NAME_EXCEPTIONS lists import names nobody imports — remove "
        f"them: {unused}")


def test_import_name_map_covers_every_first_party_dist() -> None:
    """Every first-party distribution has at least one known import name, so
    none of them can be consumed invisibly."""
    unmapped = sorted(FIRST_PARTY - set(_IMPORT_TO_DIST.values()))
    assert not unmapped, (
        f"first-party distributions with no import name — this gate cannot see "
        f"whether they are consumed: {unmapped}")


def test_every_git_pinned_dependency_is_first_party() -> None:
    """VCS_FIRST_PARTY cannot fall behind pyproject.

    A dependency pinned to a git URL is by construction one we build ourselves.
    If such a pin is missing from VCS_FIRST_PARTY then both this gate and the
    release tooling stop seeing it — which is how the ``loomground-*`` prefix
    rule hid enforcement-posture, effect-reconciliation and
    oversight-certificate for as long as it did. Adding a git pin without
    listing it fails here.
    """
    project = _PYPROJECT.get("project", {})
    deps = list(project.get("dependencies", []))
    for extra_deps in project.get("optional-dependencies", {}).values():
        deps.extend(extra_deps)
    unlisted: list[str] = []
    for dep in deps:
        if "@ git+" not in dep:
            continue
        name = re.split(r"[\s@<>=!~;\[]", dep.strip(), maxsplit=1)[0]
        if name not in FIRST_PARTY:
            unlisted.append(name)
    assert not unlisted, (
        f"git-pinned dependencies missing from VCS_FIRST_PARTY in "
        f"scripts/release_dependency_artifacts.py — add them there (one source, "
        f"used by both the release artifacts and this gate): {sorted(unlisted)}")


def test_every_consumed_first_party_dist_is_declared_or_pinned() -> None:
    declared = _declared_first_party_dists()
    allowed = set(declared) | set(_PIN_PENDING)
    consumed = _consumed_first_party_dists()
    orphans = sorted(consumed - allowed)
    assert not orphans, (
        f"first-party distributions consumed but neither declared in pyproject "
        f"nor on _PIN_PENDING: {orphans}\n"
        f"  declared: {sorted(declared)}\n"
        f"  pin-pending: {sorted(_PIN_PENDING)}\n"
        f"  consumed: {sorted(consumed)}")


def test_every_declared_first_party_dist_is_consumed() -> None:
    """The reverse of the orphan check above, and the direction it was missing: a
    first-party distribution DECLARED as a dependency must actually be imported
    somewhere under the package root.

    A declared-but-unconsumed dependency is the ``orphan = reimplemented`` signal
    the standing consume-vs-regrow gate exists to catch: RVND paid the dependency
    cost but grew its own runtime instead of consuming the module (the historical
    ``loomground_norm`` failure — declared/vendored yet imported nowhere while a
    parallel twin carried the work). Together with
    :func:`test_every_consumed_first_party_dist_is_declared_or_pinned` this makes
    the manifest<->usage relationship bidirectional: nothing consumed-but-undeclared,
    and nothing declared-but-unconsumed.

    Both directions now cover EVERY first-party pin, not just the ``loomground-*``
    ones — enforcement-posture, effect-reconciliation and oversight-certificate
    were invisible here while the check keyed on the name prefix.

    (_PIN_PENDING dists are consumed-but-undeclared by construction, so they never
    appear in ``declared`` and cannot trip this check.)"""
    declared = _declared_first_party_dists()
    consumed = _consumed_first_party_dists()
    # _TRANSITIVE_PROVIDER dists are pinned to provide a transitive plane artifact,
    # not consumed by RVND directly — they are declared-but-unconsumed by design.
    unconsumed = sorted(set(declared) - consumed - set(_TRANSITIVE_PROVIDER))
    assert not unconsumed, (
        f"first-party distributions declared in pyproject but imported NOWHERE "
        f"under the package root — declared-but-unconsumed means the capability was "
        f"reimplemented as a parallel structure instead of consumed. Consume the "
        f"module through its adapter seam, or drop the dependency: {unconsumed}\n"
        f"  declared: {dict(sorted(declared.items()))}\n"
        f"  consumed: {sorted(consumed)}")


def test_pin_pending_entries_are_actually_consumed() -> None:
    """Guard against _PIN_PENDING going stale: every pinned-pending dist must
    actually be consumed (else drop it), and must NOT also be declared."""
    declared = _declared_first_party_dists()
    consumed = _consumed_first_party_dists()
    for dist in _PIN_PENDING:
        assert dist in consumed, f"_PIN_PENDING lists {dist} but it is not consumed"
        assert dist not in declared, (
            f"{dist} is declared in pyproject — remove it from _PIN_PENDING")


# ── transitive-provider pins: the reason must still hold ────────────────────

def _ingest_source_dir() -> Path:
    """The INSTALLED loomground-ingest source tree.

    Deliberately no skip-if-missing: loomground-ingest is a hard RVND dependency,
    so an environment without it is a broken environment, not a reason to let this
    check quietly pass."""
    spec = importlib.util.find_spec("loomground_ingest")
    assert spec is not None and spec.submodule_search_locations, (
        "loomground_ingest is not importable — it is a declared RVND dependency; "
        "install the environment rather than skipping this check")
    return Path(list(spec.submodule_search_locations)[0])


def _ingest_requirements() -> list[str]:
    from importlib.metadata import distribution
    return list(distribution("loomground-ingest").metadata.get_all("Requires-Dist") or [])


def test_transitive_provider_pins_are_still_needed() -> None:
    """factual/epistemic are pinned for a REASON — assert the reason still holds.

    RVND imports neither. They are pinned so that pip has an installable artifact
    for packages the ingest plane imports but does not itself carry a pin for.
    Two conditions make that necessary, and this test asserts both:

      (N1) the installed loomground-ingest source still imports the package —
           if it stops, nothing in the closure needs it and the pin is dead
           weight;
      (N2) ingest still declares no direct-URL (``@ git+``) requirement for it —
           if ingest starts carrying its own installable pin, pip resolves the
           package through ingest and RVND's provider pin is redundant.

    Either flip fails here, so the pins get dropped rather than outliving their
    reason. (loomground-epistemic additionally requires loomground-factual, which
    only strengthens N1 for factual.)
    """
    src = _ingest_source_dir()
    sources = [f for f in src.rglob("*.py") if "__pycache__" not in f.parts]
    assert sources, f"no python sources under the installed ingest at {src}"
    blob = "\n".join(f.read_text(encoding="utf-8") for f in sources)
    requires = _ingest_requirements()

    unneeded: list[str] = []
    superseded: list[str] = []
    for dist in _TRANSITIVE_PROVIDER:
        import_name = dist.replace("-", "_")
        if not re.search(rf"^\s*(?:from|import)\s+{re.escape(import_name)}\b",
                         blob, re.MULTILINE):
            unneeded.append(dist)
        for req in requires:
            name = re.split(r"[\s@<>=!~;\[]", req.strip(), maxsplit=1)[0]
            if name == dist and "@ git+" in req and "extra ==" not in req:
                superseded.append(f"{dist} ({req.strip()})")

    assert not unneeded, (
        f"transitive-provider pins whose reason no longer holds: the installed "
        f"loomground-ingest no longer imports them, so nothing in the closure "
        f"needs them. Drop the pin from pyproject.toml, _TRANSITIVE_PROVIDER and "
        f"VCS_FIRST_PARTY: {sorted(unneeded)}")
    assert not superseded, (
        f"transitive-provider pins now superseded: loomground-ingest carries its "
        f"own installable direct-URL requirement for these, so RVND's provider "
        f"pin is redundant. Drop it: {sorted(superseded)}")


def test_transitive_provider_entries_are_not_stale() -> None:
    """Every _TRANSITIVE_PROVIDER entry must be a first-party dist that is
    declared in pyproject and NOT consumed by RVND directly — otherwise the
    exemption is hiding a dependency that belongs in the normal
    declared<->consumed direction."""
    declared = _declared_first_party_dists()
    consumed = _consumed_first_party_dists()
    for dist in _TRANSITIVE_PROVIDER:
        assert dist in FIRST_PARTY, (
            f"_TRANSITIVE_PROVIDER lists {dist}, which is not in VCS_FIRST_PARTY")
        assert dist in declared, (
            f"_TRANSITIVE_PROVIDER lists {dist} but pyproject does not declare it "
            f"— the whole point of the entry is that RVND provides the artifact")
        assert dist not in consumed, (
            f"{dist} IS consumed by RVND directly — remove it from "
            f"_TRANSITIVE_PROVIDER so the normal declared<->consumed checks apply")


# ════════════════════════════════════════════════════════════════════════════
# The legal-world stack is CONSUMED, not re-grown.
#
# RVND's world-map stack (the entity model + graph container + reach, the seed
# corpus, the md-table loader, the curated relational enrichment, the instrument
# catalogue, corpus validation, the contract model) is retired in favour of
# ``loomground-legal``, consumed through ``adapters.legal``. Unlike
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

# twin file (relative to PACKAGE_ROOT) -> regexes for MOVED symbols that must
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
        rf"from\s+\.+adapters\.legal\s+import|from\s+{PKG}\.adapters\.legal\s+import")
    not_shims: list[str] = []
    regrown: list[str] = []
    for name, forbidden in LEGAL_TWINS.items():
        text = (PACKAGE_ROOT / name).read_text(encoding="utf-8")
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
                    importers.append(str(path.relative_to(PACKAGE_ROOT)))
            elif isinstance(node, ast.ImportFrom) and not node.level:
                if node.module and (node.module == "loomground_legal"
                                    or node.module.startswith("loomground_legal.")):
                    importers.append(str(path.relative_to(PACKAGE_ROOT)))
    assert set(importers) == {"adapters/legal.py"}, (
        f"loomground_legal must be imported only in adapters/legal.py; got {sorted(set(importers))}")


# The required-artifact catalogue is CONSUMED from loomground-ingest, not re-grown.
# (A single-import-site rule is NOT used here — loomground_ingest is legitimately
# imported by ingest/* and adapters/ingest/governance.py — so this is the
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
        rf"from\s+\.+adapters\.ingest\b|from\s+{PKG}\.adapters\.ingest\b")
    not_shims: list[str] = []
    regrown: list[str] = []
    for name, forbidden in INGEST_TWINS.items():
        text = (PACKAGE_ROOT / name).read_text(encoding="utf-8")
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
    q = PACKAGE_ROOT / "_quarantine"
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
# DEFINITIONS, not imports: a live package module (outside the adapters/
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
    """A live package module must not DEFINE (top-level) a symbol that
    duplicates a consumed package's distinctive surface, outside the adapters/
    seam. Catches parallels that import nothing upstream. Legitimate
    name-collisions are allow-listed above with a reason."""
    offenders: list[str] = []
    for path in _live_py_files():
        rel = path.relative_to(PACKAGE_ROOT).as_posix()
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
        path = PACKAGE_ROOT / rel
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
    src = (PACKAGE_ROOT / "matcher.py").read_text(encoding="utf-8")
    assert ("from .adapters.solver.subsumption import" in src
            and "subsume_across" in src), (
        "matcher.py must consume Solver's subsumption engine (subsume_across) "
        "through the adapters.solver.subsumption seam")
    assert ".ancestors(" not in src, (
        "matcher.py re-grows the transitive is-a walk (vocab.ancestors) — that "
        "reachability is Solver's; route it through subsume_across instead")
