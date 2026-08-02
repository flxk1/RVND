# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace migrate + gc (B7.1 + B7.2 / 0.6.8).

Two operator-facing maintenance verbs:

- :func:`migrate_workspace` — re-key a workspace log directory from
  ``<old_hash>`` to ``<new_hash>`` because the user renamed/moved the
  workspace folder. Without this, the log appears orphaned (the folder
  hash key no longer matches any live path) and the new folder starts
  with an empty log.

- :func:`gc_orphans` — walk every log directory under ``<log_root>``
  and identify ones whose source folder we can't locate. Useful when
  the user moved several workspaces and forgot to migrate.

Both operations are explicit user actions; nothing here runs automatically.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .mutation_log import (
    LOG_ROOT_DEFAULT,
    LogEvent,
    MutationLog,
    folder_hash,
)


# ---------------------------------------------------------------------------
# B7.1 — migrate
# ---------------------------------------------------------------------------


class WorkspaceMigrateError(RuntimeError):
    """Migration refused (missing source, target collision, …)."""


@dataclass
class MigrateResult:
    from_path: str
    to_path: str
    from_hash: str
    to_hash: str
    event_count: int
    strategy: str
    audit_id: str


def _log_dir(log_root: Path, fhash: str) -> Path:
    return log_root / fhash


_MIGRATION_MARKER = ".migrating.json"


def _write_migration_marker(log_dir: Path, *, to_path: str, to_hash: str,
                            from_path: str, from_hash: str) -> None:
    """Drop a crash-window marker naming this dir's migration target.

    Written into the SOURCE dir just before the move, so it travels with the
    move into the destination. If the process dies between the move and the
    migration audit event, the destination still carries its intended
    identity — gc_orphans reads it and refuses to treat the dir as an orphan,
    closing the window where a correctly-moved log could be deleted."""
    try:
        (log_dir / _MIGRATION_MARKER).write_text(json.dumps({
            "to_path": to_path, "to_hash": to_hash,
            "from_path": from_path, "from_hash": from_hash,
            "started_at": time.time(),
        }), encoding="utf-8")
    except OSError as exc:
        raise WorkspaceMigrateError(
            f"could not write migration recovery marker in {log_dir}"
        ) from exc


def _read_migration_marker(log_dir: Path) -> Optional[str]:
    """Return the ``to_path`` from an in-flight migration marker, or None."""
    try:
        raw = (log_dir / _MIGRATION_MARKER).read_text()
    except (OSError, ValueError):
        return None
    try:
        tp = json.loads(raw).get("to_path")
    except (json.JSONDecodeError, AttributeError):
        return None
    return tp if isinstance(tp, str) and tp else None


def _clear_migration_marker(log_dir: Path) -> None:
    try:
        (log_dir / _MIGRATION_MARKER).unlink()
    except OSError:
        pass


