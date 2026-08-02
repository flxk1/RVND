# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Persistent workflow-run queue + lease primitive.

At-least-once semantics (B7.3 / 0.6.8)
======================================

A worker that crashes mid-step does NOT have its lease auto-failed. The
lease simply expires; on the next ``take_next_run`` call another worker
may claim the run and replay the dispatched skill. This means a single
logical "do X" may be observed twice on the chain — the dispatched skill
is responsible for idempotency (we cannot guarantee exactly-once
delivery across crash boundaries without a 2-phase commit, which is
heavier than our threat model justifies).

``mark_run_done(run_id, worker_id=...)`` and
``mark_run_failed(run_id, worker_id=..., error=...)`` enforce
lease-ownership: only the worker currently holding the lease may
finalise the run. A worker whose lease has already expired and been
re-claimed will see :class:`LeaseStolen` raised. The MCP wrappers in
``mcp_server`` accept the worker_id as an explicit argument so the
caller can prove it owns the run before mutating the queue.

The synchronous runner in ``workflows.py`` is appropriate for short
workflows but blocks the MCP server for long ones and can't survive a
crash. This module is the queue + lease substrate that the background
worker will drive.

Design choices:

A. **Shape B** — persistent local queue + worker process. NOT a daemon
   thread inside the MCP server (orphans on every MCP restart); NOT
   Cowork's scheduled-tasks (ties Workspaces to Cowork lifetime).

B. **Resume policy = surface-and-ask**. On worker start, orphans are
   surfaced via ``active_workflows`` / dashboard; user decides resume
   vs mark-failed.

D. **Concurrency v1 = one run per (folder, workflow_name)**. Enqueuing a
   second run while one is already queued or running is rejected with
   ``already_queued`` / ``already_running``.

Storage layout (per-log-root, NOT per-folder — the queue spans the whole
Workspaces install so one worker can drain all folders):

    <log_root>/queue.jsonl                   append-only enqueue events
    <log_root>/queue.state.json              materialised view (latest state
                                             per run_id), rewritten on each
                                             append; cheap to read.
    <log_root>/leases/<run_id>.json          per-run lease (pid + expiry)

A queue entry has these states:

    pending     — enqueued, no worker yet
    leased      — a worker holds the lease and is processing
    done        — terminal success
    failed      — terminal failure
    cancelled   — user cancelled before terminal

State transitions:

    pending --take_next_run()-->     leased
    leased  --mark_done()-->         done
    leased  --mark_failed()-->       failed
    pending/leased --cancel_run()--> cancelled

The queue itself is the source of truth for *which runs are scheduled*.
The mutation log (workflow-event entries) remains the source of truth for
*what happened during the run*. They cross-reference via ``run_id``.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

from .mutation_log import LOG_ROOT_DEFAULT


QUEUE_FILE = "queue.jsonl"
QUEUE_STATE_FILE = "queue.state.json"
LEASES_SUBDIR = "leases"
DEFAULT_LEASE_SECONDS = 60     # workers must renew within this window
LEASE_RENEW_INTERVAL = 30      # caller-side hint: renew every N seconds


