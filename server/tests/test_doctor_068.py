# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the `workspaces doctor` preflight subcommand (068 A+I-r1 patch).

The doctor is the operator's first-five-minutes diagnostic: Python version,
required + optional dependencies, key-dir permissions, controller-key state,
log-root reachability, sample round-trip, NFS detection, MCP-server reach,
symlink mode.

Stable exit-code taxonomy:
    0  — all green
    10 — warnings only
    20 — errors present
"""
from __future__ import annotations

import json

import pytest

from rvnd import cli as _cli
from rvnd.cli import main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Each doctor run uses tmp_path for log-root + key-dir + writable HOME."""
    log_root = tmp_path / "logs"
    key_dir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(key_dir))
    monkeypatch.delenv("WORKSPACE_SYMLINK_MODE", raising=False)
    return {"log_root": log_root, "key_dir": key_dir, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# 1. Clean install returns zero (or, when MCP entry point isn't available,
#    a warning — but no errors). We accept either OK or WARN since the
#    test environment may not have workspaces-mcp on PATH.
# ---------------------------------------------------------------------------


def test_doctor_clean_install_returns_zero(isolated_env, capsys):
    """A clean install with no missing required deps exits 0 or 10 (warn-only).

    NEVER 20: missing optional MCP entry point is a warn, not an error.
    """
    rc = main(["--log-root", str(isolated_env["log_root"]),
               "doctor", "--skip-mcp"])
    out = capsys.readouterr().out
    assert "preflight diagnostics" in out
    # With --skip-mcp and required deps present, this should be 0 or 10.
    assert rc in (_cli.DOCTOR_EXIT_OK, _cli.DOCTOR_EXIT_WARN), (
        f"unexpected exit {rc}; output: {out}"
    )


# ---------------------------------------------------------------------------
# 2. Missing optional dependency warns, never errors.
# ---------------------------------------------------------------------------


def test_doctor_missing_optional_dep_warns_not_errors(isolated_env, monkeypatch,
                                                       capsys):
    """If pypdf/python-docx/mcp aren't importable, the check is WARN level."""
    import importlib

    real_import = importlib.import_module

    def _fail_optional(name, *args, **kwargs):
        if name in ("pypdf", "docx", "mcp"):
            raise ImportError(f"simulated missing: {name}")
        return real_import(name, *args, **kwargs)

    # Monkeypatch __import__ so the doctor's optional-dep probe sees them missing
    import builtins
    real_builtin_import = builtins.__import__

    def _patched(name, globals=None, locals=None, fromlist=(), level=0):
        if name in ("pypdf", "docx", "mcp"):
            raise ImportError(f"simulated missing: {name}")
        return real_builtin_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _patched)

    # Call the check functions directly so we don't trip over the full doctor.
    opt = {c["name"]: c["level"] for c in _cli._doctor_check_optional_deps()}
    assert opt["opt_dep_pypdf"] == _cli.DOCTOR_LEVEL_WARN
    assert opt["opt_dep_python-docx"] == _cli.DOCTOR_LEVEL_WARN
    # `mcp` was promoted to a required dependency: it must no longer appear in
    # the optional set, and optional-missing is never an ERROR.
    assert "opt_dep_mcp" not in opt
    assert _cli.DOCTOR_LEVEL_ERROR not in opt.values()
    # `mcp` is now REQUIRED — missing → ERROR in the required-deps check.
    req = {c["name"]: c["level"] for c in _cli._doctor_check_required_deps()}
    assert req["dep_mcp"] == _cli.DOCTOR_LEVEL_ERROR


# ---------------------------------------------------------------------------
# 3. Uninitialised controller key is INFO, not error.
#    Controller key is only needed for purge/erase; basic memory works without.
# ---------------------------------------------------------------------------


def test_doctor_uninitialised_controller_key_is_info_not_error(isolated_env):
    """A fresh keydir (no controller.priv) reports INFO, not ERROR or WARN."""
    # Confirm no controller key exists yet.
    from rvnd import signing
    assert not signing._controller_private_key_path().exists()

    result = _cli._doctor_check_controller_key()
    assert result["name"] == "controller_key"
    assert result["level"] == _cli.DOCTOR_LEVEL_INFO
    assert "init-controller" in result["detail"]


# ---------------------------------------------------------------------------
# 4. Sample round-trip appends + verifies cleanly.
# ---------------------------------------------------------------------------


def test_doctor_sample_round_trip_appends_and_verifies(isolated_env):
    """The doctor's sample round-trip must produce ok=True from verify_chain."""
    result = _cli._doctor_check_sample_round_trip(isolated_env["log_root"])
    assert result["name"] == "sample_round_trip"
    assert result["level"] == _cli.DOCTOR_LEVEL_OK, (
        f"sample round-trip failed: {result}"
    )
    assert "appended + verified" in result["detail"]


