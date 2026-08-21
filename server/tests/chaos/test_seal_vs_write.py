# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Chaos C4: sealing a workspace while a writer is appending.

Pre-fix behaviour: ``seal_folder`` snapshotted the log dir, wrote the
blob, then ``rmtree``'d the plaintext — without taking the mutation log's
lock. An append that landed between the snapshot and the rmtree was
destroyed AFTER ``append()`` had already returned its audit_id: accepted
evidence, silently gone. An append past its sealed entry-check could also
die on a raw FileNotFoundError once the dir vanished.

Post-fix contract, pinned here across many seal-vs-write interleavings:

  * no torn end state: afterwards the store is either cleanly sealed
    (blob present, no plaintext dir) with the writer refused via the typed
    ``SealedWriteError``, or the seal lost the timing race it is allowed
    to lose only BEFORE the blob lands;
  * accepted-write durability: every append that returned an audit_id is
    present after unseal — nothing accepted is destroyed;
  * the writer never dies on an untyped error, and never recreates the
    plaintext dir beside the ciphertext (which would brick unseal).
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.security]

_PASSPHRASE = "chaos-c4-pw"


def _writer(workspace_str: str, log_root_str: str, key_dir: str,
            accepted_path: str, refused_flag: str) -> None:
    """Append until sealed-out; record every ACCEPTED audit_id durably."""
    os.environ["WORKSPACE_KEY_DIR"] = key_dir
    from rvnd.mutation_log import LogEvent, MutationLog, SealedWriteError

    workspace = Path(workspace_str)
    log = MutationLog(workspace, log_root=Path(log_root_str))
    with open(accepted_path, "a", encoding="utf-8") as out:
        for i in range(10_000):
            try:
                audit_id = log.append(LogEvent(
                    event="ingest",
                    folder_path=str(workspace),
                    pair_id=f"pair:sealrace:{i:05d}",
                    channel="document",
                    actor="chaos:sealrace:writer",
                    extra={"i": i},
                ))
            except SealedWriteError:
                Path(refused_flag).write_text("typed-refusal")
                return
            # Durably record acceptance BEFORE the next iteration, so the
            # parent can compare accepted ids against what survived.
            out.write(audit_id + "\n")
            out.flush()
            os.fsync(out.fileno())
    raise SystemExit("writer was never sealed out after 10k appends")


def _sealer(workspace_str: str, log_root_str: str, delay_s: float) -> None:
    from rvnd import seal
    time.sleep(delay_s)  # let the writer get going mid-stream
    seal.seal_folder(workspace_str, passphrase=_PASSPHRASE,
                     log_root=log_root_str)


@pytest.mark.parametrize("delay_s", [0.05, 0.2, 0.5])
def test_seal_mid_stream_loses_no_accepted_write(tmp_path: Path,
                                                 monkeypatch,
                                                 delay_s: float) -> None:
    workspace = tmp_path / "sealrace_workspace"
    workspace.mkdir(parents=True)
    log_root = tmp_path / ".workspaces"
    log_root.mkdir(parents=True)
    key_dir = tmp_path / "keys"
    accepted_path = tmp_path / "accepted_ids.txt"
    refused_flag = tmp_path / "refused.flag"
    # Verify below with the SAME identity the writer child signs with —
    # otherwise the head-anchor signature check fails for key-mismatch
    # reasons unrelated to the race under test.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(key_dir))

    ctx = mp.get_context("spawn")
    w = ctx.Process(target=_writer,
                    args=(str(workspace), str(log_root), str(key_dir),
                          str(accepted_path), str(refused_flag)))
    s = ctx.Process(target=_sealer,
                    args=(str(workspace), str(log_root), delay_s))
    w.start()
    s.start()
    s.join(timeout=120)
    w.join(timeout=120)
    assert s.exitcode == 0, f"sealer failed (exit {s.exitcode})"
    assert w.exitcode == 0, (
        f"writer died untyped (exit {w.exitcode}) — the seal race must "
        "surface as SealedWriteError, never a raw OSError")
    assert refused_flag.exists(), "writer finished without a typed refusal"

    # End state: cleanly sealed — blob present, NO plaintext dir beside it
    # (a recreated plaintext dir would brick unseal).
    from rvnd.mutation_log import MutationLog
    from rvnd import seal
    probe = MutationLog(workspace, log_root=log_root)
    blob = log_root / (probe.folder_id + ".sealed")
    plaintext_dir = log_root / probe.folder_id
    assert blob.exists(), "no sealed blob after seal_folder returned"
    assert not plaintext_dir.exists(), (
        "plaintext dir exists beside the sealed blob — unseal is bricked")

    # Durability: every append that RETURNED an audit_id must be inside the
    # blob. Unseal and reconcile.
    seal.unseal_folder(str(workspace), passphrase=_PASSPHRASE,
                       log_root=str(log_root))
    events_file = log_root / probe.folder_id / "events.jsonl"
    on_disk = {
        json.loads(line)["audit_id"]
        for line in events_file.read_text().splitlines() if line.strip()
    }
    accepted = [l for l in accepted_path.read_text().splitlines() if l.strip()]
    lost = [a for a in accepted if a not in on_disk]
    assert not lost, (
        f"{len(lost)} accepted append(s) destroyed by the seal "
        f"(first: {lost[:3]}) — accepted evidence must be durable")

    # And the restored chain still verifies.
    result = MutationLog(workspace, log_root=log_root).verify_chain()
    assert result.ok, f"restored chain not ok: {result.broken_links[:3]}"
