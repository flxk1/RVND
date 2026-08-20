# SPDX-License-Identifier: AGPL-3.0-only
"""A failed audit write must be reported, never silently succeeded past.

Each of these sites used to be `except Exception: pass`. The regression they
guard against is not a crash -- it is the absence of one: the operation returns
normally and nothing anywhere records that the audit write was lost. So every
test here forces the write to fail and asserts that (a) the operation still
returns, and (b) the drop is visible afterwards.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces import audit_drop


@pytest.fixture(autouse=True)
def _clean():
    audit_drop.clear()
    yield
    audit_drop.clear()


def _boom(*_a, **_k):
    raise OSError("disk gone")


def test_record_reports_to_stderr_process_register_and_marker(tmp_path, capsys):
    exc = OSError("disk gone")
    audit_drop.record("unit.test", exc, log_root=tmp_path, detail="x")

    assert [d["where"] for d in audit_drop.drops()] == ["unit.test"]
    assert "AUDIT WRITE DROPPED at unit.test" in capsys.readouterr().err

    marker = tmp_path / audit_drop.MARKER_NAME
    assert marker.exists(), "a drop must survive the process that observed it"
    row = json.loads(marker.read_text().splitlines()[0])
    assert row["where"] == "unit.test" and "disk gone" in row["error"]


def test_record_never_raises_even_when_the_marker_cannot_be_written(tmp_path):
    """The marker write is attempted in the conditions that broke the original
    write, so it must degrade rather than raise."""
    unwritable = tmp_path / "file-not-a-dir"
    unwritable.write_text("x")
    audit_drop.record("unit.test", OSError("x"), log_root=unwritable / "sub")
    assert audit_drop.drops(), "stderr + register still carry it"


def test_durable_drops_reports_corrupt_records_rather_than_skipping(tmp_path):
    (tmp_path / audit_drop.MARKER_NAME).write_text('{"where":"a"}\nnot json\n')
    rows = audit_drop.durable_drops(tmp_path)
    assert len(rows) == 2, "a corrupt drop record is still evidence of a drop"
    assert rows[1]["error"] == "unparseable drop record"


def test_egress_proxy_audit_log_failure_is_reported(tmp_path, monkeypatch):
    from workspaces.lock import egress_proxy as ep
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path))

    class _P:
        audit_log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("builtins.open", _boom)
    ep.EgressProxy.audit_log(_P(), {"kind": "proxy_block"})   # must not raise
    monkeypatch.undo()

    drops = audit_drop.drops()
    assert [d["where"] for d in drops] == ["egress_proxy.audit_log"]
    assert drops[0]["entry_kind"] == "proxy_block"


def test_doctor_reports_dropped_writes_as_an_error(tmp_path):
    from workspaces.cli.impl import _doctor_check_audit_drops
    assert _doctor_check_audit_drops(tmp_path)["level"] == "ok"

    audit_drop.record("egress_proxy.audit_log", OSError("x"), log_root=tmp_path)
    check = _doctor_check_audit_drops(tmp_path)
    assert check["level"] == "error", "a lost audit record is not a warning"
    assert "egress_proxy.audit_log" in check["detail"]


def test_lock_reports_through_the_injected_hook_not_an_import(monkeypatch, capsys):
    """The lock must not import audit_drop -- lock_boundary_check enforces that.
    It reports through host_deps.record_audit_drop instead."""
    from workspaces.lock import egress_proxy as ep, host_deps
    host_deps._wired = False
    ep._report_audit_drop("unit.wired", OSError("boom"), detail="d")
    assert [d["where"] for d in audit_drop.drops()] == ["unit.wired"]


def test_lock_falls_back_to_stderr_without_its_host(monkeypatch, capsys):
    """The extraction case: hooks unfilled. Degraded (no durable marker) but
    never silent.

    Worth a test of its own because the fallback is the branch no normal run
    takes: it was shipped calling `sys.stderr` in a module that never imported
    `sys`, and ruff's F82 (undefined name) -- which this repo selects -- did not
    flag it. Only executing the branch did.
    """
    from workspaces.lock import egress_proxy as ep, host_deps
    monkeypatch.setattr(host_deps, "record_audit_drop", None)
    monkeypatch.setattr(host_deps, "_wired", True)
    ep._report_audit_drop("unit.fallback", OSError("boom"))
    assert "AUDIT WRITE DROPPED at unit.fallback" in capsys.readouterr().err
    assert audit_drop.drops() == [], "no host means no in-process register either"
