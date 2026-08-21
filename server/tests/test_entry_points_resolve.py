# SPDX-License-Identifier: AGPL-3.0-only
"""Every console entry point must name the real package, not the compat alias.

Six of seven pointed at `workspaces.*` after the rename. They worked, because
the alias replaces its own sys.modules entry — so the failure was invisible and
would only surface on the day the alias is deleted, as every shipped command
breaking at once. Nothing imports an entry point, so no other test covers this.

The COMMAND names are contracts with hosts and existing installs and are not
asserted here; only the module path behind each one.
"""
from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(
    encoding="utf-8")).get("project", {}).get("scripts", {})


def test_there_are_entry_points_to_check():
    assert SCRIPTS, "no [project.scripts] found — this gate would prove nothing"


def test_no_entry_point_resolves_through_the_compat_alias():
    through_alias = {name: target for name, target in SCRIPTS.items()
                     if target.split(":")[0].split(".")[0] == "workspaces"}
    assert not through_alias, (
        "these resolve only while the compat alias exists, and will break the "
        f"day it is removed: {through_alias}")


def test_every_entry_point_target_actually_imports():
    broken = {}
    for name, target in SCRIPTS.items():
        mod, _, func = target.partition(":")
        try:
            m = importlib.import_module(mod)
        except Exception as exc:                      # noqa: BLE001 - reported, not raised
            broken[name] = f"{type(exc).__name__}: {exc}"
            continue
        if func and not hasattr(m, func):
            broken[name] = f"{mod} has no attribute {func!r}"
    assert not broken, f"entry points that would fail on invocation: {broken}"
