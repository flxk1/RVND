# SPDX-License-Identifier: AGPL-3.0-only
"""Which adapter fronts which plane — asserted, and printed.

`test_adapter_boundary` proves CONFINEMENT: no module outside the seam imports a
plane. It walks the whole AST, so lazy imports inside functions are caught too.
What no gate did was state the MAPPING — plane -> the adapter that fronts it.

That absence has a cost. Reconstructing the map by grep gets it wrong, because
two planes publish an import package that drops the `loomground-` prefix:
`loomground-deontic` imports as `deontic`. A grep for `loomground_*` reports the
deontic adapter as fronting nothing, and reads a correct seam as a hole.

So the map is derived here from the same two sources the other gates use —
`VCS_FIRST_PARTY` for what counts as first-party, and the import-name rules from
`test_consumed_modules` — and printed, so it can be read rather than inferred.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from test_consumed_modules import (  # the ONE definition of these, not a copy
    _IMPORT_TO_DIST, _TRANSITIVE_PROVIDER, FIRST_PARTY, PACKAGE_ROOT)

ADAPTERS = PACKAGE_ROOT / "adapters"


def _planes_imported_by(path: Path) -> set[str]:
    """Distributions this file imports, lazy imports included."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                d = _IMPORT_TO_DIST.get(a.name.split(".")[0])
                if d:
                    found.add(d)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            d = _IMPORT_TO_DIST.get(node.module.split(".")[0])
            if d:
                found.add(d)
    return found


def _seam_unit(path: Path) -> str:
    """The adapter UNIT fronting a plane: a module, or a seam sub-package.

    A large plane gets a package rather than one file — adapters/solver/,
    adapters/ingest/, adapters/versum/ each hold many modules and are ONE seam.
    Keying on the file would read a package-shaped seam as a dozen rival ones.
    """
    rel = path.relative_to(ADAPTERS)
    return rel.parts[0] if len(rel.parts) > 1 else rel.name


def _seam_map() -> dict[str, list[str]]:
    m: dict[str, set[str]] = defaultdict(set)
    for p in sorted(ADAPTERS.rglob("*.py")):
        if p.name == "__init__.py":
            continue
        for dist in _planes_imported_by(p):
            m[dist].add(_seam_unit(p))
    return {d: sorted(u) for d, u in m.items()}


def test_the_map_is_not_empty():
    """A mapping gate that maps nothing would pass forever."""
    assert _seam_map(), "no adapter imports any first-party plane — impossible"


#: The one plane that is a SUBSTRATE rather than a domain: the domain planes are
#: built on the solver, so an adapter naming it alongside its own plane is
#: composition. Named explicitly, with its own seam asserted below, rather than
#: skipped — a blanket exemption would also hide a genuine second solver seam.
SUBSTRATE = "loomground-solver"


def test_each_fronted_plane_has_exactly_one_adapter():
    """Two adapters for one plane means two places to change, and a reader with
    no way to tell which is authoritative."""
    many = {d: a for d, a in _seam_map().items()
            if len(a) > 1 and d != SUBSTRATE}
    assert not many, f"more than one adapter fronts these planes: {many}"


def test_the_substrate_has_its_own_seam_and_the_others_only_compose_on_it():
    """The solver is exempt from one-adapter-only, so assert what replaces it:
    it still HAS a seam of its own, and every other unit naming it also fronts a
    plane of its own — i.e. is composing, not quietly acting as a second seam."""
    m = _seam_map()
    units = set(m.get(SUBSTRATE, []))
    assert "solver" in units, (
        "the solver substrate lost its own adapter package; without it, "
        "'composition' cannot be told apart from a bypass")
    for unit in units - {"solver"}:
        own = {d for d, us in m.items() if unit in us and d != SUBSTRATE}
        assert own, (
            f"adapters/{unit} imports the solver but fronts no plane of its "
            f"own — that is a second solver seam, not composition")


def test_every_adapter_that_imports_a_plane_fronts_exactly_one():
    """An adapter pulling in two planes is a seam for neither."""
    by_unit = defaultdict(set)
    for p in sorted(ADAPTERS.rglob("*.py")):
        if p.name == "__init__.py":
            continue
        for dist in _planes_imported_by(p):
            by_unit[_seam_unit(p)].add(dist)
    multi = {}
    for unit, planes in by_unit.items():
        # solver is the substrate the domain planes compose ON, so a domain
        # adapter naming it too is composition, not a second seam.
        rest = planes - {"loomground-solver"}
        if len(rest) > 1:
            multi[unit] = sorted(rest)
    assert not multi, f"these adapter units front more than one plane: {multi}"


def test_the_deontic_seam_is_visible_under_its_real_import_name():
    """The specific case that a prefix-based grep gets wrong."""
    assert _seam_map().get("loomground-deontic") == ["deontic.py"], (
        "loomground-deontic imports as `deontic`; a map keyed on the "
        "`loomground_` prefix reports this correct seam as missing")


def _consumers_outside_the_seam(dist: str) -> list[str]:
    """Where a plane with no adapter is consumed instead.

    Some planes are consumed directly and legitimately — `oversight-certificate`
    is an optional extra imported lazily by `oversight_cert`, so it has no seam
    by design. Printing "no adapter" alone would read as a defect.
    """
    out = []
    for f in sorted(PACKAGE_ROOT.rglob("*.py")):
        if ADAPTERS in f.parents:
            continue
        if dist in _planes_imported_by(f):
            out.append(str(f.relative_to(PACKAGE_ROOT)))
    return out


def test_print_the_map(capsys):
    """Not an assertion — the artifact. Run with -s to read it."""
    m = _seam_map()
    with capsys.disabled():
        print("\n  plane                      consumed through")
        for dist in sorted(FIRST_PARTY):
            fronted = m.get(dist, [])
            if dist == SUBSTRATE and "solver" in fronted:
                where = "adapters/solver  (+ composed by " \
                        + ", ".join(u for u in fronted if u != "solver") + ")"
            elif fronted:
                where = "adapters/" + fronted[0]
            elif dist in _TRANSITIVE_PROVIDER:
                where = "provider pin — installs it for loomground-ingest"
            else:
                outside = _consumers_outside_the_seam(dist)
                where = ("direct: " + ", ".join(outside)) if outside else "NOT CONSUMED"
            print(f"    {dist:<26} {where}")


def test_a_plane_is_either_fronted_consumed_or_a_declared_provider_pin():
    """No first-party pin may be unexplained.

    Three legitimate shapes: fronted by an adapter, consumed directly outside
    the seam (an optional extra like oversight-certificate), or a declared
    `_TRANSITIVE_PROVIDER` pin that exists so another plane is installable.
    Anything else is a genuine orphan, and this says so instead of leaving a
    reader to work it out.
    """
    m = _seam_map()
    unexplained = [d for d in sorted(FIRST_PARTY)
                   if d not in m
                   and d not in _TRANSITIVE_PROVIDER
                   and not _consumers_outside_the_seam(d)]
    assert not unexplained, (
        f"pinned, but fronted by no adapter, consumed nowhere, and not declared "
        f"a provider pin: {unexplained}")
