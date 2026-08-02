# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the discipline gate — the third per-folder dial.

Covers the policy fields + helpers (enable/disable, round-trip, back-compat)
and the engine (audit / diff / check, manifest resolution, severities, and the
audit-chain write).
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import mock_open, patch

import pytest

from workspaces import (
    FolderPolicy,
    disable_discipline,
    enable_discipline,
    load_policy,
)
from workspaces.discipline import DEFAULT_MANIFEST, resolve_manifest, run_discipline


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "ws"
    f.mkdir()
    return f


def test_manifest_read_closes_file_handle():
    from workspaces.discipline import _read

    opener = mock_open(read_data="rules")
    with patch("workspaces.discipline.open", opener, create=True):
        assert _read("manifest.txt") == "rules"
    opener.return_value.__enter__.assert_called_once()
    opener.return_value.__exit__.assert_called_once()


# --- policy fields ----------------------------------------------------------
def test_discipline_defaults_off():
    p = FolderPolicy()
    assert p.discipline_enabled is False
    assert p.discipline_is_active is False
    assert p.discipline_manifest == ""


def test_discipline_roundtrip():
    p = FolderPolicy.from_dict(
        {"discipline_enabled": True, "discipline_manifest": "rules/d.json"})
    d = p.to_dict()
    assert d["discipline_enabled"] is True
    assert d["discipline_manifest"] == "rules/d.json"
    assert FolderPolicy.from_dict(d).discipline_is_active is True


def test_legacy_policy_grows_no_discipline_keys():
    # A pre-discipline policy file must round-trip without sprouting new keys.
    legacy = FolderPolicy.from_dict({"privacy_lock_enabled": True})
    assert "discipline_enabled" not in legacy.to_dict()
    assert "discipline_manifest" not in legacy.to_dict()


def test_enable_then_disable_discipline(folder):
    enable_discipline(folder, manifest="rules/d.json", actor="t")
    pol = load_policy(folder)
    assert pol.discipline_enabled is True
    assert pol.discipline_manifest == "rules/d.json"
    disable_discipline(folder, actor="t")
    assert load_policy(folder).discipline_enabled is False


def test_enable_discipline_writes_audit_event(folder, tmp_path):
    log_root = tmp_path / "log"
    enable_discipline(folder, actor="alex", log_root=log_root)
    # the policy-change event must be on the chain
    blob = "".join(p.read_text() for p in log_root.rglob("*.jsonl"))
    assert "discipline_enabled" in blob


# --- manifest resolution ----------------------------------------------------
def test_resolve_manifest_default(folder):
    assert resolve_manifest(folder) is DEFAULT_MANIFEST


def test_resolve_manifest_from_path(folder):
    custom = folder / "rules.json"
    custom.write_text(json.dumps({"rules": [], "scopes": {}}))
    man = resolve_manifest(folder, "rules.json")
    assert man["rules"] == []


# --- engine -----------------------------------------------------------------
def _seed(folder):
    (folder / "bad").mkdir()
    (folder / "bad" / "SKILL.md").write_text(
        "---\nname: bad\ndescription: has a <bracket>\n---\n")
    (folder / "pay.py").write_text('x = float("amount")\n# TODO later\n')
    (folder / "notes.md").write_text("still called Workspaceversum\n")


def test_audit_flags_fail_and_warn(folder):
    _seed(folder)
    res = run_discipline(folder, mode="audit", write_audit=False)
    assert res["failures"] == 1          # the angle-bracket SKILL.md
    assert res["warnings"] >= 2          # TODO, float-money, stale term
    assert res["clean"] is False
    rules = {f["rule"] for f in res["findings"]}
    assert "skill-description" in rules


def test_clean_folder_passes(folder):
    (folder / "ok").mkdir()
    (folder / "ok" / "SKILL.md").write_text(
        "---\nname: ok\ndescription: Clean and short.\n---\n")
    res = run_discipline(folder, mode="audit", write_audit=False)
    assert res["clean"] is True
    assert res["failures"] == 0


def test_check_mode_explicit_file(folder):
    _seed(folder)
    res = run_discipline(folder, mode="check",
                         files=[str(folder / "bad" / "SKILL.md")],
                         write_audit=False)
    assert res["scanned"] == 1
    assert res["failures"] == 1


def test_diff_mode_only_incoming(folder):
    # commit a clean baseline, then add offenders → diff sees only the new ones
    (folder / "ok").mkdir()
    (folder / "ok" / "SKILL.md").write_text(
        "---\nname: ok\ndescription: Clean and short.\n---\n")
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    subprocess.run(["git", "-C", str(folder), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(folder), "-c", "user.email=a@b.c",
                    "-c", "user.name=t", "commit", "-qm", "base"], check=True)
    _seed(folder)
    res = run_discipline(folder, mode="diff", write_audit=False)
    files = {f["file"] for f in res["findings"]}
    assert "ok/SKILL.md" not in files     # committed clean file not re-scanned
    assert res["failures"] == 1


def test_strict_promotes_warnings(folder):
    (folder / "pay.py").write_text("# TODO\n")
    lax = run_discipline(folder, mode="audit", write_audit=False)
    assert lax["clean"] is True           # warnings don't fail by default
    strict = run_discipline(folder, mode="audit", write_audit=False, strict=True)
    assert strict["clean"] is False       # under strict, a warning fails


def test_audit_chain_write(folder, tmp_path):
    _seed(folder)
    log_root = tmp_path / "log"
    res = run_discipline(folder, mode="audit", write_audit=True, log_root=log_root)
    assert res["audit"]["recorded"] is True
    blob = "".join(p.read_text() for p in log_root.rglob("*.jsonl"))
    assert "discipline-run" in blob


def test_stale_terms_mention_vs_usage(folder):
    # a live usage (no cue) is flagged; a mention (retired-context) is not
    (folder / "live.md").write_text("we still call it Workspaceversum here\n")
    (folder / "doc.md").write_text(
        "Workspaceversum is retired; the namespace is now Workspaces.\n")
    res = run_discipline(folder, mode="audit", write_audit=False)
    flagged = {f["file"] for f in res["findings"] if f["rule"] == "stale-terms"}
    assert "live.md" in flagged
    assert "doc.md" not in flagged
