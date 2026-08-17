# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P0b Layer 1a — the effect ledger.

RVND's chain has always recorded what was *decided* (gate verdicts, run
dispositions) but never what actually *happened*. A finished run left no
counter-entry, so an evidence pack's "N governed actions" counted permissions
granted, not effects observed — the two could never be reconciled.

Layer 1a adds the second entry: when a run reaches a terminal outcome
(``mark_run_done`` / ``mark_run_failed``) the queue journals an
``effect-observed`` event onto the run folder's signed chain, cross-referenced
to the authorising decision by ``run_id``. It is a *witness*: best-effort,
verdict-neutral, and it never breaks the run it records.
"""
from __future__ import annotations

from pathlib import Path

from workspaces.mutation_log import MutationLog
from workspaces.queue import (
    cancel_run,
    enqueue_run,
    get_run,
    mark_done,
    mark_run_done,
    mark_run_failed,
    take_next_run,
)


def _effects(folder: Path, log_root: Path) -> list:
    """Every ``effect-observed`` event on the folder's chain, in append order."""
    return [e for e in MutationLog(folder, log_root=log_root).replay()
            if e.extra.get("kind") == "effect-observed"]


def _lease(folder: Path, log_root: Path, wf: str = "wf-1"):
    enqueue_run(folder, wf, log_root=log_root)
    return take_next_run("worker-A", log_root=log_root)


def test_mark_run_done_journals_effect_observed(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    entry = _lease(folder, log_root)

    assert mark_run_done(entry.run_id, "worker-A", log_root=log_root)

    effects = _effects(folder, log_root)
    assert len(effects) == 1
    ev = effects[0]
    assert ev.extra["outcome"] == "done"
    assert ev.extra["run_id"] == entry.run_id          # reconciliation key
    assert ev.extra["workflow"] == "wf-1"
    assert ev.pair_id == f"run:{entry.run_id}"
    assert ev.actor == "system:effect"


def test_mark_run_failed_journals_the_error(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    entry = _lease(folder, log_root)

    assert mark_run_failed(entry.run_id, "worker-A", "boom", log_root=log_root)

    effects = _effects(folder, log_root)
    assert len(effects) == 1
    assert effects[0].extra["outcome"] == "failed"
    assert effects[0].extra["error"] == "boom"


def test_effect_event_is_on_the_signed_chain(tmp_path, monkeypatch):
    # Not a sidecar — the effect rides the same append-only, Ed25519-signed chain
    # as the decision it answers to, so verify_chain covers it.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    entry = _lease(folder, log_root)
    mark_run_done(entry.run_id, "worker-A", log_root=log_root)

    result = MutationLog(folder, log_root=log_root).verify_chain()
    assert result.ok, (result.broken_links, result.signature_failures)
    ev = _effects(folder, log_root)[0]
    assert ev.signature, "effect event must be signed like every chain entry"


def test_finalise_contract_is_unchanged(tmp_path, monkeypatch):
    # Verdict/behaviour-neutral: the queue transition still happens exactly as
    # before — the witness is additive, never a gate.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    entry = _lease(folder, log_root)
    assert mark_run_done(entry.run_id, "worker-A", log_root=log_root) is True
    assert get_run(entry.run_id, log_root=log_root).state == "done"


def test_journalling_failure_never_breaks_the_run(tmp_path, monkeypatch):
    # The witness is best-effort: if the chain append raises, the run still
    # finalises cleanly. A broken witness must not become a broken run.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    entry = _lease(folder, log_root)

    def _boom(*a, **k):
        raise RuntimeError("chain unavailable")

    monkeypatch.setattr("workspaces.mutation_log.MutationLog", _boom)
    assert mark_run_done(entry.run_id, "worker-A", log_root=log_root) is True
    assert get_run(entry.run_id, log_root=log_root).state == "done"


def test_cancel_is_not_an_effect(tmp_path, monkeypatch):
    # A cancel is a queue-state transition, not a world-effect — nothing ran, so
    # nothing is witnessed on the chain.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    entry = _lease(folder, log_root)
    assert cancel_run(entry.run_id, log_root=log_root)
    assert _effects(folder, log_root) == []


def test_noop_finalise_journals_nothing(tmp_path, monkeypatch):
    # Finalising an already-terminal run is a no-op that returns False; it must
    # not mint a second, duplicate effect for the same run. (Uses the lease-less
    # mark_done so the second call reaches _finalise instead of tripping the
    # lease check on the already-dropped lease.)
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    entry = _lease(folder, log_root)
    assert mark_done(entry.run_id, log_root=log_root) is True
    assert mark_done(entry.run_id, log_root=log_root) is False
    assert len(_effects(folder, log_root)) == 1


def test_evidence_pack_lifts_the_effect_detail(tmp_path, monkeypatch):
    # Legibility: the pack must show the OUTCOME, not just the kind label — a
    # reviewer reads done/failed + the run cross-reference line by line, without
    # re-opening the raw chain event.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    log_root = tmp_path
    folder = tmp_path / "folder"
    folder.mkdir()
    entry = _lease(folder, log_root)
    mark_run_failed(entry.run_id, "worker-A", "disk full", log_root=log_root)

    from workspaces import conformity
    pack = conformity.evidence_pack(folder, log_root=log_root)
    assert pack["counts_by_kind"].get("effect-observed") == 1
    rec = next(r for r in pack["records"] if r["record_kind"] == "effect-observed")
    assert rec["detail"]["outcome"] == "failed"
    assert rec["detail"]["run_id"] == entry.run_id
    assert rec["detail"]["workflow"] == "wf-1"
    assert rec["detail"]["error"] == "disk full"
    assert rec["pair_id"] == f"run:{entry.run_id}"