def migrate_workspace(
    from_path: str | Path,
    to_path: str | Path,
    *,
    on_collision: str = "refuse",   # refuse | merge | archive_existing
    operator: str = "system",
    log_root: Optional[Path] = None,
) -> MigrateResult:
    """Move a workspace log directory from one folder hash to another.

    Steps:
      1. Resolve OLD path → ``from_hash``.
      2. Confirm ``<log_root>/<from_hash>/events.jsonl`` exists.
      3. Resolve NEW path → ``to_hash``.
      4. Honour ``on_collision`` if ``<log_root>/<to_hash>`` already exists:
         - ``refuse`` (default): raise WorkspaceMigrateError.
         - ``merge``: append the OLD events to the NEW log file.
         - ``archive_existing``: move the existing target out of the way
           to ``<log_root>/_archived/<to_hash>.<ts>/`` first.
      5. Atomically move the source directory to the target.
      6. Write a ``system`` event in the NEW log recording the migration.
      7. Update the workspace registry if present (add NEW, drop OLD).
    """
    root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
    root.mkdir(parents=True, exist_ok=True)

    from_resolved = str(Path(from_path).expanduser().resolve())
    to_resolved = str(Path(to_path).expanduser().resolve())
    from_hash = folder_hash(from_path)
    to_hash = folder_hash(to_path)

    if from_hash == to_hash:
        raise WorkspaceMigrateError(
            f"old hash and new hash are identical ({from_hash}); "
            f"folder identity unchanged — nothing to migrate"
        )

    src_dir = _log_dir(root, from_hash)
    dst_dir = _log_dir(root, to_hash)
    src_events = src_dir / "events.jsonl"

    if not src_events.exists():
        raise WorkspaceMigrateError(
            f"no log at {src_events} (from_hash={from_hash}); "
            f"nothing to migrate"
        )

    strategy = (on_collision or "refuse").lower().strip()
    if strategy not in {"refuse", "merge", "archive_existing"}:
        raise WorkspaceMigrateError(
            f"unknown on_collision strategy: {strategy!r}"
        )

    pre_event_count = sum(1 for _ in src_events.open("rb"))

    # Crash-window marker: written into the source before any move so it
    # rides along to the destination. Cleared after the registry update. Not
    # used for merge (the destination pre-exists with its own provenance).
    if strategy in {"refuse", "archive_existing"} or not dst_dir.exists():
        _write_migration_marker(
            src_dir, to_path=to_resolved, to_hash=to_hash,
            from_path=from_resolved, from_hash=from_hash)

    if dst_dir.exists():
        if strategy == "refuse":
            _clear_migration_marker(src_dir)
            raise WorkspaceMigrateError(
                f"target log dir already exists at {dst_dir} "
                f"(to_hash={to_hash}); pick on_collision='merge' or "
                f"'archive_existing' to override"
            )
        if strategy == "archive_existing":
            archive_root = root / "_archived"
            archive_root.mkdir(parents=True, exist_ok=True)
            archived_at = root / "_archived" / f"{to_hash}.{int(time.time())}"
            shutil.move(str(dst_dir), str(archived_at))
            # Now dst_dir is gone; treat as plain move.
            strategy_used = "archive_existing"
            shutil.move(str(src_dir), str(dst_dir))
        else:  # merge
            strategy_used = "merge"
            dst_events = dst_dir / "events.jsonl"
            dst_dir.mkdir(parents=True, exist_ok=True)
            # Append source events to destination, then delete source dir.
            with src_events.open("rb") as src_fh:
                with dst_events.open("ab") as dst_fh:
                    for chunk in iter(lambda: src_fh.read(65536), b""):
                        dst_fh.write(chunk)
            shutil.rmtree(src_dir)
    else:
        strategy_used = "move"
        shutil.move(str(src_dir), str(dst_dir))

    # Write a system event in the NEW log marking the migration.
    new_log = MutationLog(to_resolved, log_root=root)
    audit_id = new_log.append_raw(
        event="system",
        channel="system",
        pair_id=f"workspace_migrate:{uuid.uuid4().hex[:12]}",
        actor=f"operator:{operator}",
        lifecycle_state="",
        extra={
            "kind":      "workspace_migrated",
            "from_hash": from_hash,
            "to_hash":   to_hash,
            "from_path": from_resolved,
            "to_path":   to_resolved,
            "strategy":  strategy_used,
            "event_count_before": pre_event_count,
        },
    )

    # Update workspace registry, if present.
    try:
        from .workspace_registry import (
            add_known_workspace,
            list_known_workspaces,
            remove_known_workspace,
        )
        existing = {w.get("path") for w in list_known_workspaces(log_root=root)}
        if from_resolved in existing:
            remove_known_workspace(from_resolved, log_root=root)
            add_known_workspace(to_resolved, log_root=root)
        elif to_resolved not in existing:
            # If the OLD wasn't registered but the user is migrating
            # anyway, register the NEW so it shows up in the dashboard.
            add_known_workspace(to_resolved, log_root=root)
    except Exception:
        # Registry is a nice-to-have; never block the migration on it.
        pass

    # Migration is fully recorded (moved + audit event + registry). Clearing
    # the marker last means a crash at ANY earlier point leaves it in place
    # for gc_orphans / a resume to find.
    _clear_migration_marker(dst_dir)

    return MigrateResult(
        from_path=from_resolved,
        to_path=to_resolved,
        from_hash=from_hash,
        to_hash=to_hash,
        event_count=pre_event_count,
        strategy=strategy_used,
        audit_id=audit_id,
    )


# ---------------------------------------------------------------------------
# B7.2 — orphan gc
# ---------------------------------------------------------------------------


@dataclass
class OrphanCandidate:
    folder_hash:    str
    log_dir:        Path
    event_count:    int
    last_event_ts:  float
    recovered_path: Optional[str]   # None if we couldn't extract one
    action:         str             # "ok" | "orphan" | "archived" | "deleted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_hash":    self.folder_hash,
            "log_dir":        str(self.log_dir),
            "event_count":    self.event_count,
            "last_event_ts":  self.last_event_ts,
            "recovered_path": self.recovered_path or "(unknown)",
            "action":         self.action,
        }


