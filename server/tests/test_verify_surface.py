# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Surface verifier: the console-coverage gate must classify every declared
op into exactly one of three states and fail only when the gap GROWS.

Claims under test (written before the logic):
  V1  classify_token: quoted multi-word op -> surfaced; quoted generic
      (no underscore) -> ambiguous; absent -> unsurfaced
  V2  a match requires the op as a complete quoted string — an op that only
      appears inside a longer quoted literal does not count
  V3  build_op_inventory enumerates every _DECLARED_TOOLS entry; op-based
      facades contribute one row per help op, standalone tools one row
  V4  gate_against_baseline flags only NEW gaps; known and healed gaps pass
  V5  module roll-up: reached via static import closure from mcp_server;
      "internal by design" in a docstring exempts; otherwise unreferenced
  V6  the committed baseline gate passes end-to-end (exit 0) — the CI rule
  V7  a module named in the APPROVED_NONVISUAL_CONTRACTS registry (and not
      otherwise reached or internal) is cli-contract, not a surface gap; a
      module absent from the registry is unreferenced even when the CLI
      imports it; mcp reach and an explicit internal declaration both outrank
      registry membership
  V8  every registered contract names a module the COMMITTED candidate
      contains (read from HEAD's tree, not the index — a staged-but-uncommitted
      module is not in the candidate) and that exists as source; source presence
      is git-independent and enforced even when the committed tree cannot be
      resolved, so git unavailability narrows the check, it does not silence it

Run: python -m pytest server/tests/test_verify_surface.py -q
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("verify_surface", REPO / "scripts" / "verify_surface.py")
vs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vs)


def test_three_states_from_quoted_matches():                      # V1
    html = "tool('workspace_audit',{op:'verify_chain'}); get('list')"
    assert vs.classify_token("verify_chain", html) == "surfaced"
    assert vs.classify_token("list", html) == "ambiguous"
    assert vs.classify_token("shadow_scan", html) == "unsurfaced"


def test_quote_styles_all_match():                                # V1
    assert vs.classify_token("drift_report", 'x("drift_report")') == "surfaced"
    assert vs.classify_token("drift_report", "x(`drift_report`)") == "surfaced"


def test_substring_inside_longer_literal_is_no_match():           # V2
    html = "call('patch_apply_all')"
    assert vs.classify_token("patch_apply", html) == "unsurfaced"


def test_inventory_covers_every_declared_tool():                  # V3
    mcp = vs.load_mcp_module()
    rows = vs.build_op_inventory(mcp)
    facades = {r["facade"] for r in rows}
    assert facades == set(mcp._DECLARED_TOOLS)
    op_based = [r for r in rows if r["op"]]
    assert len(op_based) > 100
    standalone = [r for r in rows if r["op"] is None]
    assert {r["facade"] for r in standalone} >= {
        "cross_workspace_read", "workspace_orchestrate", "workspace_ask", "server_info"}
    assert all(r["match_token"] for r in rows)


def test_gate_flags_only_new_gaps():                              # V4
    baseline = {"unsurfaced_ops": ["a.x", "a.y"], "unreferenced_modules": ["m1"]}
    new_ops, new_mods = vs.gate_against_baseline(["a.x", "b.z"], ["m1", "m2"], baseline)
    assert new_ops == ["b.z"]
    assert new_mods == ["m2"]
    healed = vs.gate_against_baseline(["a.x"], [], baseline)
    assert healed == ([], [])


def test_module_rollup_reach_and_exemption(tmp_path, monkeypatch):  # V5
    pkg = tmp_path / "workspaces"
    pkg.mkdir()
    (pkg / "mcp_server.py").write_text("from .reached_mod import f\n")
    (pkg / "reached_mod.py").write_text("def f():\n    return 1\n")
    (pkg / "declared_mod.py").write_text('"""Probe battery; internal by design."""\n')
    (pkg / "orphan_mod.py").write_text("def g():\n    return 2\n")
    monkeypatch.setattr(vs, "PACKAGE_DIR", pkg)
    rollup = vs.build_module_rollup()
    assert rollup["mcp_server"] == "reached"
    assert rollup["reached_mod"] == "reached"
    assert rollup["declared_mod"] == "internal-declared"
    assert rollup["orphan_mod"] == "unreferenced"


def test_lazy_in_function_imports_count_as_reach(tmp_path, monkeypatch):  # V5
    pkg = tmp_path / "workspaces"
    pkg.mkdir()
    (pkg / "mcp_server.py").write_text(
        "def tool():\n    from .lazy_mod import run\n    return run()\n")
    (pkg / "lazy_mod.py").write_text("def run():\n    return 1\n")
    monkeypatch.setattr(vs, "PACKAGE_DIR", pkg)
    assert vs.build_module_rollup()["lazy_mod"] == "reached"


def _contract_membership_violations(registry, committed_modules, package_dir):
    """Two independent checks on the non-visual-contract registry, returned as
    (no_source, absent_from_head).

    ``no_source`` — modules with no source file under ``package_dir`` — needs no
    git and is always computed. ``absent_from_head`` — modules not in the
    committed tree — needs ``committed_modules``; pass None when the committed
    tree cannot be resolved (git unavailable) and it is reported empty, leaving
    the source-presence check to stand on its own."""
    def _has_source(name):
        rel = Path(*name.split("."))
        return ((package_dir / rel.with_suffix(".py")).is_file()
                or (package_dir / rel / "__init__.py").is_file())
    no_source = sorted(n for n in registry if not _has_source(n))
    absent_from_head = ([] if committed_modules is None
                        else sorted(set(registry) - set(committed_modules)))
    return no_source, absent_from_head


