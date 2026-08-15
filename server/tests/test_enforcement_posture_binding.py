# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P0a — effective-posture attestation.

The posture read is the LIVE post-``setdefault`` value (never laundered to the
configured intent); each attestation is a signed, offline-re-verifiable chain
event carrying its algorithm; and wiring it into ``operate`` leaves every verdict
untouched — evidence, not control.
"""
from __future__ import annotations

from workspaces import enforcement_posture_binding as B
from workspaces.mutation_log import MutationLog
from workspaces.operations import operate
from workspaces.use_case import register_use_case


def _controls(posture):
    return {c.name: c for c in posture.controls}


# ---- anti-laundering: the posture is the live runtime value ------------------

def test_folder_allowlist_reads_post_setdefault_not_intent(monkeypatch):
    # hook.py/CLI force WORKSPACES_ALLOW_UNREGISTERED=1 at runtime; attesting that
    # path must report folder_allowlist DISABLED, not the configured intent — else
    # the attestation launders the very gap it exists to expose.
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    assert _controls(B.effective_posture())["folder_allowlist"].enabled is False
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "0")
    assert _controls(B.effective_posture())["folder_allowlist"].enabled is True


def test_all_controls_track_their_switches(monkeypatch):
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "0")
    monkeypatch.setenv("WORKSPACE_STRICT_HOST_DIVERGENCE", "1")
    monkeypatch.setenv("RVND_REQUIRE_VERIFIED_EGRESS", "1")
    monkeypatch.setenv("WORKSPACE_STRICT_KEY_PINNING", "1")
    monkeypatch.setenv("RVND_HOOK_MODE", "enforce")
    monkeypatch.setenv("RVND_EGRESS_POLICY", "on")
    monkeypatch.setenv("RVND_AUTONOMY_GRADE", "L1")
    c = _controls(B.effective_posture())
    assert c.keys() >= {"folder_allowlist", "host_divergence", "verified_egress",
                        "key_pinning", "hook_enforce", "egress_policy", "autonomy_ceiling"}
    assert all(c[n].enabled for n in ("folder_allowlist", "host_divergence",
               "verified_egress", "key_pinning", "hook_enforce", "egress_policy"))
    assert c["autonomy_ceiling"].mode == "L1"


# ---- attest: signed, chain-verified, offline-re-verifiable, idempotent -------

def test_attest_writes_a_verified_offline_reverifiable_event(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    B._ATTESTED_THIS_PROCESS.clear()
    d, lr = str(tmp_path / "ws"), str(tmp_path / "logs")

    assert B.attest_posture(d, log_root=lr)                  # appended
    assert B.attest_posture(d, log_root=lr) is None          # idempotent, unchanged

    log = MutationLog(d, log_root=lr)
    assert log.verify_chain().ok
    evs = [e for e in log.replay() if (e.extra or {}).get("kind") == "posture-attested"]
    assert len(evs) == 1
    rep = B.verify_attestation(evs[0].extra["envelope"])
    assert rep.ok and rep.algorithm == "ed25519" and rep.algorithm_stated is True


def test_a_changed_posture_appends_a_fresh_attestation(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("RVND_REQUIRE_VERIFIED_EGRESS", "0")
    B._ATTESTED_THIS_PROCESS.clear()
    d, lr = str(tmp_path / "ws"), str(tmp_path / "logs")

    assert B.attest_posture(d, log_root=lr)
    monkeypatch.setenv("RVND_REQUIRE_VERIFIED_EGRESS", "1")   # posture hardened
    assert B.attest_posture(d, log_root=lr)                   # a fresh attestation

    log = MutationLog(d, log_root=lr)
    evs = [e for e in log.replay() if (e.extra or {}).get("kind") == "posture-attested"]
    assert len(evs) == 2 and log.verify_chain().ok


# ---- verdict-neutral: operate attests without changing its verdict -----------

def test_operate_attests_posture_without_changing_its_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    B._ATTESTED_THIS_PROCESS.clear()
    ws = tmp_path / "org"
    ws.mkdir()
    lr = str(tmp_path / "logs")
    register_use_case(
        str(ws), use_case_id="uc1", name="uc1",
        fingerprint={"issue_type": "liability_cap", "profile": "legal-de", "rooms": ["§ 309"]},
        risk="low", allowed_agents=["bot-7"], actor="alex",
        prior_approvals=20, override_window_seconds=120, log_root=lr,
    )
    run = operate(
        str(ws), use_case_id="uc1", agent_id="bot-7",
        issues=[{"issue_id": "i1", "issue_type": "formatting_fix", "completeness": "high"}],
        now_epoch=1000, log_root=lr,
    )
    # verdict unchanged: a confident node at high autonomy still runs auto, exactly
    # as without the wiring; the run record carries no posture field.
    assert run["steps"][0]["disposition"] == "auto"
    assert "posture" not in run
    # ...but the effective posture now rides on the folder's chain as side-evidence.
    log = MutationLog(str(ws), log_root=lr)
    kinds = {(e.extra or {}).get("kind") for e in log.replay()}
    assert "posture-attested" in kinds and log.verify_chain().ok


def test_enforcement_posture_is_immutably_commit_pinned():
    # The evidence layer is consumed as an immutable git dependency — a full 40-char
    # commit SHA, never a floating branch — the same discipline as the loomground
    # planes (test_upstream_consumption covers those).
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    lines = [ln for ln in (root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
             if "git+https://github.com/flxk1/enforcement-posture@" in ln]
    assert len(lines) == 1
    rev = lines[0].rsplit("@", 1)[-1].split('"', 1)[0]
    assert len(rev) == 40 and all(c in "0123456789abcdef" for c in rev)
