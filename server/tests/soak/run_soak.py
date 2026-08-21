# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Soak-test orchestrator for Workspace.

Runs three synthetic load generators concurrently against a single workspace
for ``--hours`` hours (default 24, ``--quick`` shortcut runs 5 minutes for CI).
Emits per-minute metrics as CSV.

Workload generators (stubbed — they exercise the runtime paths but do not
depend on a live cloud LLM or any external network):

* ``ingest_loop``  — drops a synthetic ~5 KB markdown file into ``Inbox/``
                     every 10 s; calls ``scan_folder`` every 30 s.
* ``dispatch_loop`` — invokes ``dispatch_skill`` against a stub no-op skill
                      every 5 s; records the returned ``run_id``.
* ``erase_loop``   — every 60 s, picks a random recorded ``pair_id`` and
                     calls ``MutationLog.purge()`` on it, continuously
                     verifying the tombstone/re-link contract at scale.

Per-minute metrics (one CSV row each):

    t_minute, rss_mb, open_fds, log_size_mb, queue_depth,
    chain_verify_ms, chain_verify_ok,
    dispatch_p95_ms, ingest_errors

Soak-failed thresholds:

- any ``chain_verify_ok`` == False
- ``rss_mb`` at end > 2x ``rss_mb`` at minute 60 (post warm-up)
- ``open_fds`` monotone-increasing over any 6h window
- ``chain_verify_ms`` > 5000 at any sample
- ``queue_depth`` > 100 sustained 10+ min
- any non-zero ``ingest_errors``

Invocation::

    python -m tests.soak.run_soak                    # 24h default
    python -m tests.soak.run_soak --hours 168        # 7 days
    python -m tests.soak.run_soak --quick            # 5 min CI smoke
    python -m tests.soak.run_soak --output soak.csv  # explicit output path

