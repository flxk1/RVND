# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Background workflow worker.

The worker is a long-running process that drains the queue built in
``queue.py``. It takes one run at a time, dispatches its steps via the
existing ``run_workflow`` machinery (which already records workflow-event
audit entries to the mutation log), then marks the run done or failed.

Lifecycle:

    while not stopping:
        run = take_next_run(worker_id, lease_seconds)
        if run is None:
            sleep(interval)
            continue
        result = run_workflow(run.folder, run.workflow_name, ...)
        if result.ok:
            mark_done(run.run_id)
        else:
            mark_failed(run.run_id, error=result.final_state_reason)

Properties this gives us:

- **Survives MCP-server restart.** Worker is a separate process; if the
  MCP server crashes, queue + leases persist on disk, worker keeps going.
- **Survives worker crash.** Stale leases auto-revoke when the next worker
  calls ``take_next_run``. Orphan runs land in ``active_workflows`` and
  the dashboard surfaces them.
- **Graceful shutdown.** SIGINT / SIGTERM flips a flag; the loop finishes
  the current run before exiting.
- **No auto-start.** The user runs ``workspaces run-worker``
  themselves. A launchd / systemd installer comes in v2.

This module exposes ``run_forever``, ``run_once``, and ``stop_worker`` for
use from the CLI; tests use ``run_once`` to drive single iterations
without sleep loops.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .queue import (
    QueueEntry,
    DEFAULT_LEASE_SECONDS,
    list_queue,
    mark_done,
    mark_failed,
    renew_lease,
    take_next_run,
)
from .workflows import run_workflow


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


DEFAULT_INTERVAL_SECONDS = 2.0
DEFAULT_RENEW_INTERVAL = 30


@dataclass
class WorkerConfig:
    worker_id: str = ""
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS
    log_root: Optional[Path] = None
    once: bool = False                  # run a single iteration then exit
    max_iterations: int = 0             # 0 = unlimited
    verbose: bool = False

    def __post_init__(self) -> None:
        if not self.worker_id:
            self.worker_id = f"worker-{socket.gethostname()}-{os.getpid()}"


# ---------------------------------------------------------------------------
# Stop signal
# ---------------------------------------------------------------------------


class _StopFlag:
    """Mutable cross-handler flag for graceful shutdown."""
    def __init__(self) -> None:
        self.set = False
        self.reason = ""

    def trip(self, reason: str = "") -> None:
        self.set = True
        self.reason = reason or "user-requested"


_GLOBAL_STOP = _StopFlag()


def stop_worker(reason: str = "stop_worker() called") -> None:
    """Politely ask the running worker to finish its current run and exit.

    Intended for tests + programmatic shutdown. CLI installs SIGINT/SIGTERM
    handlers that call this.
    """
    _GLOBAL_STOP.trip(reason)


def _install_signal_handlers(stop: _StopFlag,
                              log: logging.Logger) -> None:
    def _h(signum: int, frame: object) -> None:
        sig = signal.Signals(signum).name if isinstance(signum, int) else str(signum)
        log.info("received %s — finishing current run then exiting", sig)
        stop.trip(f"signal:{sig}")
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _h)
        except (OSError, ValueError):
            # Non-main-thread or unsupported on this platform — best effort
            pass


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def _process_one_run(entry: QueueEntry,
                     cfg: WorkerConfig,
                     log: logging.Logger) -> tuple[bool, str]:
    """Run one queued workflow to completion. Returns (ok, error).

    *** AUDIT-ONLY MODE (v0.6.x) ***
    The default dispatcher is ``record_dispatch``, which writes a
    ``skill-dispatch`` event to the mutation log and returns ok=True.
    It does NOT actually invoke the skill body. A successful return here
    means "the dispatch was recorded for audit", not "the skill ran".
    Until a real dispatcher is wired (the host LLM, a local-model bridge,
    or a Cell-side runner), this worker is an audit-trail emitter, not a
    skill executor. Callers depending on side-effects must supply their
    own ``dispatcher=`` via ``run_workflow``.
    """
    log.info("run %s — starting workflow %r on %s (AUDIT-ONLY mode)",
              entry.run_id, entry.workflow_name, entry.folder_path)
    try:
        result = run_workflow(
            entry.folder_path,
            entry.workflow_name,
            run_id=entry.run_id,
            actor=cfg.worker_id,
            log_root=cfg.log_root,
        )
    except FileNotFoundError as e:
        msg = f"workflow definition gone: {e}"
        log.error("run %s — %s", entry.run_id, msg)
        return (False, msg)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        log.exception("run %s — runner crashed: %s", entry.run_id, msg)
        return (False, msg)

    ok = bool(result.get("ok"))
    final = result.get("final_state") or ("done" if ok else "failed")
    err = ""
    if not ok:
        # Build a short error summary from the step results
        steps = result.get("steps") or []
        failed = [s for s in steps if s.get("state") == "failed"]
        if failed:
            err = "; ".join(f"step{s['step_index']}({s['skill_id']}): {s.get('error','?')}"
                            for s in failed)
        else:
            err = final
    log.info("run %s — final_state=%s", entry.run_id, final)
    return (ok, err)


