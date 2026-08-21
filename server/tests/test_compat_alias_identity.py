# SPDX-License-Identifier: AGPL-3.0-only
"""The compat alias must return the SAME module object, not a second copy.

`sys.modules["workspaces"] = rvnd` aliases the top-level name only. Importing
`workspaces.mcp_serving` still drives the import machinery for that dotted name
and builds a second module from rvnd's __path__ — same source, separate globals.

That failed OPEN. A caller on the legacy path setting a request principal wrote
to one ContextVar while the enforcement path read another, so
`get_request_principal()` returned None and the per-principal registry scope
returned the FULL registry to a principal who is a member of nothing. Both
copies "worked"; only their state disagreed, and nothing said so.
"""
from __future__ import annotations

import warnings

import pytest


@pytest.mark.parametrize("name", [
    "mcp_serving", "principal", "registry", "folder_context",
    "mutation_log", "policy", "lock.core",
])
def test_legacy_path_is_the_same_object(name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        legacy = __import__(f"workspaces.{name}", fromlist=["_"])
        real = __import__(f"rvnd.{name}", fromlist=["_"])
    assert legacy is real, (
        f"workspaces.{name} is a second copy of rvnd.{name}: same code, separate "
        f"module state. Anything stored at module scope — a ContextVar, a cache, "
        f"a registry — silently stops being shared.")


def test_a_principal_set_through_the_legacy_path_reaches_enforcement():
    """The concrete failure, not just object identity.

    This is the property that broke: the scope filter reads through `principal`,
    so a principal set on a *different* copy is invisible and the fail-closed
    filter never engages.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from workspaces.mcp_serving import clear_request_principal, set_request_principal
    from rvnd.principal import get_request_principal

    clear_request_principal()
    set_request_principal("intruder", "intruder", rung="proxy-verified")
    try:
        seen = get_request_principal()
    finally:
        clear_request_principal()
    assert seen is not None and seen.get("principal") == "intruder", (
        "a principal set through the legacy import path was invisible to the "
        "enforcement path — the per-principal scope would fail OPEN")


def test_dash_m_under_the_legacy_name_refuses_clearly():
    """The alias covers imports, not `python -m`.

    Running an aliased package as a script re-enters it under the old name, so
    its relative imports resolve above the package and fail. Supporting that
    means rewriting __package__ and the parent chain — a lot of import machinery
    for a legacy spelling. It refuses and names the command to run instead,
    because the alternative was an AttributeError from inside runpy that says
    nothing about what to do.
    """
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "workspaces.cli", "--version"],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "python -m rvnd.cli" in (r.stderr + r.stdout), (
        "the refusal must name the supported command, not just fail")


def test_the_alias_has_not_outlived_its_removal_version():
    """Migration scaffolding needs an expiry, or it becomes architecture.

    This alias re-points sys.modules, installs a meta-path finder and silences an
    import warning — real machinery, kept only so consumers outside this repo
    survive the rename. When the version reaches REMOVE_AT_VERSION this fails, so
    keeping it becomes a decision someone makes rather than a comment that ages
    out unread.
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import workspaces as _alias          # noqa: F401 - importing IS the test
    import sys

    shim = sys.modules.get("workspaces")
    remove_at = getattr(shim, "REMOVE_AT_VERSION", None)
    if remove_at is None:      # sys.modules holds rvnd itself once aliased
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_alias_src",
            Path(__file__).resolve().parents[1] / "src" / "workspaces" / "__init__.py")
        remove_at = None
        text = spec.origin and Path(spec.origin).read_text(encoding="utf-8")
        for line in (text or "").splitlines():
            if line.startswith("REMOVE_AT_VERSION"):
                remove_at = line.split("=", 1)[1].strip().strip('"')
                break
    assert remove_at, "the compat alias declares no removal version"

    from rvnd._version import __version__

    def _v(s):
        return tuple(int(x) for x in s.split(".") if x.isdigit())

    assert _v(__version__) < _v(remove_at), (
        f"RVND is {__version__} and the compat alias was to be removed at "
        f"{remove_at}. Delete server/src/workspaces/ and this test, or move the "
        f"date deliberately — do not let scaffolding become architecture.")


def test_nothing_inside_this_repo_imports_the_alias():
    """The alias is for consumers OUTSIDE the repo. An internal import of it
    would keep it alive for a reason that is ours to remove."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "rvnd"
    offenders = []
    for f in src.rglob("*.py"):
        if "_quarantine" in str(f):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                if any(a.name.split(".")[0] == "workspaces" for a in n.names):
                    offenders.append(f"{f.name}:{n.lineno}")
            elif isinstance(n, ast.ImportFrom) and not n.level and n.module:
                if n.module.split(".")[0] == "workspaces":
                    offenders.append(f"{f.name}:{n.lineno}")
    assert not offenders, (
        f"rvnd imports its own compatibility alias: {offenders}. The alias exists "
        f"for outside consumers; importing it internally makes removing it harder "
        f"for a reason we created.")
