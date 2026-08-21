# SPDX-License-Identifier: AGPL-3.0-only
"""A certificate may not be minted from a marker anyone could have written.

`GovernanceCertification` asserts `blocked_unless_permitted: true` and its module
calls that the load-bearing pillar: possessing a valid certificate is meant to be
evidence the governance happened, not a claim that it did.

That rested on an unsigned JSON file at a predictable path. Anyone who could
write `<log_root>/hook-pending/` could cause a validly signed, offline-verifiable
certificate to be minted for an action that was never held — unforgeable and
untrue at the same time, which is the worst combination an attestation can have.
"""
from __future__ import annotations

import json

import pytest

from rvnd import hook


@pytest.fixture
def pending(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    return tmp_path


def _held(tuid="tu-1", **over):
    evt = {"tool_use_id": tuid, "tool_name": "Bash", "cwd": "/ws",
           "tool_input": {"command": "rm -rf /"}}
    evt.update(over)
    return evt


class _Decision:
    reason = "held for human approval"
    detail = {"action_class": "shell.exec", "audit_id": "evt-1", "evidence": [],
              "grounded": True, "traffic_light": "red"}


def test_the_marker_is_signed_when_written(pending):
    hook._mark_held(_held(), _Decision())
    env = json.loads(hook._marker_path("tu-1").read_text(encoding="utf-8"))
    assert env["signature"], "the hold marker was written without a signature"
    assert env["tool_use_id"] == "tu-1"
    from rvnd.signing import verify_signature
    assert verify_signature(
        hook._marker_signed_bytes(env["marker"], "tu-1"), env["signature"])


def test_a_forged_marker_mints_nothing(pending, capsys, monkeypatch):
    """The attack: write a marker for an action that was never held."""
    forged = {"tool_use_id": "tu-forged",
              "marker": {"action_class": "shell.exec", "audit_id": "made-up",
                         "folder": "/ws", "evidence": []},
              "signature": "00" * 64}
    hook._marker_path("tu-forged").write_text(json.dumps(forged), encoding="utf-8")

    minted = []
    import rvnd.governance_cert as gc
    monkeypatch.setattr(gc, "emit_governance_certification",
                        lambda *a, **k: minted.append(k) or {"envelope": True})

    hook._run_posttooluse(_held(tuid="tu-forged"))
    assert not minted, (
        "a certificate was minted from a marker this installation never signed — "
        "it would verify cryptographically and assert an enforcement that never "
        "happened")
    assert "refusing to certify" in capsys.readouterr().err


def test_an_unsigned_legacy_marker_mints_nothing(pending, capsys, monkeypatch):
    """Markers written before signing existed are flat dicts with no signature.
    Fail closed: a hold that cannot be shown to have happened is not certified."""
    hook._marker_path("tu-old").write_text(
        json.dumps({"action_class": "shell.exec", "folder": "/ws"}), encoding="utf-8")
    minted = []
    import rvnd.governance_cert as gc
    monkeypatch.setattr(gc, "emit_governance_certification",
                        lambda *a, **k: minted.append(k))
    hook._run_posttooluse(_held(tuid="tu-old"))
    assert not minted


def test_a_marker_cannot_be_replayed_against_another_action(pending, monkeypatch):
    """The signature covers the tool_use_id, so moving a genuine marker to a
    different action breaks it — otherwise one real hold could certify many."""
    hook._mark_held(_held(tuid="tu-real"), _Decision())
    env = json.loads(hook._marker_path("tu-real").read_text(encoding="utf-8"))
    env["tool_use_id"] = "tu-other"
    hook._marker_path("tu-other").write_text(json.dumps(env), encoding="utf-8")

    minted = []
    import rvnd.governance_cert as gc
    monkeypatch.setattr(gc, "emit_governance_certification",
                        lambda *a, **k: minted.append(k))
    hook._run_posttooluse(_held(tuid="tu-other"))
    assert not minted, "a genuine marker certified a different action"


def test_a_genuine_hold_still_mints(pending, monkeypatch):
    """The gate must not be so tight that nothing passes it."""
    hook._mark_held(_held(tuid="tu-ok"), _Decision())
    minted = []
    import rvnd.governance_cert as gc
    monkeypatch.setattr(gc, "emit_governance_certification",
                        lambda *a, **k: minted.append(k) or {"ok": True})
    hook._run_posttooluse(_held(tuid="tu-ok"))
    assert minted, "a genuinely signed hold marker failed to mint a certificate"
