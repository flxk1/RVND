# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Front-door install scripts: scripts/preflight.sh and bootstrap.sh.

These run on a bare machine (plain POSIX sh, no RVND install), so they are
tested by invoking the scripts directly. The CI runners have git + python +
curl, so the happy path is exercised for real; the bootstrap's safety guard is
tested without any network by pointing it at a non-empty directory.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "scripts" / "preflight.sh"
BOOTSTRAP = REPO_ROOT / "bootstrap.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh required")


def _run(args, env=None, cwd=REPO_ROOT):
    return subprocess.run(
        args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120,
    )


def test_scripts_exist_and_parse():
    assert PREFLIGHT.is_file() and BOOTSTRAP.is_file()
    # `sh -n` is a syntax-only check — guards against future edits breaking them.
    for s in (PREFLIGHT, BOOTSTRAP):
        r = _run(["sh", "-n", str(s)])
        assert r.returncode == 0, f"syntax error in {s.name}: {r.stderr}"


def test_preflight_reports_ready_on_ci():
    # CI runners have git + a modern python, so pre-flight must pass and name
    # the two required tools it checked.
    r = _run(["sh", str(PREFLIGHT)])
    assert r.returncode == 0, f"pre-flight failed on CI: {r.stdout}\n{r.stderr}"
    out = r.stdout
    assert "git:" in out
    assert "python:" in out
    assert "READY" in out


def test_bootstrap_help():
    r = _run(["sh", str(BOOTSTRAP), "--help"])
    assert r.returncode == 0
    assert "Usage:" in r.stdout and "TARGET_DIR" in r.stdout


def test_bootstrap_refuses_nonempty_target(tmp_path):
    # The clobber guard: a non-empty directory that isn't an RVND clone must be
    # refused BEFORE any clone happens — nothing in it is touched.
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "mine.txt").write_text("keep me", encoding="utf-8")

    import os
    env = dict(os.environ, RVND_DIR=str(target))
    r = _run(["sh", str(BOOTSTRAP)], env=env)

    assert r.returncode != 0
    assert "not empty" in (r.stdout + r.stderr).lower()
    # proof nothing was cloned over it
    assert (target / "mine.txt").read_text(encoding="utf-8") == "keep me"
    assert not (target / ".git").exists()
    assert not (target / "server").exists()