def _committed_workspace_modules():
    """Dotted module names committed under server/src/rvnd at HEAD
    (a package's __init__.py contributes the package name itself), or None
    when git cannot resolve the committed tree."""
    committed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", "server/src/rvnd"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO))
    if committed.returncode != 0:
        return None
    names = set()
    for line in committed.stdout.splitlines():
        if not line.endswith(".py"):
            continue
        rel = Path(line).relative_to("server/src/rvnd")
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            names.add(".".join(parts))
    return names


def test_registered_contracts_name_modules_the_candidate_contains():   # V8
    """A registry entry approves a supported contract, so it may name only a
    module the committed candidate contains, and that module must exist as
    source. Committed-tree membership is read from HEAD, never the index (a
    staged-but-uncommitted file is not in the candidate). Source presence needs
    no git and is enforced unconditionally, so git being unavailable narrows
    what is checked, it does not silence the check."""
    committed_modules = _committed_workspace_modules()
    no_source, absent_from_head = _contract_membership_violations(
        vs.APPROVED_NONVISUAL_CONTRACTS, committed_modules, vs.PACKAGE_DIR)

    # git-independent, always asserted
    assert not no_source, (
        f"registry names non-visual contracts with no source module: {no_source}. "
        "A classification must resolve to code that exists.")

    if committed_modules is None:
        import pytest
        pytest.skip("git unavailable: committed-tree membership unchecked; "
                    "source presence was enforced above")

    assert not absent_from_head, (
        "registry approves non-visual contracts absent from the committed "
        f"candidate: {absent_from_head}. Register a module after it is committed "
        "under its own reviewed scope; leave it a visible gap until then.")


def test_v8_source_presence_holds_without_git(tmp_path):               # V8
    """git unavailable (committed_modules=None): membership is not evaluated,
    but a registered module with no source file still fails."""
    (tmp_path / "present.py").write_text("x = 1\n")
    registry = {"present": "shipped", "absent": "no file"}
    no_source, absent_from_head = _contract_membership_violations(
        registry, None, tmp_path)
    assert no_source == ["absent"]      # caught with no git
    assert absent_from_head == []       # membership deferred, not faked


def test_v8_committed_membership_catches_staged_only(tmp_path):        # V8
    """git available: a module present as source but not in HEAD (staged only)
    is caught by the committed-tree check that the source-presence check alone
    would miss."""
    (tmp_path / "shipped.py").write_text("x = 1\n")
    (tmp_path / "staged.py").write_text("x = 1\n")
    registry = {"shipped": "in HEAD", "staged": "staged, not committed"}
    no_source, absent_from_head = _contract_membership_violations(
        registry, {"shipped"}, tmp_path)
    assert no_source == []               # both exist as source
    assert absent_from_head == ["staged"]  # only HEAD membership catches it


def test_registered_module_is_a_contract_unregistered_is_a_gap(tmp_path, monkeypatch):  # V7
    pkg = tmp_path / "workspaces"
    pkg.mkdir()
    (pkg / "mcp_server.py").write_text("from .reached_mod import f\n")
    (pkg / "reached_mod.py").write_text("def f():\n    return 1\n")
    # cli imports both, but only the registered one is an approved contract;
    # transitive import reach must NOT launder the other into a contract.
    (pkg / "cli.py").write_text(
        "from .registered_mod import report\nfrom .cli_only_mod import helper\n")
    (pkg / "registered_mod.py").write_text("def report():\n    return {}\n")
    (pkg / "cli_only_mod.py").write_text("def helper():\n    return 0\n")
    (pkg / "orphan_mod.py").write_text("def g():\n    return 2\n")
    monkeypatch.setattr(vs, "PACKAGE_DIR", pkg)
    monkeypatch.setattr(vs, "APPROVED_NONVISUAL_CONTRACTS",
                        {"registered_mod": "documented CLI report; non-visual by design"})

    rollup = vs.build_module_rollup()

    assert rollup["registered_mod"] == "cli-contract"
    assert rollup["cli_only_mod"] == "unreferenced"
    assert rollup["orphan_mod"] == "unreferenced"
    assert rollup["reached_mod"] == "reached"


def test_mcp_reach_and_internal_declaration_outrank_registry(tmp_path, monkeypatch):  # V7
    pkg = tmp_path / "workspaces"
    pkg.mkdir()
    (pkg / "mcp_server.py").write_text("from .shared_mod import f\n")
    (pkg / "shared_mod.py").write_text("def f():\n    return 1\n")
    (pkg / "declared_mod.py").write_text(
        '"""Probe battery; internal by design."""\n\n\ndef g():\n    return 2\n')
    monkeypatch.setattr(vs, "PACKAGE_DIR", pkg)
    # Both are listed, yet reach and the internal declaration win.
    monkeypatch.setattr(vs, "APPROVED_NONVISUAL_CONTRACTS",
                        {"shared_mod": "listed but also reached from mcp_server",
                         "declared_mod": "listed but also declared internal"})

    rollup = vs.build_module_rollup()

    assert rollup["shared_mod"] == "reached"
    assert rollup["declared_mod"] == "internal-declared"


def test_committed_baseline_gate_passes():                        # V6
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "verify_surface.py")],
                       capture_output=True, text=True, timeout=120, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NO NEW GAPS" in r.stdout
