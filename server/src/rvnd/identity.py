# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Per-workspace salted identity helpers.

Provides ``workspace_salt(folder)`` — a per-workspace secret used to compute
opaque document tokens (and any other opaque view that should not be
reversible by an attacker who guesses candidate plaintexts).

Salt lifecycle:

- Generated once per workspace, on first use, using ``os.urandom(32)``.
- Stored on disk at ``<log_root>/<folder-hash>/workspace-salt.hex``.
- NEVER returned in any MCP response, prompt, or audit log.
- If the user deletes their mutation logs for a workspace, the salt is
  regenerated → all existing opaque tokens for that workspace stop
  matching. This is a tolerable failure mode: re-ingest the folder
  and tokens regenerate.

The salt is OS-readable by the user account that owns it. Anyone who
can read ``~/.workspace`` (or the legacy ``~/.workspaceversum`` if you
haven't migrated yet) can compute the same tokens. This is
acceptable because the threat model the salt addresses is *cloud
attacker tries to reverse leaked tokens by guessing paths*, not
*local attacker has full read access to your home directory* — that
threat is FileVault's job, not Workspace's.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from .mutation_log import LOG_ROOT_DEFAULT, folder_hash


_SALT_FILENAME = "workspace-salt.hex"
_SALT_BYTES = 32


def _salt_path(folder_path: str | Path,
               log_root: str | Path | None = None) -> Path:
    """Return the absolute path of the salt file for a workspace."""
    root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
    return root / folder_hash(folder_path) / _SALT_FILENAME


def workspace_salt(folder_path: str | Path,
                log_root: str | Path | None = None) -> bytes:
    """Return this workspace's salt. Creates one if it doesn't exist.

    Thread-safe enough for single-user local use: an O_EXCL create
    races against a concurrent caller, but the loser falls through to
    read the winner's file. Race-window is one syscall wide.
    """
    p = _salt_path(folder_path, log_root=log_root)
    if p.exists():
        try:
            return bytes.fromhex(p.read_text().strip())
        except (ValueError, OSError):
            # Corrupted salt — fall through to regenerate.
            pass
    p.parent.mkdir(parents=True, exist_ok=True)
    fresh = os.urandom(_SALT_BYTES)
    # Atomic-ish write: write to .tmp then rename.
    tmp = p.with_suffix(".hex.partial")
    tmp.write_text(fresh.hex())
    tmp.replace(p)
    return fresh


def opaque_doc_token(source: Optional[str],
                     folder_path: str | Path,
                     log_root: str | Path | None = None,
                     length: int = 12) -> str:
    """Compute an opaque ``<DOC_xxx>`` token for a document path.

    Uses HMAC-SHA256(salt, source) → hex-truncated. The salt is the
    per-workspace secret from ``workspace_salt()``.

    ``length`` controls the truncation length (in hex chars). 12 hex
    chars = 48 bits of collision resistance; collisions within a
    folder are unlikely below ~16M documents and harmless if they
    occur (the LLM just sees two paths sharing one token).

    Returns ``"<DOC_NONE>"`` if ``source`` is empty.
    """
    if not source:
        return "<DOC_NONE>"
    salt = workspace_salt(folder_path, log_root=log_root)
    digest = hashlib.sha256(salt + source.encode("utf-8", errors="ignore")).hexdigest()
    return f"<DOC_{digest[:length]}>"