def test_doctor_sample_round_trip_ok_under_allowlist_enforcement(
        isolated_env, monkeypatch):
    """The probe goes green on an ENFORCING install — the profile every real
    machine runs (conftest's suite-global opt-out masked this: the probe's
    scratch folder is unregistered, so fresh installs saw [x] sample_round_trip
    and doctor could never report a healthy engine). The probe must scope the
    unregistered-allow to itself, restore the prior environment, and leave
    enforcement intact for everything that is not the probe.
    """
    import os
    import tempfile

    from rvnd.folder_context import (
        ALLOW_UNREGISTERED_ENV,
        FolderContextNotAllowed,
    )
    from rvnd.mutation_log import MutationLog

    monkeypatch.delenv(ALLOW_UNREGISTERED_ENV, raising=False)

    result = _cli._doctor_check_sample_round_trip(isolated_env["log_root"])
    assert result["level"] == _cli.DOCTOR_LEVEL_OK, (
        f"probe must not depend on {ALLOW_UNREGISTERED_ENV}: {result}"
    )
    assert "appended + verified" in result["detail"]

    # No leak: the probe restored the environment it found.
    assert os.environ.get(ALLOW_UNREGISTERED_ENV) is None

    # Enforcement is untouched outside the probe: an unregistered scratch
    # folder (system TMPDIR — never under any registered root, including the
    # hardened profile's per-test registration of tmp_path) is still refused.
    with tempfile.TemporaryDirectory(prefix="doctor-a6-check-") as td:
        with pytest.raises(FolderContextNotAllowed):
            MutationLog(td, log_root=isolated_env["log_root"])


# ---------------------------------------------------------------------------
# 5. JSON output parses cleanly and carries the expected schema.
# ---------------------------------------------------------------------------


def test_doctor_json_output_parseable(isolated_env, capsys):
    rc = main(["--log-root", str(isolated_env["log_root"]),
               "doctor", "--skip-mcp", "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["exit_code"] == rc
    assert "checks" in parsed and isinstance(parsed["checks"], list)
    assert len(parsed["checks"]) >= 8
    # Every check has the expected shape.
    for c in parsed["checks"]:
        assert "name" in c and "level" in c and "detail" in c
        assert c["level"] in (
            _cli.DOCTOR_LEVEL_OK, _cli.DOCTOR_LEVEL_INFO,
            _cli.DOCTOR_LEVEL_WARN, _cli.DOCTOR_LEVEL_ERROR,
        )
    # Counts are present and sum to len(checks).
    counts = parsed["counts"]
    assert (counts["ok"] + counts["info"] + counts["warn"]
            + counts["error"] == len(parsed["checks"]))
    # Exit-code taxonomy is exposed.
    assert "exit_code_taxonomy" in parsed


# ---------------------------------------------------------------------------
# 6. Exit codes match the stable taxonomy: 0 / 10 / 20.
# ---------------------------------------------------------------------------


def test_doctor_exit_codes_match_taxonomy():
    """Direct test of the level-to-exit-code reducer."""
    # All OK → 0.
    all_ok = [{"name": "a", "level": _cli.DOCTOR_LEVEL_OK, "detail": ""}]
    assert _cli._doctor_overall_exit(all_ok) == _cli.DOCTOR_EXIT_OK

    # OK + INFO → 0 (info is benign).
    ok_info = [
        {"name": "a", "level": _cli.DOCTOR_LEVEL_OK, "detail": ""},
        {"name": "b", "level": _cli.DOCTOR_LEVEL_INFO, "detail": ""},
    ]
    assert _cli._doctor_overall_exit(ok_info) == _cli.DOCTOR_EXIT_OK

    # OK + WARN → 10.
    ok_warn = [
        {"name": "a", "level": _cli.DOCTOR_LEVEL_OK, "detail": ""},
        {"name": "b", "level": _cli.DOCTOR_LEVEL_WARN, "detail": ""},
    ]
    assert _cli._doctor_overall_exit(ok_warn) == _cli.DOCTOR_EXIT_WARN

    # OK + WARN + ERROR → 20 (error dominates).
    ok_warn_err = [
        {"name": "a", "level": _cli.DOCTOR_LEVEL_OK, "detail": ""},
        {"name": "b", "level": _cli.DOCTOR_LEVEL_WARN, "detail": ""},
        {"name": "c", "level": _cli.DOCTOR_LEVEL_ERROR, "detail": ""},
    ]
    assert _cli._doctor_overall_exit(ok_warn_err) == _cli.DOCTOR_EXIT_ERROR

    # Only ERROR → 20.
    only_err = [{"name": "a", "level": _cli.DOCTOR_LEVEL_ERROR, "detail": ""}]
    assert _cli._doctor_overall_exit(only_err) == _cli.DOCTOR_EXIT_ERROR

    # Confirm the constants themselves are the documented values.
    assert _cli.DOCTOR_EXIT_OK == 0
    assert _cli.DOCTOR_EXIT_WARN == 10
    assert _cli.DOCTOR_EXIT_ERROR == 20