_ARCHIVE_DIRNAME = "_archived"


def _scan_log_dir(log_dir: Path) -> tuple[int, float, Optional[str]]:
    """Walk events.jsonl: return (count, last_ts, recovered_path).

    ``recovered_path`` is the workspace's CURRENT folder path. After a
    migration, the carried-over events still record the OLD ``folder_path``
    in their bodies, so recovering from the first event would report a path
    that no longer exists on disk — and gc_orphans would then classify a
    healthy, successfully-migrated workspace as an orphan and archive/delete
    it. To avoid that, the latest ``workspace_migrated`` event's ``to_path``
    is authoritative when present; otherwise fall back to the first event's
    ``folder_path``. An in-flight ``.migrating`` marker (a crash between the
    move and the audit event) is also honoured, so the destination's current
    identity is known even before the audit event lands.
    """
    events_path = log_dir / "events.jsonl"
    marker = _read_migration_marker(log_dir)
    if not events_path.exists():
        # A dir that holds only the crash-window marker still has a known
        # identity — its migration target.
        return (0, 0.0, marker) if marker else (0, 0.0, None)
    count = 0
    last_ts = 0.0
    first_path: Optional[str] = None
    migrated_to: Optional[str] = None
    with events_path.open("rb") as fh:
        for raw in fh:
            try:
                line = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            count += 1
            ts = obj.get("ts")
            if isinstance(ts, (int, float)) and ts > last_ts:
                last_ts = float(ts)
            if first_path is None:
                p = obj.get("folder_path")
                if isinstance(p, str) and p:
                    first_path = p
            extra = obj.get("extra")
            if isinstance(extra, dict) and extra.get("kind") == "workspace_migrated":
                tp = extra.get("to_path")
                if isinstance(tp, str) and tp:
                    migrated_to = tp   # latest wins (events are in order)
    # Precedence: crash-window marker > recorded migration target > origin.
    recovered = marker or migrated_to or first_path
    return count, last_ts, recovered


def gc_orphans(
    *,
    log_root: Optional[Path] = None,
    archive: bool = False,
    delete: bool = False,
) -> list[OrphanCandidate]:
    """Walk every log dir, classify, optionally archive/delete orphans.

    A directory is an *orphan* iff one of:
      - the recovered folder_path doesn't resolve to a live filesystem entry, or
      - no path could be recovered at all (chain too short or malformed).

    Default mode (both flags False) is read-only: returns the list of
    candidates with ``action="ok"`` or ``"orphan"``.

    ``archive=True`` moves orphan log dirs to ``<log_root>/_archived/<hash>/``.
    ``delete=True`` removes orphan log dirs irreversibly. The CLI guards
    this behind a ``--yes-i-mean-it`` flag.
    """
    root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
    if not root.exists():
        return []
    out: list[OrphanCandidate] = []
    archive_root = root / _ARCHIVE_DIRNAME
    if archive:
        archive_root.mkdir(parents=True, exist_ok=True)

    for entry in sorted(root.iterdir()):
        # Skip non-hash dirs (queue files, archive folder, leases, …).
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") or entry.name in ("leases",):
            continue
        # Hash is 32 hex chars.
        if not (len(entry.name) == 32 and all(c in "0123456789abcdef" for c in entry.name)):
            continue

        count, last_ts, recovered = _scan_log_dir(entry)
        if _read_migration_marker(entry) is not None:
            # An in-flight migration (crash between the move and the audit
            # event, or a resume pending). Never reclaim it — the log is a
            # live workspace mid-relocation, not an orphan.
            action = "migrating"
        elif count == 0:
            action = "orphan"
        else:
            if recovered and Path(recovered).expanduser().exists():
                action = "ok"
            else:
                action = "orphan"

        if action == "orphan" and (archive or delete):
            if archive:
                target = archive_root / f"{entry.name}.{int(time.time())}"
                shutil.move(str(entry), str(target))
                action = "archived"
            elif delete:
                shutil.rmtree(entry)
                action = "deleted"

        out.append(OrphanCandidate(
            folder_hash=entry.name,
            log_dir=entry,
            event_count=count,
            last_event_ts=last_ts,
            recovered_path=recovered,
            action=action,
        ))

    return out
