# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-legal's corpus loader — RVND owns neither.

RVND's parallel md-table corpus loader (the markdown-table parser, the
country/EU27/body-code normalisers, and the ``build_world`` graph builder) is
RETIRED. It now lives in ``loomground-legal`` and is consumed through the
``adapters/legal`` seam (the workspaces boundary rule confines every upstream
import there).

This module re-exports ``build_world`` behind its historical name and keeps the
**env-configured reference-dir resolver local**: ``_default_refdir()`` /
``WORKSPACE_WORLD_MAP_DIR`` decide *where the companion corpus lives* on this
host — RVND folder runtime, injected into the consumed ``build_world`` (which
takes the ref dir as a parameter). ``EU27`` is re-exported for the callers
(``world_relations``) that historically imported it from here.
"""
from __future__ import annotations

from pathlib import Path

from .adapters.legal import build_world, EU27

__all__ = ["build_world", "EU27", "_default_refdir", "WORKSPACE_WORLD_MAP_DIR"]

#: The env var naming the world-map reference directory (companion data the core
#: does not ship).
WORKSPACE_WORLD_MAP_DIR = "WORKSPACE_WORLD_MAP_DIR"


def _default_refdir() -> Path:
    """The world-map reference dir. Bring your own corpus: set
    ``WORKSPACE_WORLD_MAP_DIR``, else ``~/.workspace/world-map``. The corpus is
    companion data the core does not ship. RVND folder runtime, injected into the
    consumed ``build_world``."""
    import os
    env = os.environ.get(WORKSPACE_WORLD_MAP_DIR, "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".workspace" / "world-map"
