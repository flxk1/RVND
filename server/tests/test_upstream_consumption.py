"""Architecture gates: upstream engines are consumed, never copied or bypassed."""

from __future__ import annotations

import ast
from pathlib import Path
import re

from workspaces import mcp_server


ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "server" / "src" / "workspaces"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            # Preserve relativity so ``from ..versum`` (the local sanctioned
            # adapter) is not mistaken for an absolute upstream import.
            out.add("." * node.level + node.module)
    return out


def test_upstream_packages_are_direct_dependencies():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for dependency in ("loomground-versum", "loomground-solver",
                       "loomground-governance", "loomground-deontic",
                       "loomground-ingest"):
        assert dependency in text


def test_loomground_toolchain_is_release_pinned():
    """Every Loomground engine is consumed as an immutable git dependency,
    never a floating branch, and all four planes appear in the manifest.

    Full commit IDs make the exact upstream reviewed for governance compatibility
    immutable even if a release tag is later moved.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    direct_urls = [line for line in text.splitlines()
                   if "git+https://github.com/flxk1/" in line]
    assert len(direct_urls) == 5
    for line in direct_urls:
        revision = line.rsplit("@", 1)[-1].split('"', 1)[0]
        assert len(revision) == 40
        assert all(character in "0123456789abcdef" for character in revision)


def test_documented_release_commits_match_install_manifest():
    """The public resolution table and the actual install pins are one claim."""
    manifest = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    documented = (ROOT / "docs" / "release-dependency-resolution.md").read_text(
        encoding="utf-8"
    )
    for package in (
        "loomground-solver",
        "loomground-versum",
        "loomground-governance",
        "loomground-deontic",
        "loomground-ingest",
    ):
        installed = re.search(
            rf'"{re.escape(package)} @ git\+https://github\.com/flxk1/'
            rf'{re.escape(package)}@([0-9a-f]{{40}})"',
            manifest,
        )
        table_row = re.search(
            rf"^\| `{re.escape(package)}` \| [^|]+ \| `([0-9a-f]{{40}})` \|$",
            documented,
            re.MULTILINE,
        )
        assert installed is not None, f"missing immutable install pin: {package}"
        assert table_row is not None, f"missing documented release pin: {package}"
        assert installed.group(1) == table_row.group(1), package


def test_versum_imports_are_confined_to_adapter():
    violations = []
    for path in PKG.rglob("*.py"):
        imports = _imports(path)
        if any(name == "versum" or name.startswith("versum.") for name in imports):
            if "adapters/versum" not in path.as_posix():
                violations.append(path.relative_to(PKG).as_posix())
    assert not violations, f"direct Versum imports outside adapter: {violations}"


def test_policy_ingest_is_consumed_from_upstream_through_the_adapter():
    """RVND no longer grows its own policy ingester: the RVND-local
    ``ingest/policy.py`` is retired (fenced in ``test_no_parallel_structures``)
    and the governance compiler is consumed from ``loomground-ingest`` through the
    sanctioned adapter seam. The "both language packs consumed, never transitively"
    invariant now lives upstream inside ``loomground-ingest``; the RVND host reaches
    it only through this one seam (never a copied or bypassed ingester)."""
    assert not (PKG / "ingest" / "policy.py").exists(), (
        "ingest/policy.py (RVND-grown PolicyIngester) reappeared")
    imports = _imports(PKG / "adapters" / "ingest" / "governance.py")
    assert any(name == "loomground_ingest" or name.startswith("loomground_ingest.")
               for name in imports), (
        "the governance adapter must consume the compiler from loomground-ingest")


def test_solver_compatibility_modules_have_no_substantive_bodies():
    facades = (
        "dimensions.py", "reasoning.py", "predicate.py", "temporal.py",
        "reasoning_phases.py", "solver_topology.py",
    )
    for name in facades:
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        definitions = [n.name for n in tree.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        assert not definitions, f"{name} contains copied implementation: {definitions}"


def test_no_vendored_loomground_artifacts_remain():
    assert not any(path.is_file() for path in (PKG / "_loomground_data").rglob("*"))


def test_runtime_reports_one_aligned_loomground_governance():
    info = mcp_server.server_info()
    runtime = info["language_runtime"]
    assert runtime == {
        "name": "loomground",
        "rvnd": runtime["rvnd"],
        "solver": runtime["rvnd"],
        "versum": runtime["rvnd"],
        "aligned": True,
    }
    assert set(info["dependency_versions"]) == {
        "loomground-governance", "loomground-deontic", "loomground-ingest",
        "loomground-solver", "loomground-versum",
    }
    assert info["deontic_runtime"] == {
        "name": "deontic",
        "version": info["deontic_runtime"]["version"],
        "direct": True,
        "ingest": True,
    }
