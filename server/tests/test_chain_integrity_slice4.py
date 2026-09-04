# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Chain integrity: signature-strip detection; cycle check fails closed."""
from __future__ import annotations

import json
import pytest

from rvnd.mutation_log import LogEvent, MutationLog, STRICT_KEY_PINNING_ENV
from rvnd.loomground_lang import _has_cycle


@pytest.fixture
def isolated_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd import signing
    signing.ensure_keypair()


def _chain(tmp_path, n=5):
    log = MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")
    for i in range(n):
        log.append(LogEvent(event="ingest", folder_path=str(tmp_path / "ws"),
                            pair_id=f"sha256:pair-{i}", actor="t", extra={"i": i}))
    return log


# ── D5: stripping a signature after the signing epoch is detected ───────────
def test_signature_strip_after_epoch_is_detected(tmp_path, isolated_keys):
    log = _chain(tmp_path, n=5)
    assert log.verify_chain().ok                      # baseline clean

    lf = log.log_file
    lines = lf.read_text().splitlines()
    obj = json.loads(lines[3])
    obj["signature"] = ""                              # attacker strips the sig
    lines[3] = json.dumps(obj)
    lf.write_text("\n".join(lines) + "\n")

    res = log.verify_chain()
    assert res.ok is False
    assert any(f["reason"] == "unsigned_event_after_signing_epoch"
               for f in res.signature_failures), res.signature_failures


def test_fully_signed_chain_still_verifies(tmp_path, isolated_keys):
    assert _chain(tmp_path, n=6).verify_chain().ok    # non-regression


# ── E2: cycle check is iterative — no RecursionError on a deep chain ────────
def test_has_cycle_deep_linear_no_recursionerror():
    edges = [(f"n{i}", f"n{i+1}") for i in range(5000)]   # far past sys recursion limit
    assert _has_cycle(edges) is False


def test_has_cycle_detects_cycle():
    assert _has_cycle([("a", "b"), ("b", "c"), ("c", "a")]) is True
    assert _has_cycle([("a", "b"), ("b", "c")]) is False
    assert _has_cycle([("a", "a")]) is True              # self-loop


# ── D5 false-positive guard: purge re-link re-signing is MANDATORY ──────────
def test_purge_aborts_if_resign_fails_and_log_is_intact(tmp_path, isolated_keys, monkeypatch):
    """If a re-linked survivor can't be re-signed, purge aborts (no silently
    unsigned survivor — which D5 would otherwise flag as tamper). Log unchanged."""
    log = _chain(tmp_path, n=5)
    assert log.verify_chain().ok
    import rvnd.signing as signing
    def _boom(*a, **k):
        raise RuntimeError("signing unavailable")
    monkeypatch.setattr(signing, "sign_bytes", _boom)
    with pytest.raises(RuntimeError, match="purge aborted"):
        log.purge("sha256:pair-1", legal_basis="art_17_1_a",
                  requester_ref="dsr-42", reason="erasure request")
    # NB: don't monkeypatch.undo() — it shares the instance with isolated_keys
    # and would revert WORKSPACE_KEY_DIR. verify_chain uses verify_signature (not the
    # mocked sign_bytes), so leaving the mock in place is fine.
    # original log untouched → still fully signed + verifies (no false-positive)
    assert log.verify_chain().ok


def test_purge_success_keeps_survivors_signed(tmp_path, isolated_keys):
    """A normal purge re-signs re-linked survivors → chain still verifies (no
    unsigned-after-epoch)."""
    log = _chain(tmp_path, n=5)
    log.purge("sha256:pair-1", legal_basis="art_17_1_a",
              requester_ref="dsr-42", reason="erasure request")
    assert log.verify_chain().ok


@pytest.mark.xfail(reason="D5 alone can't distinguish an all-signatures-stripped "
                          "chain from a legit pre-signing legacy log; the queued "
                          "D6 signed head/length anchor closes this.", strict=True)
def test_all_signatures_stripped_is_detected(tmp_path, isolated_keys):
    log = _chain(tmp_path, n=5)
    lf = log.log_file
    lines = []
    for ln in lf.read_text().splitlines():
        o = json.loads(ln); o["signature"] = ""; lines.append(json.dumps(o))
    lf.write_text("\n".join(lines) + "\n")
    assert log.verify_chain().ok is False


# ── D6: signed head anchor detects tail-truncation ───────────────────────────

def _make_log_with_events(tmp_path, n=3):
    log = MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")
    for i in range(n):
        log.append(LogEvent(event="ingest", folder_path=str(tmp_path / "ws"),
                            pair_id=f"p{i}", actor="tester"))
    return log


def test_d6_clean_chain_with_anchor_verifies(tmp_path, isolated_keys):
    log = _make_log_with_events(tmp_path, 3)
    assert log.verify_chain().ok
    assert (log.log_dir / "events.anchor").exists()


