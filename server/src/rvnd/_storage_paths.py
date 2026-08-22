# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim — storage defaults now live in ``loomground_workspace``.

Where a workspace keeps what it accumulates is part of what a workspace is, so
``LOG_ROOT_DEFAULT`` moved to ``loomground_workspace.paths``. Kept as a shim
because ``mutation_log``, ``reasoning_integrity`` and ``hook`` address this
module path. Zero definitions of its own.
"""

from __future__ import annotations

from .adapters.workspace import LOG_ROOT_DEFAULT  # noqa: F401

__all__ = ["LOG_ROOT_DEFAULT"]
