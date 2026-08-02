# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compatibility facade for the independent Loomground Solver runtime.

RVND owns application policy and integration. Parsing, validation, projection,
and evaluation are supplied by ``loomground-solver`` and ultimately consume the
neutral artifacts published by ``loomground-governance``.
"""
from __future__ import annotations

from workspaces.adapters.solver.loomground import *  # noqa: F401,F403
from workspaces.adapters.solver.loomground import _guard_holds, _has_cycle


def _loomground_core():
    """Legacy test hook exposing RVND's read-only bundled artifact facade.

    Kept during the dependency migration so existing deployments and parity
    checks retain their public seam. Runtime language semantics no longer come
    from this bundle.
    """
    from . import loomground_assets

    return loomground_assets