def test_d6_tail_truncation_is_detected(tmp_path, isolated_keys):
    log = _make_log_with_events(tmp_path, 3)
    assert log.verify_chain().ok
    # Drop the last signed event — the prev_hash links of the remainder still
    # validate, but the anchored head is now gone.
    p = log.log_file
    lines = p.read_text("utf-8").splitlines()
    p.write_text("\n".join(lines[:-1]) + "\n", "utf-8")
    res = log.verify_chain()
    assert not res.ok
    assert any("truncation" in (b.get("reason") or "") for b in res.broken_links)


def test_d6_deleted_log_with_surviving_anchor_is_detected(tmp_path, isolated_keys):
    # Removing the whole log is tail-truncation taken to the limit. The anchor is a
    # separate file and survives, so it still commits to a head that is now gone.
    # An absent log must not read as a clean empty history.
    log = _make_log_with_events(tmp_path, 3)
    assert log.verify_chain().ok
    log.log_file.unlink()
    assert (log.log_dir / "events.anchor").exists()
    res = MutationLog(tmp_path / "ws", log_root=tmp_path / "logs").verify_chain()
    assert not res.ok
    assert res.total_events == 0
    assert any("truncation" in (b.get("reason") or "") for b in res.broken_links)


def test_d6_absent_log_without_anchor_still_verifies(tmp_path, isolated_keys):
    # A workspace that never wrote an event has no anchor and no chain to break.
    res = MutationLog(tmp_path / "fresh", log_root=tmp_path / "logs").verify_chain()
    assert res.ok
    assert res.total_events == 0


def test_d6_forged_anchor_signature_is_rejected(tmp_path, isolated_keys):
    log = _make_log_with_events(tmp_path, 2)
    anchor = log.log_dir / "events.anchor"
    import json as _json
    data = _json.loads(anchor.read_text("utf-8"))
    data["signature"] = "00" * 32                 # not a valid Ed25519 sig over the head
    anchor.write_text(_json.dumps(data), "utf-8")
    res = log.verify_chain()
    assert not res.ok
    assert any("anchor" in (b.get("reason") or "") for b in res.broken_links)


# ── E2 (punch-list): an append-time signing failure is no longer swallowed ───
def test_append_signing_failure_with_key_present_is_logged(
        tmp_path, isolated_keys, monkeypatch, caplog):
    """A signing failure on append, when a signing key IS present, is surfaced
    (it was previously swallowed silently): the event is still written unsigned
    (legacy-compatible), but a WARNING names the degraded tamper-evidence."""
    import logging
    import rvnd.signing as signing
    log = MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")

    def _boom(*a, **k):
        raise RuntimeError("signing key unreadable")
    monkeypatch.setattr(signing, "sign_bytes", _boom)

    with caplog.at_level(logging.WARNING, logger="rvnd.mutation_log"):
        log.append(LogEvent(event="ingest", folder_path=str(tmp_path / "ws"),
                            pair_id="sha256:pair-x", actor="t", extra={}))

    # event still written, unsigned (legacy-compatible)
    assert json.loads(log.log_file.read_text().splitlines()[-1])["signature"] == ""
    # but NOT silent — a warning surfaced the degraded tamper-evidence
    assert any("signing FAILED" in r.getMessage() and "UNSIGNED" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


# ── E3-B (punch-list): posture warning when tamper-evidence isn't fail-closed ─
def test_strict_pin_off_with_key_present_warns_once(
        tmp_path, isolated_keys, monkeypatch, caplog):
    """A signing key present but strict pinning OFF → constructing a log emits a
    one-time posture warning that tamper-evidence is not fail-closed."""
    import logging
    monkeypatch.delenv(STRICT_KEY_PINNING_ENV, raising=False)
    monkeypatch.setattr("rvnd.mutation_log._STRICT_PIN_POSTURE_CHECKED", [])
    with caplog.at_level(logging.WARNING, logger="rvnd.mutation_log"):
        MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")
    assert any(STRICT_KEY_PINNING_ENV in r.getMessage()
               and "fail-closed" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


def test_strict_pin_on_is_silent(tmp_path, isolated_keys, monkeypatch, caplog):
    """Strict pinning ON → the posture warning stays silent (floor is enforced)."""
    import logging
    monkeypatch.setenv(STRICT_KEY_PINNING_ENV, "1")
    monkeypatch.setattr("rvnd.mutation_log._STRICT_PIN_POSTURE_CHECKED", [])
    with caplog.at_level(logging.WARNING, logger="rvnd.mutation_log"):
        MutationLog(tmp_path / "ws", log_root=tmp_path / "logs")
    assert not any("fail-closed" in r.getMessage() for r in caplog.records)
