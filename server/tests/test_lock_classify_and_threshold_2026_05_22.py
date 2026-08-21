# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for Lock v2 MCP tools — lock_classify_text + threshold (2026-05-22).

Three new MCP tools unblock the Privacy Lock artifact's stubbed
surfaces:
- `lock_classify_text(text, folder_context)` runs the Tier-B regex
  scan over arbitrary text and returns findings. No audit side-effects.
  Per-folder confidence threshold (if set) filters the output.
- `lock_threshold_get(folder_context)` reads the threshold.
- `lock_threshold_set(folder_context, threshold)` writes it, clamping
  to [0.0, 1.0].
"""
from __future__ import annotations

import importlib
from pathlib import Path



def _fresh_mcp(monkeypatch, log_root: Path):
    import rvnd.mcp_server as srv
    importlib.reload(srv)
    monkeypatch.setattr("rvnd.mcp_serving._log_root", lambda: log_root)
    return srv


# ---------------------------------------------------------------------------
# lock_classify_text
# ---------------------------------------------------------------------------


def test_classify_empty_text_returns_no_findings(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.lock_classify_text(text="", folder_context=str(folder))
    assert out["ok"] is True
    assert out["findings_count"] == 0
    assert out["findings"] == []


def test_classify_whitespace_only_returns_no_findings(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.lock_classify_text(text="   \n\t  ", folder_context=str(folder))
    assert out["findings_count"] == 0


def test_classify_email_detected(tmp_path, monkeypatch):
    """A plain email address should trigger a Tier-B finding."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.lock_classify_text(
        text="Please reach me at jane.doe\x40example.com next week.",
        folder_context=str(folder),
    )
    assert out["ok"] is True
    assert out["findings_count"] >= 1
    # Every finding has the expected wire shape
    for f in out["findings"]:
        assert "type" in f and "severity" in f and "confidence" in f
        assert isinstance(f["confidence"], float)
        assert "detail" in f
        assert f["tier"] == "B"


def test_classify_writes_no_audit_event(tmp_path, monkeypatch):
    """No mutation-log side-effects from a classify call."""
    from rvnd.mutation_log import MutationLog
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    srv.lock_classify_text(
        text="contact: a\x40b.com",
        folder_context=str(folder),
    )
    log = MutationLog(folder, log_root=log_root)
    events = list(log.replay())
    # Only a no-op replay; no skill-dispatch event written by classify
    dispatches = [e for e in events if e.pair_id == "skill-dispatch"]
    assert dispatches == []


def test_classify_without_folder_context_still_works(tmp_path, monkeypatch):
    """folder_context is optional — empty means no threshold filter."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.lock_classify_text(text="Email: a\x40b.com")
    assert out["ok"] is True
    assert out["threshold"] == 0.0
    assert out["folder_context"] == ""
    assert out["findings_count"] >= 1


def test_classify_non_string_input_rejected(tmp_path, monkeypatch):
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.lock_classify_text(text=12345, folder_context="")  # type: ignore
    assert out["ok"] is False
    assert "must be a string" in out["error"]


# ---------------------------------------------------------------------------
# lock_threshold_get / lock_threshold_set
# ---------------------------------------------------------------------------


def test_threshold_default_is_zero(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.lock_threshold_get(folder_context=str(folder))
    assert out["ok"] is True
    assert out["threshold"] == 0.0


def test_threshold_set_then_get_roundtrip(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    setted = srv.lock_threshold_set(folder_context=str(folder), threshold=0.85)
    assert setted["ok"] is True
    assert setted["threshold"] == 0.85
    assert setted["previous"] == 0.0
    got = srv.lock_threshold_get(folder_context=str(folder))
    assert got["threshold"] == 0.85


def test_threshold_clamped_above_one(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.lock_threshold_set(folder_context=str(folder), threshold=5.0)
    assert out["ok"] is True
    assert out["threshold"] == 1.0


def test_threshold_clamped_below_zero(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.lock_threshold_set(folder_context=str(folder), threshold=-0.5)
    assert out["ok"] is True
    assert out["threshold"] == 0.0


def test_threshold_non_numeric_rejected(tmp_path, monkeypatch):
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    out = srv.lock_threshold_set(folder_context=str(folder), threshold="high")  # type: ignore
    assert out["ok"] is False
    assert "must be a number" in out["error"]


def test_threshold_persists_across_load(tmp_path, monkeypatch):
    """Threshold set via MCP survives a policy file reload."""
    from rvnd.policy import load_policy
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    srv.lock_threshold_set(folder_context=str(folder), threshold=0.7)
    # Re-load directly via the policy library — bypasses any MCP state
    pol = load_policy(str(folder))
    assert pol.lock_confidence_threshold == 0.7


# ---------------------------------------------------------------------------
# Threshold filters classify_text findings
# ---------------------------------------------------------------------------


def test_classify_respects_per_folder_threshold(tmp_path, monkeypatch):
    """Setting a high threshold suppresses low-confidence findings."""
    folder = tmp_path / "wks"; folder.mkdir()
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)

    # First, classify with threshold=0 — get a baseline finding count
    baseline = srv.lock_classify_text(
        text="Please email me at a\x40b.com about case 12345.",
        folder_context=str(folder),
    )
    baseline_count = baseline["findings_count"]
    assert baseline_count >= 1

    # Bump threshold to 0.99 — Tier-B findings have confidence around 0.85-0.98
    # depending on the pattern, so 0.99 should suppress most or all.
    srv.lock_threshold_set(folder_context=str(folder), threshold=0.99)
    filtered = srv.lock_classify_text(
        text="Please email me at a\x40b.com about case 12345.",
        folder_context=str(folder),
    )
    assert filtered["threshold"] == 0.99
    assert filtered["findings_count"] <= baseline_count
    # All surviving findings must clear the threshold
    for f in filtered["findings"]:
        assert f["confidence"] >= 0.99


# ---------------------------------------------------------------------------
# server_info registration
# ---------------------------------------------------------------------------


def test_new_lock_tools_in_server_info(tmp_path, monkeypatch):
    """0.6.6+: the standalone lock_* tools were collapsed into the workspace_lock
    facade; server_info declares the facade, and the ops remain reachable
    through it (classify / threshold_get / threshold_set)."""
    log_root = tmp_path / "log"
    srv = _fresh_mcp(monkeypatch, log_root)
    tools = set(srv.server_info()["tools"])
    assert "workspace_lock" in tools
    ops = {o["op"] for o in srv.workspace_lock("help")["ops"]}
    assert {"classify", "threshold_get", "threshold_set"} <= ops
