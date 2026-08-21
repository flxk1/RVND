# SPDX-License-Identifier: AGPL-3.0-only
"""Evidence must be accounted for, readable, and true about the current tree.

The failure this exists to prevent already happened. `transport-evidence-appendix.md`
documented the CLI channel as `python -m workspaces.cli` while the test exercised
`rvnd.cli`; it was wrong, survived a full package rename untouched, and no gate
noticed — because nothing checks that an evidence file still describes something
real. A committed baseline listing four gaps that no longer existed had the same
shape.

Three properties, each closing a different way evidence rots:

E3  nothing is write-only — every artifact names what produces or consumes it,
    so an emitted file cannot sit unread and be mistaken for a checked one;
E4  freshness — a generated artifact must match what its producer emits now, and
    prose must only name paths and commands that resolve;
E5  provenance — a generated artifact carries a schema tag, so a reader can tell
    what shape they are looking at.
"""
from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "docs" / "evidence"
MANIFEST = json.loads((EVIDENCE / "PRODUCERS.json").read_text(encoding="utf-8"))
ARTIFACTS = MANIFEST["artifacts"]


def _on_disk() -> set[str]:
    return {p.name for p in EVIDENCE.iterdir() if p.is_file()}


# ── E3: nothing unaccounted for, nothing write-only ─────────────────────────

def test_every_artifact_on_disk_is_declared():
    undeclared = sorted(_on_disk() - set(ARTIFACTS))
    assert not undeclared, (
        f"evidence files with no entry in PRODUCERS.json: {undeclared}. An "
        f"artifact nobody can regenerate and nobody reads is not evidence.")


def test_every_declared_artifact_exists():
    missing = sorted(a for a in ARTIFACTS if not (EVIDENCE / a).is_file())
    assert not missing, f"declared but absent: {missing}"


def test_every_artifact_is_gated_by_something_that_exists():
    """The E3 property: an emitted file with no reader is not checked evidence."""
    broken = {}
    for name, meta in ARTIFACTS.items():
        gates = meta.get("gated_by") or []
        if not gates:
            broken[name] = "no gate declared"
            continue
        absent = [g for g in gates if not (REPO / g).exists()]
        if absent:
            broken[name] = f"gate(s) do not exist: {absent}"
    assert not broken, f"evidence that nothing reads: {broken}"


# ── E4: freshness ───────────────────────────────────────────────────────────

def test_generated_artifacts_declare_how_to_regenerate_them():
    """Without a command, staleness cannot be detected at all — only guessed."""
    bad = [n for n, m in ARTIFACTS.items()
           if m["kind"] == "generated" and not m.get("regenerate")]
    assert not bad, f"generated but no regenerate command: {bad}"


@pytest.mark.parametrize("name", sorted(
    n for n, m in ARTIFACTS.items() if m["kind"] == "prose"))
def test_prose_evidence_names_only_things_that_resolve(name):
    """Prose rots silently: no import fails, no test breaks, it is simply untrue.

    Every `python -m X` it names must import, and every repo path it cites must
    exist. This is exactly the check the transport appendix needed.
    """
    text = (EVIDENCE / name).read_text(encoding="utf-8")
    sys.path.insert(0, str(REPO / "server" / "src"))
    try:
        bad_modules = []
        for mod in set(re.findall(r"python -m ([\w.]+)", text)):
            try:
                importlib.import_module(mod)
            except Exception as exc:            # noqa: BLE001 - reported
                bad_modules.append(f"{mod} ({type(exc).__name__})")
        bad_paths = sorted({p for p in re.findall(r"`((?:server|app|scripts|docs)/[\w./-]+)`", text)
                            if not (REPO / p).exists()})
    finally:
        sys.path.pop(0)
    assert not bad_modules, f"{name} names modules that do not import: {bad_modules}"
    assert not bad_paths, f"{name} cites paths that do not exist: {bad_paths}"


def test_the_lock_boundary_baseline_matches_the_current_tree():
    """The cheapest real freshness check: this producer has a gate mode, so a
    stale baseline fails it directly."""
    r = subprocess.run([sys.executable, "scripts/lock_boundary_check.py"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, (
        "lock-boundary-baseline.json no longer describes the tree:\n"
        + r.stdout[-800:] + r.stderr[-400:])


# ── E5: provenance ──────────────────────────────────────────────────────────

def test_generated_json_carries_a_schema_tag():
    """A reader has to be able to tell what shape they are holding, and a schema
    bump has to be visible rather than inferred from the keys."""
    missing = []
    for name, meta in ARTIFACTS.items():
        if meta["kind"] != "generated" or not name.endswith(".json"):
            continue
        data = json.loads((EVIDENCE / name).read_text(encoding="utf-8"))
        if isinstance(data, dict) and not any(k in data for k in ("schema", "$schema", "version")):
            missing.append(name)
    assert not missing, f"generated evidence with no schema/version tag: {missing}"