class LeaseStolen(RuntimeError):
    """Raised when a worker tries to finalise a run it no longer owns.

    Possible causes:
      - lease expired and another worker claimed the run
      - worker_id mismatch (caller passed the wrong id)
      - lease file vanished (administrator wiped it)

    The mutating call is refused; the caller should treat its work as
    dropped and either re-enqueue or surface the issue."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    # Microsecond precision so two enqueues in rapid succession still sort
    # deterministically. ISO-8601 with microsecond fraction.
    t = time.time()
    secs = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))
    micros = int((t - int(t)) * 1_000_000)
    return f"{secs}.{micros:06d}Z"


def _now_epoch() -> int:
    return int(time.time())


def _new_run_id(folder_path: str, workflow_name: str) -> str:
    seed = f"{folder_path}|{workflow_name}|{time.time_ns()}|{os.getpid()}"
    return "wfrun:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _resolved(folder_path: str | Path) -> str:
    return str(Path(folder_path).expanduser().resolve())


def _queue_path(log_root: Optional[Path] = None) -> Path:
    return (Path(log_root) if log_root else LOG_ROOT_DEFAULT) / QUEUE_FILE


def _state_path(log_root: Optional[Path] = None) -> Path:
    return (Path(log_root) if log_root else LOG_ROOT_DEFAULT) / QUEUE_STATE_FILE


def _leases_dir(log_root: Optional[Path] = None) -> Path:
    return (Path(log_root) if log_root else LOG_ROOT_DEFAULT) / LEASES_SUBDIR


LOCK_FILE = ".queue.lock"


@contextmanager
def _queue_lock(log_root: Optional[Path] = None,
                timeout_s: float = 5.0) -> Iterator[None]:
    """Acquire an exclusive POSIX flock on ``<log_root>/.queue.lock`` for the
    duration of the with-block. Falls back to a no-op when fcntl is missing
    (Windows). Times out after ``timeout_s`` seconds with RuntimeError.

    Wraps every state-mutating section so two workers + the MCP server can
    safely contend for the same queue without losing writes or handing the
    same run to two workers.
    """
    if not _HAVE_FCNTL:
        yield
        return
    root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / LOCK_FILE
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        # The lock currently carries no payload, but its path discloses queue
        # activity and may gain metadata later. Correct legacy/world-readable
        # files as well as creating new ones with an owner-only mode.
        os.fchmod(fd, 0o600)
        deadline = time.time() + max(0.1, float(timeout_s))
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise RuntimeError(
                        f"queue lock at {lock_path} held >{timeout_s}s; "
                        "another worker may be stuck"
                    )
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:
                pass
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


def _lease_path(run_id: str, log_root: Optional[Path] = None) -> Path:
    return _leases_dir(log_root) / f"{run_id.replace(':', '_')}.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class QueueEntry:
    run_id:           str
    folder_path:      str
    workflow_name:    str
    enqueued_at:      str
    enqueued_by:      str = "system"
    state:            str = "pending"   # pending | leased | done | failed | cancelled
    leased_to:        str = ""          # worker_id when state=leased
    last_state_at:    str = ""
    error:            str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "QueueEntry":
        return cls(
            run_id=str(d.get("run_id") or ""),
            folder_path=str(d.get("folder_path") or ""),
            workflow_name=str(d.get("workflow_name") or ""),
            enqueued_at=str(d.get("enqueued_at") or _now_iso()),
            enqueued_by=str(d.get("enqueued_by") or "system"),
            state=str(d.get("state") or "pending"),
            leased_to=str(d.get("leased_to") or ""),
            last_state_at=str(d.get("last_state_at") or ""),
            error=str(d.get("error") or ""),
        )


@dataclass
class Lease:
    run_id:      str
    worker_id:   str
    pid:         int
    host:        str
    acquired_at: int
    expires_at:  int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Lease":
        return cls(
            run_id=str(d.get("run_id") or ""),
            worker_id=str(d.get("worker_id") or ""),
            pid=int(d.get("pid") or 0),
            host=str(d.get("host") or ""),
            acquired_at=int(d.get("acquired_at") or 0),
            expires_at=int(d.get("expires_at") or 0),
        )

    @property
    def is_expired(self) -> bool:
        return _now_epoch() >= self.expires_at


# ---------------------------------------------------------------------------
# State materialisation
# ---------------------------------------------------------------------------


def _replay_queue(log_root: Optional[Path] = None) -> dict[str, QueueEntry]:
    """Read the append-only queue.jsonl and project into the latest state
    per ``run_id``. Used to rebuild ``queue.state.json`` after a crash."""
    qp = _queue_path(log_root)
    if not qp.exists():
        return {}
    out: dict[str, QueueEntry] = {}
    with open(qp, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry = QueueEntry.from_dict(d)
            if not entry.run_id:
                continue
            out[entry.run_id] = entry
    return out


def _load_state(log_root: Optional[Path] = None) -> dict[str, QueueEntry]:
    """Load the materialised state file, falling back to a full replay if
    the state file is missing or unreadable."""
    sp = _state_path(log_root)
    if sp.exists():
        try:
            with open(sp, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {rid: QueueEntry.from_dict(d) for rid, d in raw.items()}
        except (OSError, json.JSONDecodeError):
            pass
    return _replay_queue(log_root)


def _save_state(state: dict[str, QueueEntry],
                log_root: Optional[Path] = None) -> None:
    sp = _state_path(log_root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_suffix(sp.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({rid: e.to_dict() for rid, e in state.items()},
                  f, indent=2, sort_keys=True)
    os.replace(tmp, sp)


def _append_queue_event(entry: QueueEntry,
                        log_root: Optional[Path] = None) -> None:
    qp = _queue_path(log_root)
    qp.parent.mkdir(parents=True, exist_ok=True)
    with open(qp, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Lease ops
# ---------------------------------------------------------------------------


def _write_lease(lease: Lease, log_root: Optional[Path] = None) -> Path:
    p = _lease_path(lease.run_id, log_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lease.to_dict(), f, indent=2, sort_keys=True)
    os.replace(tmp, p)
    return p


def _read_lease(run_id: str,
                log_root: Optional[Path] = None) -> Optional[Lease]:
    p = _lease_path(run_id, log_root)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return Lease.from_dict(json.load(f))
    except (OSError, json.JSONDecodeError):
        return None


def _drop_lease(run_id: str, log_root: Optional[Path] = None) -> bool:
    p = _lease_path(run_id, log_root)
    if not p.exists():
        return False
    p.unlink()
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enqueue_run(folder_path: str | Path,
                workflow_name: str,
                *,
                enqueued_by: str = "system",
                log_root: Optional[Path] = None) -> QueueEntry:
    """Enqueue a workflow run.

    Per the concurrency-v1 rule (D), if a non-terminal entry already
    exists for ``(folder_path, workflow_name)`` we raise ValueError with
    state ``already_queued`` or ``already_running``. The caller can then
    surface the existing run_id to the user.
    """
    folder = _resolved(folder_path)
    wf = (workflow_name or "").strip()
    if not wf:
        raise ValueError("workflow_name must be non-empty")
    with _queue_lock(log_root):
        state = _load_state(log_root)
        for e in state.values():
            if e.folder_path == folder and e.workflow_name == wf and \
               e.state in ("pending", "leased"):
                label = "already_queued" if e.state == "pending" else "already_running"
                raise ValueError(
                    f"{label}: run {e.run_id} for {wf!r} on {folder} is {e.state}"
                )
        entry = QueueEntry(
            run_id=_new_run_id(folder, wf),
            folder_path=folder,
            workflow_name=wf,
            enqueued_at=_now_iso(),
            enqueued_by=enqueued_by or "system",
            state="pending",
            last_state_at=_now_iso(),
        )
        _append_queue_event(entry, log_root)
        state[entry.run_id] = entry
        _save_state(state, log_root)
        return entry


def _is_actively_leased(entry: QueueEntry,
                        log_root: Optional[Path] = None) -> bool:
    if entry.state != "leased":
        return False
    lease = _read_lease(entry.run_id, log_root)
    if lease is None:
        # State says leased but no lease file: treat as stale → recoverable
        return False
    return not lease.is_expired


def take_next_run(worker_id: str,
                  *,
                  lease_seconds: int = DEFAULT_LEASE_SECONDS,
                  folder_allowed: Optional[Callable[[str], bool]] = None,
                  log_root: Optional[Path] = None) -> Optional[QueueEntry]:
    """Atomically claim the next pending run for ``worker_id``.

    Returns the leased entry, or None if the queue has nothing pending.
    Side-effect: stale leases (state=leased but lease file expired/missing)
    are auto-revoked back to pending so they can be re-taken — this is the
    crash-recovery property.

    ``folder_allowed`` restricts both passes (the stale-lease revoke and the
    take) to runs whose ``folder_path`` it admits — per-principal scoping
    leases and mutates nothing outside the caller's workspaces.
    """
    with _queue_lock(log_root):
        state = _load_state(log_root)
        # First pass: revoke stale leases
        revoked: list[str] = []
        for rid, entry in state.items():
            if folder_allowed is not None and not folder_allowed(entry.folder_path):
                continue
            if entry.state == "leased" and not _is_actively_leased(entry, log_root):
                entry.state = "pending"
                entry.leased_to = ""
                entry.last_state_at = _now_iso()
                entry.error = "lease expired (worker died?); reverted to pending"
                _append_queue_event(entry, log_root)
                revoked.append(rid)
                # Best-effort: remove any orphan lease file
                _drop_lease(rid, log_root)
        if revoked:
            _save_state(state, log_root)
        # Second pass: take the oldest pending
        candidates = [e for e in state.values() if e.state == "pending"
                      and (folder_allowed is None
                           or folder_allowed(e.folder_path))]
        if not candidates:
            return None
        candidates.sort(key=lambda e: e.enqueued_at)
        pick = candidates[0]
        pick.state = "leased"
        pick.leased_to = worker_id
        pick.last_state_at = _now_iso()
        pick.error = ""
        _append_queue_event(pick, log_root)
        state[pick.run_id] = pick
        _save_state(state, log_root)
        # Write the lease file
        now = _now_epoch()
        _write_lease(Lease(
            run_id=pick.run_id,
            worker_id=worker_id,
            pid=os.getpid(),
            host=socket.gethostname(),
            acquired_at=now,
            expires_at=now + max(5, int(lease_seconds)),
        ), log_root)
        return pick


def renew_lease(run_id: str,
                *,
                additional_seconds: int = DEFAULT_LEASE_SECONDS,
                log_root: Optional[Path] = None) -> bool:
    """Extend an existing lease's expiry. Returns False if the lease is
    missing or the run is no longer leased to anyone."""
    with _queue_lock(log_root):
        state = _load_state(log_root)
        entry = state.get(run_id)
        if entry is None or entry.state != "leased":
            return False
        lease = _read_lease(run_id, log_root)
        if lease is None:
            return False
        lease.expires_at = _now_epoch() + max(5, int(additional_seconds))
        _write_lease(lease, log_root)
        return True


def mark_done(run_id: str,
              *,
              worker_id: Optional[str] = None,
              log_root: Optional[Path] = None) -> bool:
    """Mark a leased run as completed.

    If ``worker_id`` is provided, B7.3 lease-ownership is enforced:
    refuses with :class:`LeaseStolen` if the active lease holder is not
    ``worker_id`` (or the lease has expired). When omitted the call
    behaves pre-0.6.8 — no ownership check, kept for back-compat with
    older callers and tests.
    """
    if worker_id is not None:
        _assert_lease_owned(run_id, worker_id, log_root)
    return _finalise(run_id, "done", "", log_root)


def mark_failed(run_id: str,
                error: str = "",
                *,
                worker_id: Optional[str] = None,
                log_root: Optional[Path] = None) -> bool:
    """Mark a leased run as failed. See :func:`mark_done` for worker_id."""
    if worker_id is not None:
        _assert_lease_owned(run_id, worker_id, log_root)
    return _finalise(run_id, "failed", error, log_root)


# B7.3: explicit "run" suffix variants required by the spec. They REQUIRE
# worker_id and always enforce lease ownership.
def mark_run_done(run_id: str,
                  worker_id: str,
                  *,
                  log_root: Optional[Path] = None) -> bool:
    """Strict mark-done that requires + enforces lease ownership.

    Raises :class:`LeaseStolen` if the lease holder isn't ``worker_id``
    or the lease has expired. Returns False if the run is already in a
    terminal state.
    """
    _assert_lease_owned(run_id, worker_id, log_root)
    return _finalise(run_id, "done", "", log_root)


def mark_run_failed(run_id: str,
                    worker_id: str,
                    error: str = "",
                    *,
                    log_root: Optional[Path] = None) -> bool:
    """Strict mark-failed that requires + enforces lease ownership."""
    _assert_lease_owned(run_id, worker_id, log_root)
    return _finalise(run_id, "failed", error, log_root)


def _assert_lease_owned(run_id: str,
                        worker_id: str,
                        log_root: Optional[Path] = None) -> None:
    """Refuse if ``worker_id`` is not the active lease holder.

    Active = lease file exists, expires_at is in the future, worker_id
    matches. Any other combination is treated as "lease stolen" and
    raises LeaseStolen. A warning is logged for visibility (race
    conditions are rare and worth surfacing).
    """
    import logging as _logging
    lease = _read_lease(run_id, log_root)
    if lease is None:
        _logging.getLogger(__name__).warning(
            "lease check failed for run %s: no lease file (worker_id=%s)",
            run_id, worker_id,
        )
        raise LeaseStolen(
            f"run {run_id}: no active lease (worker_id={worker_id!r})"
        )
    if lease.is_expired:
        _logging.getLogger(__name__).warning(
            "lease check failed for run %s: lease expired at %s (worker_id=%s)",
            run_id, lease.expires_at, worker_id,
        )
        raise LeaseStolen(
            f"run {run_id}: lease expired at epoch {lease.expires_at}"
        )
    if lease.worker_id != worker_id:
        _logging.getLogger(__name__).warning(
            "lease check failed for run %s: held by %s, claimed by %s",
            run_id, lease.worker_id, worker_id,
        )
        raise LeaseStolen(
            f"run {run_id}: lease held by {lease.worker_id!r}, "
            f"not {worker_id!r}"
        )


def cancel_run(run_id: str,
               *,
               actor: str = "user",
               log_root: Optional[Path] = None) -> bool:
    with _queue_lock(log_root):
        state = _load_state(log_root)
        entry = state.get(run_id)
        if entry is None or entry.state in ("done", "failed", "cancelled"):
            return False
        entry.state = "cancelled"
        entry.last_state_at = _now_iso()
        entry.error = f"cancelled by {actor}"
        _append_queue_event(entry, log_root)
        state[run_id] = entry
        _save_state(state, log_root)
        _drop_lease(run_id, log_root)
        return True


def _finalise(run_id: str,
              new_state: str,
              error: str,
              log_root: Optional[Path]) -> bool:
    with _queue_lock(log_root):
        state = _load_state(log_root)
        entry = state.get(run_id)
        if entry is None or entry.state in ("done", "failed", "cancelled"):
            return False
        entry.state = new_state
        entry.last_state_at = _now_iso()
        entry.error = error or ""
        _append_queue_event(entry, log_root)
        state[run_id] = entry
        _save_state(state, log_root)
        _drop_lease(run_id, log_root)
        return True


# ---------------------------------------------------------------------------
# Read-side
# ---------------------------------------------------------------------------


def list_queue(*,
               state_filter: Optional[str] = None,
               folder_path: Optional[str | Path] = None,
               log_root: Optional[Path] = None) -> list[QueueEntry]:
    """List queue entries. Optionally filter by state and/or folder.

    Stale-lease auto-revoke is NOT done here — the read path should be
    side-effect-free. Use ``take_next_run`` for the revoke-and-take cycle.
    """
    state = _load_state(log_root)
    out = list(state.values())
    if state_filter:
        out = [e for e in out if e.state == state_filter]
    if folder_path:
        fp = _resolved(folder_path)
        out = [e for e in out if e.folder_path == fp]
    out.sort(key=lambda e: e.enqueued_at)
    return out


def get_run(run_id: str,
            log_root: Optional[Path] = None) -> Optional[QueueEntry]:
    return _load_state(log_root).get(run_id)


# ---------------------------------------------------------------------------
# Crash-recovery surface
# ---------------------------------------------------------------------------


def inspect_stuck_runs(*,
                       stale_pending_seconds: int = 300,
                       log_root: Optional[Path] = None) -> list[dict[str, Any]]:
    """Return queue entries that look stuck and warrant user attention.

    Two flavours:

    - ``leased-stale``: state=leased but the lease file is missing or
      expired. The worker probably died.
    - ``pending-stale``: state=pending and enqueued more than
      ``stale_pending_seconds`` ago. The worker probably isn't running.

    This is the eager surface for the dashboard's resume panel. It does
    NOT mutate state (unlike ``take_next_run``). The user decides via
    Resume / Mark-failed.
    """
    state = _load_state(log_root)
    out: list[dict[str, Any]] = []
    now = _now_epoch()
    cutoff_pending = now - max(1, int(stale_pending_seconds))
    for entry in state.values():
        if entry.state == "leased":
            lease = _read_lease(entry.run_id, log_root)
            if lease is None or lease.is_expired:
                out.append({
                    "kind":           "leased-stale",
                    "entry":          entry.to_dict(),
                    "lease":          lease.to_dict() if lease else None,
                    "reason":         "worker lease missing" if lease is None
                                       else "worker lease expired",
                })
        elif entry.state == "pending":
            # enqueued_at is ISO-8601 with microsecond precision; convert
            # via a forgiving parser
            try:
                from datetime import datetime, timezone
                # Strip trailing Z and parse as UTC
                ts = entry.enqueued_at.rstrip("Z")
                dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
                epoch = int(dt.timestamp())
            except Exception:
                continue
            if epoch < cutoff_pending:
                out.append({
                    "kind":   "pending-stale",
                    "entry":  entry.to_dict(),
                    "reason": f"enqueued {now - epoch}s ago, no worker pickup",
                })
    out.sort(key=lambda r: r["entry"].get("enqueued_at", ""))
    return out


def resume_run(run_id: str,
               *,
               actor: str = "user",
               log_root: Optional[Path] = None) -> bool:
    """Revoke a stale lease and flip the run back to pending.

    Returns False if the run is missing or already in a terminal /
    pending state (resume is a no-op in that case)."""
    with _queue_lock(log_root):
        state = _load_state(log_root)
        entry = state.get(run_id)
        if entry is None:
            return False
        if entry.state != "leased":
            # nothing to resume
            return False
        entry.state = "pending"
        entry.leased_to = ""
        entry.last_state_at = _now_iso()
        entry.error = f"resumed by {actor} (was leased to {entry.leased_to or '?'})"
        _append_queue_event(entry, log_root)
        state[run_id] = entry
        _save_state(state, log_root)
        _drop_lease(run_id, log_root)
    return True
