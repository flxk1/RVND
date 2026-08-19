# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""In-process smoke matrix — records whether each declared operation can be
called in-process, and guards the harness against side effects.

IMPORTANT: this matrix is NOT a transport proof and NOT a support basis. It
invokes the facade Python functions in-process (see coverage_harness.CHANNELS);
it does not cross the MCP host, the HTTP ``/tool`` request, or the gateway
serving boundary. A green cell means the in-process call did not crash and
returned a plausible shape — nothing about public callability. The register
records this as the ``callable`` fact but never derives ``supported`` from it;
support requires public-transport success+refusal evidence (see the register
note and docs/evidence/transport-evidence-appendix.md). This gate also refuses
an empty supported set: a release cannot claim capability reconciliation when
there is nothing for the reconciliation to prove.

The matrix is built in a SUBPROCESS (``coverage_run.py``) that roots all state
(registry, keys, logs) in a disposable ``$HOME`` so it cannot mutate the user's
registry or leak process-global state into the suite. This module reads the
result, checks it against the committed artifact, and asserts the harness left
no external state behind. Regenerating the committed artifact is an explicit
command — ``python server/tests/coverage_run.py docs/evidence/capability-coverage-matrix.json``
— never a side effect of test collection.

Run: python -m pytest server/tests/test_capability_coverage.py -q
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from .coverage_run import OBLIGATIONS

# Driving the full matrix (below, at import time) can run for minutes — it
# exercises every registered operation, including local-model backends, in a
# subprocess. `slow` documents that; it does NOT skip the module-level build
# below, since marker-based deselection only applies after collection has
# already run this file's top-level code. The fast subset instead passes
# --ignore for this file (see Makefile test-fast); running it at all requires
# the full, unbounded suite.
pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[2]
REGISTER = json.loads((REPO / "docs" / "evidence" / "capability-register.json").read_text())["operations"]
MATRIX_ARTIFACT = REPO / "docs" / "evidence" / "capability-coverage-matrix.json"


def _real_registry_path() -> Path:
    # resolved against the CURRENT $HOME (conftest's test home under pytest)
    return Path.home() / ".workspace" / "log" / "known-rvnd.json"


def _snapshot(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "<absent>"


def _build_matrix_isolated() -> tuple[dict, str, str]:
    """Run coverage_run.py as a subprocess into a TEMP file (never the committed
    artifact). Snapshot the user registry before/after to prove the isolated
    harness changed no external state."""
    reg = _real_registry_path()
    before = _snapshot(reg)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        out = tf.name
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("coverage_run.py")), out],
        capture_output=True, text=True, timeout=1200)
    after = _snapshot(reg)
    if proc.returncode != 0:
        raise RuntimeError(f"coverage_run failed: {proc.stderr[-2000:]}")
    doc = json.loads(Path(out).read_text())
    return doc, before, after


_DOC, _REG_BEFORE, _REG_AFTER = _build_matrix_isolated()
MATRIX = _DOC["rows"]
DEFERRED_LEAKS = _DOC.get("deferred_leaks", [])

_SUPPORTED_STATUSES = ("ui-supported", "gateway-supported", "mcp-supported")
SUPPORTED = {(e["facade"], e["op"]) for e in REGISTER
             if e["status"] in _SUPPORTED_STATUSES}
DEFERRED = {(e["facade"], e["op"]) for e in REGISTER if e["status"] == "deferred"}


def _outcomes(rows) -> dict:
    """(facade, op, channel) -> (valid_ok, invalid_ok); drops the volatile
    ``reason`` text (it embeds temp paths) so drift is compared semantically."""
    return {(r["facade"], r["op"], r["channel"]): (r["valid_ok"], r["invalid_ok"])
            for r in rows}


def test_harness_left_no_external_state():
    # The isolated harness must not touch the user's registry.
    assert _REG_BEFORE == _REG_AFTER, (
        "coverage harness mutated the user workspace registry "
        f"({_real_registry_path()}): {_REG_BEFORE} -> {_REG_AFTER}")


def test_committed_smoke_matrix_is_current():
    # The committed artifact is compared, not rewritten at collection.
    assert MATRIX_ARTIFACT.exists(), (
        "committed matrix missing; regenerate with "
        "`python server/tests/coverage_run.py docs/evidence/capability-coverage-matrix.json`")
    committed = json.loads(MATRIX_ARTIFACT.read_text())["rows"]
    drift = _outcomes(MATRIX) != _outcomes(committed)
    assert not drift, (
        "committed capability-coverage-matrix.json is stale; regenerate with "
        "`python server/tests/coverage_run.py docs/evidence/capability-coverage-matrix.json` "
        "and commit it (do not let a test rewrite it).")


def test_matrix_reconciles_to_the_register_and_catalogue():
    obl = set(OBLIGATIONS)
    keys = [(r["facade"], r["op"], r["channel"]) for r in MATRIX]
    assert len(keys) == len(set(keys)), "duplicate matrix rows"
    assert set(keys) == obl, (
        f"matrix does not reconcile: missing={sorted(obl - set(keys))[:10]} "
        f"stale={sorted(set(keys) - obl)[:10]}")
    live = {(e["facade"], e["op"]) for e in REGISTER}
    assert len(live) == len(REGISTER), "register has duplicate ops"


def test_supported_register_is_non_empty():
    assert SUPPORTED, (
        "capability register has no supported operations; reconciliation would "
        "be vacuous. Promote only operations with committed public-transport "
        "success and refusal evidence.")


def test_every_supported_operation_is_callable_on_every_claimed_channel():
    matrix_ops = {(r["facade"], r["op"]) for r in MATRIX}
    absent = SUPPORTED - matrix_ops
    assert not absent, (
        "supported operations absent from the executable matrix: "
        f"{sorted(absent)}")
    fails = [f"{r['facade']}/{r['op']} [{r['channel']}]: {r['reason']}"
             for r in MATRIX if (r["facade"], r["op"]) in SUPPORTED and not r["valid_ok"]]
    assert not fails, "supported obligation(s) not callable:\n  " + "\n  ".join(fails)


def test_deferred_exposure_probe_is_recorded():
    # This probe crosses only the private gateway dispatch, so it is a
    # LOWER BOUND, not a full-surface no-leak statement. It must still not find a
    # deferred op reachable there; a real multi-surface reconciliation (MCP
    # discovery, HTTP/UI, CLI/chat help, gateway discovery) remains required.
    assert not DEFERRED_LEAKS, ("deferred ops reachable via private gateway dispatch:\n  "
                                + "\n  ".join(DEFERRED_LEAKS))