def run_once(cfg: Optional[WorkerConfig] = None,
             *,
             stop: Optional[_StopFlag] = None,
             log: Optional[logging.Logger] = None) -> dict:
    """Drain at most one run. Returns a short status dict.

    Always safe to call from tests / scripts. Does NOT install signal
    handlers. ``run_forever`` does.
    """
    cfg = cfg or WorkerConfig()
    stop = stop or _StopFlag()
    log = log or logging.getLogger("workspaces.worker")

    # Do not claim new work after shutdown has been requested. A stop that
    # arrives after this check is treated as occurring during the newly
    # claimed run, which is then completed before the worker exits.
    if stop.set:
        return {
            "ok": True,
            "state": "stopped",
            "reason": stop.reason,
            "iterations": 0,
        }

    entry = take_next_run(
        cfg.worker_id,
        lease_seconds=cfg.lease_seconds,
        log_root=cfg.log_root,
    )
    if entry is None:
        return {"ok": True, "state": "empty", "iterations": 0}

    ok, err = _process_one_run(entry, cfg, log)
    if ok:
        mark_done(entry.run_id, log_root=cfg.log_root)
    else:
        mark_failed(entry.run_id, error=err, log_root=cfg.log_root)

    return {
        "ok":        ok,
        "state":     "done" if ok else "failed",
        "run_id":    entry.run_id,
        "workflow":  entry.workflow_name,
        "folder":    entry.folder_path,
        "error":     err,
        "iterations": 1,
        # AUDIT-ONLY: callers should treat state="done" as "dispatch recorded",
        # not "skill body actually executed". See _process_one_run docstring.
        "dispatcher_mode": "audit_only",
    }


def run_forever(cfg: Optional[WorkerConfig] = None,
                *,
                stop: Optional[_StopFlag] = None,
                log: Optional[logging.Logger] = None) -> dict:
    """Loop forever, draining the queue and sleeping when it's empty.

    Honours ``cfg.max_iterations`` (0 = unlimited) and a passed-in or
    global stop flag. Returns a summary dict on graceful exit.
    """
    cfg = cfg or WorkerConfig()
    stop = stop or _GLOBAL_STOP
    log = log or logging.getLogger("workspaces.worker")
    _install_signal_handlers(stop, log)
    iterations = 0
    runs_done = 0
    runs_failed = 0
    # Local reason so we don't trip the shared global stop just to break out
    local_reason = ""
    log.info("worker %s starting (lease=%ds, interval=%.1fs, log_root=%s)",
              cfg.worker_id, cfg.lease_seconds, cfg.interval_seconds,
              cfg.log_root or "<default>")
    while not stop.set:
        result = run_once(cfg, stop=stop, log=log)
        if result["state"] == "stopped":
            local_reason = result.get("reason") or "stop_requested"
            break
        iterations += 1
        if result["state"] == "done":
            runs_done += 1
        elif result["state"] == "failed":
            runs_failed += 1
        if cfg.max_iterations and iterations >= cfg.max_iterations:
            log.info("max_iterations=%d reached — exiting", cfg.max_iterations)
            local_reason = "max_iterations"
            break
        if result["state"] == "empty":
            # Queue empty — sleep before next poll
            if stop.set:
                break
            time.sleep(max(0.05, cfg.interval_seconds))
    reason = stop.reason or local_reason or "loop_exit"
    log.info("worker %s stopped (reason=%s; done=%d failed=%d iterations=%d)",
              cfg.worker_id, reason, runs_done, runs_failed, iterations)
    return {
        "worker_id":   cfg.worker_id,
        "stopped":     True,
        "reason":      reason,
        "iterations":  iterations,
        "runs_done":   runs_done,
        "runs_failed": runs_failed,
    }


# ---------------------------------------------------------------------------
# Status helper (useful for the CLI's --status flag)
# ---------------------------------------------------------------------------


def worker_status(log_root: Optional[Path] = None) -> dict:
    """Return a one-shot snapshot of the queue + leases the worker would see."""
    pending = list_queue(state_filter="pending", log_root=log_root)
    leased  = list_queue(state_filter="leased",  log_root=log_root)
    return {
        "pending_count": len(pending),
        "leased_count":  len(leased),
        "pending":       [e.to_dict() for e in pending[:10]],
        "leased":        [e.to_dict() for e in leased[:10]],
    }
