# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Guard the wheel's data payload at the packaging-config level.

The data files (jurisdiction packs, eval corpus, py.typed) are shipped via
``[tool.setuptools.package-data]``. setuptools resolves each glob relative to
the OWNING package's directory, so the table MUST be keyed to ``rvnd`` — the
package that contains ``data/``. Keying it to the deprecated ``workspaces``
import shim (which has no ``data/`` subtree) makes every glob match nothing, so
a built wheel silently ships ZERO packs — and editable installs mask it because
they read the source tree in place. That regression shipped once already (the
``workspaces``->``rvnd`` rename); this test fails the build config the moment it
recurs, without needing a wheel build (the gates venv has no offline build
backend, and a build-isolation wheel would need network the egress gate blocks).

It is a pure-config + file-existence check: deterministic, offline, stdlib only.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
RVND_PKG = REPO_ROOT / "server" / "src" / "rvnd"


def _cfg() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_package_data_is_keyed_to_rvnd_not_the_workspaces_shim() -> None:
    pkg_data = _cfg()["tool"]["setuptools"]["package-data"]
    assert "rvnd" in pkg_data, (
        "package-data must be keyed to `rvnd` (the package that owns data/). "
        "Keying it to the `workspaces` shim ships a wheel with no jurisdiction "
        "packs — the workspaces->rvnd rename regression."
    )


def test_every_rvnd_data_glob_matches_real_files() -> None:
    """Each declared glob must resolve, under the rvnd package dir, to >=1 file.

    A glob that matches nothing is the exact symptom of a mis-keyed / stale
    data declaration, so an empty match is a failure, not a no-op.
    """
    globs = _cfg()["tool"]["setuptools"]["package-data"]["rvnd"]
    empty: list[str] = []
    for pattern in globs:
        if pattern == "py.typed":
            assert (RVND_PKG / "py.typed").is_file(), "rvnd/py.typed is declared but missing"
            continue
        if not list(RVND_PKG.glob(pattern)):
            empty.append(pattern)
    assert not empty, f"package-data globs under `rvnd` match no files: {empty}"


def test_jurisdiction_packs_are_actually_declared() -> None:
    """A concrete anchor: real pack JSONs on disk must be covered by the globs.

    Guards against someone narrowing the globs and silently dropping packs.
    """
    on_disk = {p.name for p in (RVND_PKG / "data" / "packs").glob("*.json")}
    assert on_disk, "no jurisdiction packs found on disk — test fixture drift"
    globs = _cfg()["tool"]["setuptools"]["package-data"]["rvnd"]
    covered = {p.name for pat in globs for p in RVND_PKG.glob(pat)}
    missing = on_disk - covered
    assert not missing, f"jurisdiction packs on disk not covered by package-data: {sorted(missing)}"


def test_dead_quarantine_tree_is_excluded_from_the_distribution() -> None:
    find = _cfg()["tool"]["setuptools"]["packages"]["find"]
    exclude = find.get("exclude", [])
    assert any("rvnd._quarantine" in e for e in exclude), (
        "packages.find must exclude `rvnd._quarantine*` so the dead, test-fenced "
        "quarantine tree does not ship in the wheel."
    )
