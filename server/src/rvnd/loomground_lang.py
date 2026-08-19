# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compatibility facade for the independent Loomground Solver runtime.

RVND owns application policy and integration. Parsing, validation, projection,
and evaluation are supplied by ``loomground-solver`` and ultimately consume the
neutral artifacts published by ``loomground-governance``.
"""
from __future__ import annotations

from rvnd.adapters.solver.loomground import *  # noqa: F401,F403
# `import *` skips underscore names, so the one private this facade really
# re-exports is named explicitly. The redundant alias is the conventional
# way to say "re-exported on purpose" rather than "imported and unused".
# `_guard_holds` used to be listed here and was never re-exported through
# this module — operations.py imports it from the adapter directly.
from rvnd.adapters.solver.loomground import _has_cycle as _has_cycle


def _loomground_core():
    """Legacy test hook exposing RVND's read-only bundled artifact facade.

    Kept during the dependency migration so existing deployments and parity
    checks retain their public seam. Runtime language semantics no longer come
    from this bundle.
    """
    from . import loomground_assets

    return loomground_assets
