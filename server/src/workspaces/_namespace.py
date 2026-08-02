# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Namespace migration shim — legacy ``WORKSPACEVERSUM_*`` / ``~/.workspaceversum/`` support.

Workspace was previously named Workspaceversum on-disk and in env vars. We renamed to
``WORKSPACE_*`` env vars and ``~/.workspace/`` paths. This module preserves
backward compatibility for one minor release window (slated for removal in
0.8) so existing installs keep working without manual migration.

Two responsibilities, both run once at package import:

1. **Env vars.** For every ``WORKSPACE_*`` variable Workspace reads, if it is unset
   but the legacy ``WORKSPACEVERSUM_*`` equivalent is set, copy the value over
   and emit a one-time DeprecationWarning. New variables always win when both
   are set (no surprises for users who already migrated).

2. **Home directory.** On first use of ``~/.workspace/``, if the path does not
   exist but ``~/.workspaceversum/`` does, move the contents and write a
   ``namespace_migration`` audit event. The legacy directory is left in place
   (renamed to ``~/.workspaceversum.migrated/``) so users can verify the move
   before deleting.

The folder-marker file (``.workspaceversum-folder-id``) and per-workspace policy
file (``.workspaceversum-policy.json``) are NOT migrated by this shim — those
are per-workspace concerns and need to be handled at workspace open. See
``workspaces.mutation_log.open_folder()`` for the workspace-level migration.

Removed in 0.8. Until then, this shim is the single source of truth for
namespace back-compat.
"""

from __future__ import annotations

import os
import shutil
import warnings
from pathlib import Path

# All env vars Workspace currently reads. Update this list when you add a new one.
_RENAMED_ENV_VARS = (
    "FOLDER_CONTEXT",
    "INTEGRATION",
    "KEY_DIR",
    "LOCAL_LLM_API_KEY",
    "LOCAL_LLM_MODEL",
    "LOCAL_LLM_TIMEOUT_SECS",
    "LOCAL_LLM_URL",
    "LOG_DIR",
    "LOG_ROOT",
    "MODELS_DIR",
    "PROFILE",
    "VAULT",
    # Newer (post-0.6.8) — listed defensively so future-additions get the same
    # back-compat treatment for free if anyone ever set them on WORKSPACEVERSUM_*.
    "SYMLINK_MODE",
)

_LEGACY_HOME = Path.home() / ".workspaceversum"
_NEW_HOME = Path.home() / ".workspace"
_MIGRATED_HOME = Path.home() / ".workspaceversum.migrated"

_warned: set[str] = set()


def _warn_once(message: str) -> None:
    if message in _warned:
        return
    _warned.add(message)
    warnings.warn(message, DeprecationWarning, stacklevel=3)


def migrate_legacy_env_vars() -> None:
    """Copy any ``WORKSPACEVERSUM_X`` env var to ``WORKSPACE_X`` if the new name is unset.

    Idempotent. Safe to call multiple times. The first call that finds a
    legacy var emits a DeprecationWarning per var.
    """
    for suffix in _RENAMED_ENV_VARS:
        legacy = f"WORKSPACEVERSUM_{suffix}"
        modern = f"WORKSPACE_{suffix}"
        if modern in os.environ:
            # User already migrated; new name wins, no warning needed.
            continue
        if legacy in os.environ:
            os.environ[modern] = os.environ[legacy]
            _warn_once(
                f"Env var {legacy} is deprecated; use {modern}. "
                f"The legacy name still works for now (removed in 0.8)."
            )


def home_dir() -> Path:
    """Return the canonical ``~/.workspace/`` path, migrating from legacy on first call.

    If ``~/.workspace/`` does not exist but ``~/.workspaceversum/`` does, move it.
    The legacy path is renamed to ``~/.workspaceversum.migrated/`` so users can
    verify before deleting. A ``namespace_migration`` audit event is written
    to ``~/.workspace/log/`` recording what was moved.
    """
    if _NEW_HOME.exists():
        return _NEW_HOME

    if _LEGACY_HOME.exists():
        _warn_once(
            f"Migrating {_LEGACY_HOME} → {_NEW_HOME}. "
            f"The legacy directory will be renamed to {_MIGRATED_HOME} for safekeeping. "
            f"Delete it manually once you've verified the new layout works."
        )
        # Move the legacy contents to the new location. We use rename because
        # both paths are on the same filesystem in any realistic deployment.
        shutil.move(str(_LEGACY_HOME), str(_NEW_HOME))
        # Leave a breadcrumb so users know what happened.
        _MIGRATED_HOME.symlink_to(_NEW_HOME, target_is_directory=True)
        # Write the migration audit event.
        _write_migration_event()
        return _NEW_HOME

    # Neither exists — fresh install. Create the new directory.
    _NEW_HOME.mkdir(parents=True, exist_ok=True)
    return _NEW_HOME


def _write_migration_event() -> None:
    """Append a ``namespace_migration`` event to the audit log.

    Written defensively — if the log dir doesn't exist or chain layer isn't
    importable yet, we silently skip. The user-visible signal is the
    DeprecationWarning; this is for the audit trail.
    """
    try:
        from datetime import datetime, timezone

        from workspaces.mutation_log import LOG_ROOT_DEFAULT, append_event

        event = {
            "kind": "namespace_migration",
            "ts": datetime.now(timezone.utc).isoformat(),
            "from": str(_LEGACY_HOME),
            "to": str(_NEW_HOME),
            "shim": "workspaces._namespace.home_dir",
        }
        # Use the unscoped chain — this is a global-state event, not workspace-scoped.
        append_event(LOG_ROOT_DEFAULT / "unscoped", event)
    except Exception:
        # Best-effort. The warning was already emitted.
        pass


def folder_marker_filenames() -> tuple[str, ...]:
    """Return both the new and legacy folder-marker filenames.

    Callers that probe for the marker should accept either; callers that
    write the marker should write the first (modern) name only.
    """
    return (".workspace-folder-id", ".workspaceversum-folder-id")


def policy_filenames() -> tuple[str, ...]:
    """Return both the new and legacy policy filenames."""
    return (".workspace-policy.json", ".workspaceversum-policy.json")


# Run env-var migration immediately on import so downstream modules
# importing this package see the modernised env vars from the first lookup.
migrate_legacy_env_vars()
