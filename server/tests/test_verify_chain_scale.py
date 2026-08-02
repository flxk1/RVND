# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-14: does ``verify_chain`` stay usable at production age? (PERF-005/006)

The largest chain the suite exercised before this was ~400 events; every
trust decision walks the whole log (O(n) per check), so the open question was
the constant and the curve at real ages. This builds a 10^5-event signed
chain and pins two things:

  * a wall-clock BUDGET — verification of 100k events completes within a
    bound generous enough for a loaded 2-core CI runner but far below
    "unusable" (the operability claim, measured);
  * the SHAPE — time grows ~linearly from 10k to 100k. A superlinear
    verify (accidental O(n^2) — rescanning, quadratic membership checks)
    blows the ratio guard long before users feel it.

The chain is synthesized through the module's OWN canonicalisation and
signing primitives (``_canonical_event_hash``, ``_signed_bytes``,
``sign_bytes`` with a preloaded key) — identical bytes to what ``append()``
produces, minus append's per-event fsync, which is a durability cost of
writing, not a property of verification, and would turn a seconds-long test
into minutes. ``verify_chain`` itself is the unmodified product path and
must return fully ok (every link and every signature checked).
"""
from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path

import pytest

from workspaces.mutation_log import (GENESIS_HASH, LogEvent, MutationLog,
                                     _canonical_event_hash, _signed_bytes)
from workspaces.signing import ensure_keypair, sign_bytes

pytestmark = [pytest.mark.slow, pytest.mark.timeout(300)]

# Generous for a loaded CI runner (quiet macOS measures ~20s at 100k);
# far below the point where "verify on every check" stops being viable.
VERIFY_BUDGET_100K_S = 90.0
# 10x the events may cost at most ~3x the proportional time. Linear passes
# with margin; accidental O(n^2) fails by an order of magnitude.
MAX_SCALING_RATIO = 30.0


def _build_chain(root: Path, n: int) -> MutationLog:
    ws = root / f"org-{n}"
    ws.mkdir()
    log = MutationLog(ws, log_root=root / "logs")
    priv, _ = ensure_keypair()
    prev = GENESIS_HASH
    lines: list[str] = []
    for i in range(n):
        e = LogEvent(event="ingest", folder_path=log.folder_path,
                     pair_id=f"pair:{i}", channel="document", actor="scale",
                     ts=1_000.0 + i, audit_id=f"a{i}", prev_hash=prev,
                     host_id="scale-host")
        e.signature = sign_bytes(_signed_bytes({**asdict(e), "signature": ""}), priv)
        lines.append(e.to_jsonl())
        prev = _canonical_event_hash(asdict(e))
    log._log_file.parent.mkdir(parents=True, exist_ok=True)
    log._log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log


def _timed_verify(log: MutationLog, n: int) -> float:
    t0 = time.perf_counter()
    r = log.verify_chain()
    dt = time.perf_counter() - t0
    assert r.ok, f"synthesized {n}-event chain must verify clean: {r}"
    assert r.total_events == n
    assert not r.broken_links and not r.signature_failures
    return dt


def test_verify_chain_100k_budget_and_shape(tmp_path):
    t_small = _timed_verify(_build_chain(tmp_path, 10_000), 10_000)
    t_large = _timed_verify(_build_chain(tmp_path, 100_000), 100_000)

    assert t_large < VERIFY_BUDGET_100K_S, (
        f"verify_chain(100k) took {t_large:.1f}s — over the {VERIFY_BUDGET_100K_S:.0f}s "
        "operability budget; verification no longer viable on every trust check")
    # shape guard: 10x events -> at most ~3x-per-event degradation
    ratio = t_large / max(t_small, 0.05)
    assert ratio < MAX_SCALING_RATIO, (
        f"verify_chain scaled {ratio:.1f}x from 10k to 100k events (limit "
        f"{MAX_SCALING_RATIO:.0f}x for 10x the data) — superlinear verification")
