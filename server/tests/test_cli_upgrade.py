# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""`workspaces upgrade` — safe, version/schema-aware upgrade.

The value under test is the *orchestration*: verify chains before, back up,
apply idempotent migrations, verify chains after, and stamp the version only on
a clean pass. The chain-verify / backup / keypair-migration primitives are
tested elsewhere, so here they are stubbed to keep the tests fast and hermetic
(no real keys touched, nothing written to the real home).
"""
from __future__ import annotations

import argparse
import io
import json

import rvnd.cli.impl as impl
import rvnd.backup as backup_mod
import rvnd.signing as signing
from rvnd._version import __version__ as CODE_VER


def _home(tmp_path):
    home = tmp_path / ".workspace"
    (home / "log").mkdir(parents=True)
    return home


def _stamp(home, version):
    (home / "version.json").write_text(
        json.dumps({"rvnd_version": version, "stamped_at": "x"}), encoding="utf-8")


def _run(monkeypatch, home, *, check=False, skip_backup=False):
    monkeypatch.setattr(impl, "LOG_ROOT_DEFAULT", home / "log")
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = impl.cmd_upgrade(argparse.Namespace(check=check, skip_backup=skip_backup))
    return rc, out.getvalue()


def _stub_verify(monkeypatch, *results):
    """Successive _verify_all_chains() calls return the given dicts in order."""
    seq = iter(results)
    last = results[-1]
    monkeypatch.setattr(impl, "_verify_all_chains", lambda _lr: next(seq, last))


def _stub_backup(monkeypatch):
    calls = {}
    def fake(home, path, *, passphrase=None):
        calls["home"] = str(home); calls["path"] = str(path)
        return {"archive": str(path), "file_count": 3, "total_bytes": 0, "encrypted": False}
    monkeypatch.setattr(backup_mod, "create_backup", fake)
    return calls


def _stub_migrate(monkeypatch):
    calls = {"n": 0}
    def fake(*, audit_log=None):
        calls["n"] += 1
        return "no legacy keypair (already per-host)"
    monkeypatch.setattr(signing, "migrate_legacy_keypair_to_host_subdir", fake)
    return calls


OK2 = {"total": 2, "ok": 2, "broken": []}


def test_verify_all_chains_empty_root(tmp_path):
    # Real (unmocked) — an empty log root has no chains and never errors.
    res = impl._verify_all_chains(tmp_path / "nope")
    assert res == {"total": 0, "ok": 0, "broken": []}


def test_check_reports_upgrade_advised_and_writes_nothing(monkeypatch, tmp_path):
    home = _home(tmp_path); _stamp(home, "0.0.1")
    _stub_verify(monkeypatch, OK2)
    rc, out = _run(monkeypatch, home, check=True)
    assert rc == 0
    assert "upgrade pass is advised" in out
    assert CODE_VER in out
    # read-only: the old stamp is untouched
    assert json.loads((home / "version.json").read_text())["rvnd_version"] == "0.0.1"


def test_check_up_to_date(monkeypatch, tmp_path):
    home = _home(tmp_path); _stamp(home, CODE_VER)
    _stub_verify(monkeypatch, OK2)
    rc, out = _run(monkeypatch, home, check=True)
    assert rc == 0
    assert "Up to date" in out


def test_upgrade_backs_up_migrates_and_stamps(monkeypatch, tmp_path):
    home = _home(tmp_path); _stamp(home, "0.0.1")
    _stub_verify(monkeypatch, OK2, OK2)          # before, after both intact
    bk = _stub_backup(monkeypatch)
    mig = _stub_migrate(monkeypatch)
    rc, out = _run(monkeypatch, home)
    assert rc == 0, out
    assert bk.get("home", "").endswith(".workspace")   # backed up the real home
    assert mig["n"] == 1                                 # migration ran
    assert "upgraded to" in out
    # stamp advanced to the installed version
    assert json.loads((home / "version.json").read_text())["rvnd_version"] == CODE_VER


def test_upgrade_aborts_and_keeps_stamp_if_a_chain_breaks(monkeypatch, tmp_path):
    home = _home(tmp_path); _stamp(home, "0.0.1")
    before = {"total": 2, "ok": 2, "broken": []}
    after = {"total": 2, "ok": 1, "broken": ["/ws/a"]}   # a chain broke during upgrade
    _stub_verify(monkeypatch, before, after)
    _stub_backup(monkeypatch); _stub_migrate(monkeypatch)
    rc, out = _run(monkeypatch, home)
    assert rc == 1
    assert "FAILED" in out and "restore" in out
    # the version stamp must NOT advance on a failed upgrade
    assert json.loads((home / "version.json").read_text())["rvnd_version"] == "0.0.1"


def test_upgrade_skip_backup(monkeypatch, tmp_path):
    home = _home(tmp_path); _stamp(home, "0.0.1")
    _stub_verify(monkeypatch, OK2, OK2)
    bk = _stub_backup(monkeypatch); _stub_migrate(monkeypatch)
    rc, out = _run(monkeypatch, home, skip_backup=True)
    assert rc == 0
    assert "WITHOUT a safety backup" in out
    assert bk == {}                              # create_backup never called


def test_upgrade_already_current_is_noop(monkeypatch, tmp_path):
    home = _home(tmp_path); _stamp(home, CODE_VER)
    _stub_verify(monkeypatch, OK2)
    bk = _stub_backup(monkeypatch)
    rc, out = _run(monkeypatch, home)
    assert rc == 0
    assert "Already current" in out
    assert bk == {}                              # nothing to do → no backup