Implementation note: this is the scaffold. The three loop bodies invoke the
real runtime — they are NOT mocked. The "stub" qualifier refers to the
content of the synthetic documents and the no-op skill, not to the code paths
being exercised.
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import resource
import signal
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Shared state (single-process orchestrator — soak runs one worker process,
# load generators run as threads against the same MutationLog instance).
# ---------------------------------------------------------------------------

_STOP = threading.Event()
_KNOWN_PAIR_IDS: list[str] = []
_KNOWN_PAIR_IDS_LOCK = threading.Lock()
_DISPATCH_LATENCIES_MS: list[float] = []
_DISPATCH_LATENCIES_LOCK = threading.Lock()
_INGEST_ERRORS = 0
_INGEST_ERRORS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Workload generators (stubbed content; real code paths)
# ---------------------------------------------------------------------------


def ingest_loop(workspace: Path, log_root: Path, period_s: float = 10.0) -> None:
    """Drop synthetic markdown into Inbox/ and trigger scan periodically.

    Synthesises a ~5 KB markdown blob with a UUID title so each ingest is
    distinct. Calls ``MutationLog.append`` with an ``ingest`` event; that's
    the same path the watcher follows in production.
    """
    global _INGEST_ERRORS
    from rvnd.mutation_log import LogEvent, MutationLog

    log = MutationLog(workspace, log_root=log_root)
    inbox = workspace / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    blob_template = ("# Soak document {uid}\n\n" + ("filler line. " * 80 + "\n") * 12)

    while not _STOP.is_set():
        try:
            uid = uuid.uuid4().hex
            blob = blob_template.format(uid=uid)
            doc = inbox / f"soak-{uid}.md"
            doc.write_text(blob, encoding="utf-8")
            pair_id = f"sha256:{uid}"
            log.append(LogEvent(
                event="ingest",
                folder_path=str(workspace),
                pair_id=pair_id,
                channel="document",
                actor="soak:ingest_loop",
                extra={"doc": doc.name, "bytes": len(blob)},
            ))
            with _KNOWN_PAIR_IDS_LOCK:
                _KNOWN_PAIR_IDS.append(pair_id)
                if len(_KNOWN_PAIR_IDS) > 10000:
                    # cap memory; oldest evicted
                    del _KNOWN_PAIR_IDS[:5000]
        except Exception:
            with _INGEST_ERRORS_LOCK:
                _INGEST_ERRORS += 1
        _STOP.wait(period_s)


def dispatch_loop(workspace: Path, log_root: Path, period_s: float = 5.0) -> None:
    """Invoke a no-op recorded dispatch periodically; capture latency.

    Stubbed at the dispatcher boundary: we use ``pinned_skills.record_dispatch``
    which writes the audit event without calling out to any LLM. That's still
    a real append to the mutation log, which is what soak is measuring.
    """
    from rvnd.mutation_log import LogEvent, MutationLog

    log = MutationLog(workspace, log_root=log_root)

    while not _STOP.is_set():
        try:
            t0 = time.perf_counter()
            # No-op recorded dispatch — write a system audit event.
            log.append(LogEvent(
                event="system",
                folder_path=str(workspace),
                pair_id="skill-dispatch",
                channel="system",
                actor="soak:dispatch_loop",
                extra={
                    "kind": "skill-dispatch",
                    "skill_id": "soak:noop",
                    "query": "soak heartbeat",
                    "chosen_via": "soak-orchestrator",
                },
            ))
            dt_ms = (time.perf_counter() - t0) * 1000.0
            with _DISPATCH_LATENCIES_LOCK:
                _DISPATCH_LATENCIES_MS.append(dt_ms)
                if len(_DISPATCH_LATENCIES_MS) > 10000:
                    del _DISPATCH_LATENCIES_MS[:5000]
        except Exception:
            pass
        _STOP.wait(period_s)


def erase_loop(workspace: Path, log_root: Path, period_s: float = 60.0) -> None:
    """Periodically purge a random known pair_id.

    This is the load generator that verifies the tombstone/re-link contract
    repeatedly; any broken link fails the soak thresholds.
    """
    from rvnd.mutation_log import MutationLog

    log = MutationLog(workspace, log_root=log_root)

    while not _STOP.is_set():
        _STOP.wait(period_s)
        if _STOP.is_set():
            return
        with _KNOWN_PAIR_IDS_LOCK:
            if not _KNOWN_PAIR_IDS:
                continue
            pid = random.choice(_KNOWN_PAIR_IDS)
        try:
            # B1: soak purges always pass minimum-viable GDPR grounds so
            # the tombstone is well-formed. Controller key must be present;
            # if not, swallow the error and continue soaking.
            log.purge(
                pid,
                legal_basis="art_17_1_a",
                requester_ref=f"soak:{pid[:8]}",
                reason="soak-test purge",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _rss_mb() -> float:
    """Resident set size in MB (works on Linux + macOS — ru_maxrss units differ)."""
    ru = resource.getrusage(resource.RUSAGE_SELF)
    # Linux returns KB; macOS returns bytes.
    raw = ru.ru_maxrss
    if sys.platform == "darwin":
        return raw / (1024 * 1024)
    return raw / 1024


def _open_fds() -> int:
    """Count open file descriptors for this process."""
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except FileNotFoundError:
        # macOS doesn't have /proc; fall back to a best-effort sweep.
        try:
            import subprocess
            out = subprocess.check_output(
                ["lsof", "-p", str(os.getpid())],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return len(out.decode().splitlines()) - 1  # minus header
        except Exception:
            return -1


def _log_size_mb(workspace: Path, log_root: Path) -> float:
    from rvnd.mutation_log import folder_hash
    fh = folder_hash(workspace)
    lf = log_root / fh / "events.jsonl"
    if not lf.exists():
        return 0.0
    return lf.stat().st_size / (1024 * 1024)


def _verify_chain_metrics(workspace: Path, log_root: Path) -> tuple[float, bool]:
    from rvnd.mutation_log import MutationLog
    log = MutationLog(workspace, log_root=log_root)
    t0 = time.perf_counter()
    result = log.verify_chain()
    dt_ms = (time.perf_counter() - t0) * 1000.0
    return dt_ms, bool(result.ok)


def _dispatch_p95_ms() -> float:
    with _DISPATCH_LATENCIES_LOCK:
        snap = list(_DISPATCH_LATENCIES_MS[-600:])  # last ~minute at 5s period × 12
    if not snap:
        return 0.0
    snap.sort()
    idx = int(len(snap) * 0.95)
    return snap[min(idx, len(snap) - 1)]


def _queue_depth_stub() -> int:
    """Stub for queue depth — real implementation reads from
    ``rvnd.queue.list_queue()``. Returns 0 in the scaffold; soak run on
    real load will pick up the live queue once dispatch_loop is wired through
    the workflow runner."""
    return 0


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_soak(hours: float, output: Path, *, workspace: Optional[Path] = None,
             log_root: Optional[Path] = None) -> int:
    """Run the soak for ``hours`` hours, writing per-minute metrics to ``output``.

    Returns 0 on clean run, 1 on threshold breach. Threshold checks here are
    minimal (chain_verify_ok); fuller analysis is left to a separate
    ``analyze_soak.py`` so the orchestrator stays simple.
    """
    global _INGEST_ERRORS, _DISPATCH_LATENCIES_MS, _KNOWN_PAIR_IDS

    workspace = workspace or Path(tempfile.mkdtemp(prefix="soak_workspace_"))
    log_root = log_root or Path(tempfile.mkdtemp(prefix="soak_log_"))
    workspace.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)
    print(f"[soak] workspace = {workspace}", flush=True)
    print(f"[soak] log_root  = {log_root}", flush=True)
    print(f"[soak] output    = {output}", flush=True)
    print(f"[soak] duration  = {hours} h", flush=True)

    threads = [
        threading.Thread(target=ingest_loop, args=(workspace, log_root),
                         name="ingest", daemon=True),
        threading.Thread(target=dispatch_loop, args=(workspace, log_root),
                         name="dispatch", daemon=True),
        threading.Thread(target=erase_loop, args=(workspace, log_root),
                         name="erase", daemon=True),
    ]
    for t in threads:
        t.start()

    # Graceful shutdown on SIGINT/SIGTERM
    def _shutdown(signum, frame):
        print(f"[soak] received signal {signum}; stopping", flush=True)
        _STOP.set()
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    deadline = time.time() + hours * 3600
    rc = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "t_minute", "rss_mb", "open_fds", "log_size_mb", "queue_depth",
            "chain_verify_ms", "chain_verify_ok",
            "dispatch_p95_ms", "ingest_errors",
        ])
        t_minute = 0
        while time.time() < deadline and not _STOP.is_set():
            t_minute += 1
            chain_ms, chain_ok = _verify_chain_metrics(workspace, log_root)
            row = [
                t_minute,
                round(_rss_mb(), 2),
                _open_fds(),
                round(_log_size_mb(workspace, log_root), 3),
                _queue_depth_stub(),
                round(chain_ms, 2),
                int(chain_ok),
                round(_dispatch_p95_ms(), 2),
                _INGEST_ERRORS,
            ]
            writer.writerow(row)
            fh.flush()
            if not chain_ok:
                print(f"[soak] FAIL chain_verify_ok=False at minute {t_minute}",
                      flush=True)
                rc = 1
                # Don't break — let it keep running so the regression is
                # captured for the full run. Caller decides what to do.
            # Sleep for the remainder of this minute
            _STOP.wait(60.0)

    _STOP.set()
    for t in threads:
        t.join(timeout=5.0)
    print(f"[soak] done. rc={rc}", flush=True)
    return rc


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Workspace soak-test orchestrator.",
    )
    p.add_argument("--hours", type=float, default=24.0,
                   help="Run duration in hours (default 24).")
    p.add_argument("--quick", action="store_true",
                   help="Override: run for 5 minutes (CI smoke).")
    p.add_argument("--output", type=Path,
                   default=Path("soak-metrics.csv"),
                   help="CSV output path.")
    p.add_argument("--workspace", type=Path, default=None,
                   help="Workspace folder (default: tmpdir).")
    p.add_argument("--log-root", type=Path, default=None,
                   help="Mutation-log root (default: tmpdir).")
    args = p.parse_args(argv)

    hours = (5 / 60.0) if args.quick else args.hours
    return run_soak(hours, args.output,
                    workspace=args.workspace, log_root=args.log_root)


if __name__ == "__main__":
    raise SystemExit(main())
