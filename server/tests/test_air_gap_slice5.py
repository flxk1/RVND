# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Air-gap enforcement.

A folder set ``local_llm.mode = local-only`` must NEVER reach a cloud tier, and
the resolver must fail CLOSED (treat as air-gapped) when it cannot trust the
policy file — mirroring verified_cost_cap. Lock/Shield + local-LLM panels."""
from __future__ import annotations

import json

import pytest

from rvnd.policy import (
    FolderPolicy, LocalLlmPolicy, is_air_gapped, save_policy,
    POLICY_FILENAME, LEGACY_POLICY_FILENAME,
    LOCAL_LLM_MODE_LOCAL_ONLY, LOCAL_LLM_MODE_CLOUD_FALLBACK,
)
from rvnd import workspace_cascade as CC


# ── is_air_gapped: verified read, fail-closed on unverifiable ────────────────

def _write_raw(folder, obj):
    (folder / POLICY_FILENAME).write_text(json.dumps(obj), encoding="utf-8")


def test_no_policy_is_not_air_gapped(tmp_path):
    assert is_air_gapped(tmp_path) is False          # opt-in: absent → cloud legal


def test_local_only_is_air_gapped(tmp_path):
    save_policy(tmp_path, FolderPolicy(local_llm=LocalLlmPolicy(mode=LOCAL_LLM_MODE_LOCAL_ONLY)))
    assert is_air_gapped(tmp_path) is True


def test_cloud_allowed_and_fallback_are_not_air_gapped(tmp_path):
    save_policy(tmp_path, FolderPolicy())             # default cloud-allowed
    assert is_air_gapped(tmp_path) is False
    save_policy(tmp_path, FolderPolicy(local_llm=LocalLlmPolicy(mode=LOCAL_LLM_MODE_CLOUD_FALLBACK)))
    assert is_air_gapped(tmp_path) is False


def test_corrupt_json_fails_closed(tmp_path):
    (tmp_path / POLICY_FILENAME).write_text("{not json", encoding="utf-8")
    assert is_air_gapped(tmp_path) is True            # unreadable → fail closed


def test_non_dict_policy_fails_closed(tmp_path):
    _write_raw(tmp_path, ["not", "a", "dict"])
    assert is_air_gapped(tmp_path) is True


def test_malformed_local_llm_block_fails_closed(tmp_path):
    _write_raw(tmp_path, {"local_llm": "local-only"})  # block must be a dict
    assert is_air_gapped(tmp_path) is True


def test_unrecognised_mode_fails_closed(tmp_path):
    _write_raw(tmp_path, {"local_llm": {"mode": "offline-ish"}})
    assert is_air_gapped(tmp_path) is True            # don't trust an unknown token


def test_malformed_unrelated_field_does_not_strand_offline(tmp_path):
    # A garbage *unrelated* field must NOT strand a cloud-allowed folder offline:
    # is_air_gapped reads only local_llm.mode (scoped failure, like verified_cost_cap).
    _write_raw(tmp_path, {"cost_cap_cents": "not-a-number",
                          "local_llm": {"mode": "cloud-allowed"}})
    assert is_air_gapped(tmp_path) is False


def test_non_utf8_policy_fails_closed(tmp_path):
    # A binary / non-UTF-8 policy file raises UnicodeDecodeError (a ValueError,
    # NOT an OSError) on read — it must still fail CLOSED, not crash.
    (tmp_path / POLICY_FILENAME).write_bytes(b"\xff\xfe\x00\x01 not utf-8")
    assert is_air_gapped(tmp_path) is True


# ── legacy policy filename (back-compat path) ────────────────────────────────

def test_legacy_filename_local_only_is_air_gapped(tmp_path):
    # Only the legacy file present, with local-only → air-gapped.
    (tmp_path / LEGACY_POLICY_FILENAME).write_text(
        json.dumps({"local_llm": {"mode": "local-only"}}), encoding="utf-8")
    assert is_air_gapped(tmp_path) is True


def test_legacy_filename_corrupt_fails_closed(tmp_path):
    (tmp_path / LEGACY_POLICY_FILENAME).write_text("{corrupt", encoding="utf-8")
    assert is_air_gapped(tmp_path) is True


def test_modern_filename_takes_precedence_over_legacy(tmp_path):
    # Modern says cloud-allowed, legacy says local-only → modern wins (not air-gapped).
    (tmp_path / POLICY_FILENAME).write_text(
        json.dumps({"local_llm": {"mode": "cloud-allowed"}}), encoding="utf-8")
    (tmp_path / LEGACY_POLICY_FILENAME).write_text(
        json.dumps({"local_llm": {"mode": "local-only"}}), encoding="utf-8")
    assert is_air_gapped(tmp_path) is False


# ── cascade_for_workspace: air-gap drops every cloud rung before the run ──────────

@pytest.fixture
def cloud_only_env(monkeypatch):
    """Configure exactly one (cloud) tier via env; suppress any machine-local
    rung (installer config OR a packaged inproc model under ~/.local-llm) so the
    only rung is the cloud one."""
    monkeypatch.setattr(CC, "_local_config", lambda: {})
    monkeypatch.setattr(CC, "_local_tiers", lambda cfg: [])
    monkeypatch.setenv(CC.CLOUD_URL_ENV, "https://cloud.example/v1")
    monkeypatch.setenv(CC.CLOUD_MODEL_ENV, "big-model")
    monkeypatch.setenv(CC.CLOUD_KEY_ENV, "sk-test")
    monkeypatch.delenv(CC.LOCAL_URL_ENV, raising=False)
    monkeypatch.delenv(CC.LOCAL_MODEL_ENV, raising=False)


def _served_completer(*a, **k):
    return {"ok": True, "response": "served", "usage": {"total_tokens": 3}}


def test_local_only_withholds_cloud_and_refuses(tmp_path, cloud_only_env):
    save_policy(tmp_path, FolderPolicy(local_llm=LocalLlmPolicy(mode=LOCAL_LLM_MODE_LOCAL_ONLY)))
    # A completer that would EXPLODE if ever called — proving the cloud rung is
    # dropped before the run, not merely after.
    def _boom(*a, **k):
        raise AssertionError("cloud completer must never be called when air-gapped")
    out = CC.cascade_for_workspace(tmp_path, "do a thing", completer=_boom)
    assert out["ok"] is False
    assert out["air_gapped"] is True
    assert out["cloud_tiers_withheld"] >= 1
    assert "air-gapped" in out["error"]
    assert out["served_is_cloud"] is False


def test_air_gapped_with_no_tiers_at_all_reports_air_gap(tmp_path, monkeypatch):
    # Air-gapped AND nothing configured (no cloud, no local) → cloud_withheld==0.
    # The result must STILL be the clear air-gap error, not the generic
    # 'no model tier configured' advice (the `if air_gapped and cloud_withheld`
    # bug the panel caught: True and 0 → fell through).
    monkeypatch.setattr(CC, "_local_config", lambda: {})
    monkeypatch.setattr(CC, "_local_tiers", lambda cfg: [])
    monkeypatch.delenv(CC.CLOUD_URL_ENV, raising=False)
    monkeypatch.delenv(CC.CLOUD_MODEL_ENV, raising=False)
    monkeypatch.delenv(CC.CLOUD_KEY_ENV, raising=False)
    monkeypatch.delenv(CC.LOCAL_URL_ENV, raising=False)
    monkeypatch.delenv(CC.LOCAL_MODEL_ENV, raising=False)
    save_policy(tmp_path, FolderPolicy(local_llm=LocalLlmPolicy(mode=LOCAL_LLM_MODE_LOCAL_ONLY)))
    out = CC.cascade_for_workspace(tmp_path, "x")
    assert out["ok"] is False
    assert out["air_gapped"] is True
    assert out["cloud_tiers_withheld"] == 0
    assert "air-gapped" in out["error"]
    assert "no model tier configured" not in out["error"]
    assert out["served_is_cloud"] is False


def test_cloud_allowed_keeps_cloud_rung(tmp_path, cloud_only_env):
    save_policy(tmp_path, FolderPolicy())             # cloud-allowed
    out = CC.cascade_for_workspace(
        tmp_path,
        "do a thing",
        completer=_served_completer,
        capability_token="RVSC1.test.signature",
        track_id="cloud-primary",
    )
    assert out["air_gapped"] is False
    assert out["cloud_tiers_withheld"] == 0
    assert out["ok"] is True
    assert out["served_is_cloud"] is True


def test_unverifiable_policy_withholds_cloud(tmp_path, cloud_only_env):
    # present-but-corrupt policy → fail-closed → cloud withheld even though the
    # operator never declared local-only.
    (tmp_path / POLICY_FILENAME).write_text("{corrupt", encoding="utf-8")
    def _boom(*a, **k):
        raise AssertionError("cloud completer must never be called when air-gapped")
    out = CC.cascade_for_workspace(tmp_path, "x", completer=_boom)
    assert out["air_gapped"] is True
    assert out["ok"] is False
