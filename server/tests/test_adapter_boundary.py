"""RVND's workspaces package reaches upstream Loomground packages through one seam.

AST-scans every ``server/src/rvnd/**/*.py`` module and fails if it imports
``loomground_solver``, ``loomground_versum``, ``loomground_governance``,
``loomground_deontic``, or the bare ``versum`` package outside an explicit
allowlist. Each entry on the allowlist is a declared seam, not an exception
being hidden:

* ``adapters/**`` — the adapter package itself. Its submodules (e.g.
  ``adapters/solver/dimensions.py``, ``adapters/versum/knowledge.py``) are the
  only place RVND is allowed to import an upstream Loomground package
  directly; every other module reaches upstream names through here (or
  through a top-level compatibility facade, e.g. ``rvnd.dimensions``,
  that itself imports from here).
* ``ingest/**`` — RVND's contributed ingester, which legitimately consumes
  ``loomground_ingest`` directly; it is upstream-facing by design, not a
  workspaces-internal consumer of solver/versum/governance/deontic.
* ``loomground_assets.py`` — the compatibility facade for
  ``loomground_governance`` artifacts (vocabulary, conformance data); kept as
  a standalone facade rather than relocated under ``adapters/`` because it
  predates this seam and every one of its call sites already treats it as
  the governance facade.

Restricted packages are matched by their dotted prefix so submodule imports
(``loomground_solver.dimensions``, ``versum.something``) are caught too.
"""

from __future__ import annotations

import ast
from pathlib import Path

WORKSPACES_ROOT = Path(__file__).resolve().parents[1] / "src" / "rvnd"

RESTRICTED_PACKAGES = (
    "loomground_solver",
    "loomground_versum",
    "loomground_governance",
    "loomground_deontic",
    "loomground_norm",
    "loomground_vertical",
    "loomground_workspace",
    "versum",
)

ALLOWLISTED_PATHS = (
    "adapters",  # adapters/** — the seam itself
    "ingest",  # ingest/** — RVND's sanctioned loomground_ingest consumer
)

ALLOWLISTED_FILES = (
    "loomground_assets.py",  # the governance compatibility facade
)


def _is_restricted(module: str) -> bool:
    return any(
        module == pkg or module.startswith(pkg + ".")
        for pkg in RESTRICTED_PACKAGES
    )


def _is_allowlisted(relative_path: Path) -> bool:
    if relative_path.name in ALLOWLISTED_FILES:
        return True
    return relative_path.parts[0] in ALLOWLISTED_PATHS


def _restricted_imports(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_restricted(alias.name):
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: cannot name a top-level package
                continue
            if node.module and _is_restricted(node.module):
                found.append(node.module)
    return found


def _all_modules() -> list[Path]:
    return sorted(WORKSPACES_ROOT.rglob("*.py"))


def test_upstream_imports_are_confined_to_the_declared_seam():
    violations: dict[str, list[str]] = {}
    for path in _all_modules():
        relative = path.relative_to(WORKSPACES_ROOT)
        if _is_allowlisted(relative):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _restricted_imports(tree)
        if hits:
            violations[str(relative)] = hits

    assert not violations, (
        "workspaces modules outside the adapters/ingest seam import an "
        "upstream Loomground package directly:\n"
        + "\n".join(f"  {mod}: {hits}" for mod, hits in sorted(violations.items()))
    )


def test_allowlisted_paths_actually_exist():
    # Guards against the allowlist silently going stale (e.g. a rename) and
    # no longer excluding anything, which would make the boundary test above
    # pass vacuously.
    for rel in ALLOWLISTED_PATHS:
        assert (WORKSPACES_ROOT / rel).is_dir(), f"expected directory: {rel}"
    for rel in ALLOWLISTED_FILES:
        assert (WORKSPACES_ROOT / rel).is_file(), f"expected file: {rel}"
