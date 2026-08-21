# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compatibility alias — ``workspaces`` is now :mod:`rvnd`; internal by design.

The import package was named after the thing it operates on. A *workspace* is a
user folder external to this repo, and the engine runs perfectly well with none.
Naming the engine after its subject invited reading process-global facts (the
enforcement posture is read from ``os.environ``) as per-workspace ones.

ONLY the Python import package moved. ``workspaces`` remains a live identifier
elsewhere and is deliberately untouched: the ``workspace_*`` MCP tool names, the
``workspaces`` parameter of the session operations, the ``workspaces`` key in a
saved session bundle, the ``workspaces`` console command, and
``WORKSPACES_ALLOW_UNREGISTERED``. Those are contracts with hosts, with operators
and with sessions already on disk.

Replacing this module's entry in :data:`sys.modules` makes submodules resolve
through ``rvnd``'s ``__path__``, so ``from workspaces.foo import bar`` keeps
working for consumers outside this repo.
"""
from __future__ import annotations

import sys
import warnings

import rvnd

warnings.warn(
    "`workspaces` is deprecated; import `rvnd` instead. The engine no longer "
    "carries the name of the folders it governs.",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = rvnd
