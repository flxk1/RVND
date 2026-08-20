# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""A dropped audit write must not read as a successful operation.

Several audit writes were wrapped in ``except Exception: pass`` so that a
bookkeeping failure could not take down the operation it was recording. The
intent is right; the consequence was not. The operation returned success, and
in three cases returned ``audit_id: None`` -- which is exactly what a caller
sees when no audit is configured at all. A failed audit and an absent one were
indistinguishable, and nothing anywhere said a write had been lost.

This module keeps the availability property (the caller is not taken down) and
removes the silence:

* stderr, always -- it is the one channel that still works when the failure
  *is* the filesystem;
* an in-process register, for a server that wants to surface it live;
* a durable marker under the log root, because ``workspaces doctor`` runs in a
  different process than the server and cannot see an in-memory list.

The marker write is itself best-effort and must not raise: it is frequently
attempted in exactly the conditions that broke the original write. stderr has
already carried the report by then, so a lost marker degrades the record, it
does not hide the event.

internal by design: this is a reporting sink for other modules, not an
operator-facing operation. What an operator sees is the stderr line and the
``audit_drops`` check in ``workspaces doctor``, which reads the marker file.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

MARKER_NAME = "audit-drops.jsonl"

_DROPS: list[dict[str, Any]] = []


def _marker_path(log_root: str | Path | None) -> Path | None:
    root = log_root if log_root is not None else os.environ.get("WORKSPACE_L0_LOG_ROOT")
    return Path(root) / MARKER_NAME if root else None


def record(where: str, exc: BaseException, *,
           log_root: str | Path | None = None, **context: Any) -> dict[str, Any]:
    """Report an audit write that did not happen. Never raises."""
    entry: dict[str, Any] = {
        "where": where,
        "error": f"{type(exc).__name__}: {exc}",
        "ts": time.time(),
        **context,
    }
    _DROPS.append(entry)
    try:
        print(f"[rvnd] AUDIT WRITE DROPPED at {where}: {type(exc).__name__}: {exc}",
              file=sys.stderr, flush=True)
    except Exception:                      # noqa: BLE001 - stderr itself is gone
        pass
    path = _marker_path(log_root)
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        except OSError:
            # The disk is usually why we are here. stderr already reported it.
            pass
    return entry


def drops() -> list[dict[str, Any]]:
    """Audit writes dropped in this process, oldest first."""
    return list(_DROPS)


def clear() -> None:
    """Reset the in-process register (tests, and long-lived servers after read)."""
    _DROPS.clear()


def durable_drops(log_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Audit writes dropped by ANY process against this log root.

    Unreadable or malformed lines are reported as entries rather than skipped;
    a corrupt drop record is still evidence that a drop occurred, and silently
    dropping the record of a dropped write is the very failure this module
    exists to end.
    """
    path = _marker_path(log_root)
    if path is None or not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{"where": "audit_drop.durable_drops",
                 "error": f"{type(exc).__name__}: {exc}", "unreadable": True}]
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"where": "unknown", "error": "unparseable drop record",
                        "raw": line[:200]})
    return out
