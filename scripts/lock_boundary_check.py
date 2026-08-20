#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Lock boundary gate — the privacy lock is the server's enforcement core,
and its import boundary is a ratchet: every import edge that crosses
workspaces/lock in either direction must appear in the committed baseline
(docs/evidence/lock-boundary-baseline.json). A new crossing fails the gate;
removing an edge and rewriting the baseline shrinks the allowlist. The goal
state is a declared lock API inbound and injected dependencies outbound.

  python3 scripts/lock_boundary_check.py                   gate
  python3 scripts/lock_boundary_check.py --write-baseline  record current edges
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "server" / "src" / "rvnd"
BASELINE = ROOT / "docs" / "evidence" / "lock-boundary-baseline.json"

def _module_parts(path: Path) -> tuple[list[str], bool]:
    """(dotted parts, is_package_init) for a file under the package root."""
    rel = path.relative_to(PKG).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        return parts[:-1], True
    return parts, False


def _import_targets(tree: ast.AST, parts: list[str], is_init: bool
                    ) -> set[tuple[str, frozenset[str] | None]]:
    """(target, imported-names) pairs this module imports, resolved within the
    workspaces package. AST-based so ``from .. import x``, alias lists and
    in-function imports all resolve; names outside the package yield nothing.
    ``imported-names`` carries the alias list when the target is a module a
    ``from`` statement pulled names out of, None when the module itself is
    the import (plain ``import``); the boundary gate uses it to recognise
    imports of the lock's declared API.
    """
    pkg = parts if is_init else parts[:-1]
    targets: set[tuple[str, frozenset[str] | None]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = frozenset(a.name for a in node.names)
            if node.level > 0:
                up = node.level - 1
                if up > len(pkg):
                    continue
                base = pkg[: len(pkg) - up]
                if node.module:
                    targets.add((".".join(base + node.module.split(".")), names))
                else:
                    for alias in node.names:
                        targets.add((".".join(base + [alias.name]), None))
            elif node.module == "rvnd":
                for alias in node.names:
                    targets.add((alias.name, None))
            elif node.module and node.module.startswith("rvnd."):
                targets.add((node.module[len("rvnd."):], names))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                p = alias.name.split(".")
                if p[0] == "rvnd" and len(p) > 1:
                    targets.add((".".join(p[1:]), None))
    return {(t, n) for t, n in targets if t}


def _lock_public_api() -> set[str]:
    """Names the lock package binds at its top level — its declared inbound
    API. A host import of the form ``from rvnd.lock import <name>``
    resolving to one of these is the sanctioned crossing and not an edge."""
    import ast as _ast
    names: set[str] = set()
    tree = _ast.parse((PKG / "lock" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, _ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (_ast.Assign,)):
            for tgt in node.targets:
                if isinstance(tgt, _ast.Name):
                    names.add(tgt.id)
    return names


def edges() -> list[str]:
    found = set()
    api = _lock_public_api()
    for py in sorted(PKG.rglob("*.py")):
        parts, is_init = _module_parts(py)
        importer = ".".join(parts)
        in_lock = importer == "lock" or importer.startswith("lock.")
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for target, names in _import_targets(tree, parts, is_init):
            target_in_lock = target == "lock" or target.startswith("lock.")
            if in_lock and not target_in_lock:
                found.add(f"lock:{importer} -> {target}")
            elif not in_lock and target_in_lock:
                if target == "lock" and names is not None and names <= api:
                    continue
                found.add(f"host:{importer} -> {target}")
    return sorted(found)


def main() -> int:
    current = edges()
    if "--write-baseline" in sys.argv:
        BASELINE.write_text(json.dumps({"edges": current}, indent=2) + "\n",
                            encoding="utf-8")
        print(f"lock-boundary: baseline written ({len(current)} edges)")
        return 0
    if not BASELINE.exists():
        print("lock-boundary: NO BASELINE — run with --write-baseline and commit it")
        return 1
    known = set(json.loads(BASELINE.read_text(encoding="utf-8"))["edges"])
    new = [e for e in current if e not in known]
    gone = sorted(known - set(current))
    if new:
        for e in new:
            print(f"  NEW boundary crossing: {e}")
        print("lock-boundary: FAIL — route new inbound uses through the lock's"
              " declared API, and new outbound needs through an injected"
              " dependency; do not widen the baseline")
        return 1
    if gone:
        print(f"lock-boundary: {len(gone)} baselined edge(s) no longer present —"
              " rewrite the baseline to ratchet down")
    print(f"lock-boundary: clean ({len(current)} known edges, 0 new)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
