# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of the governance compiler now owned by ``loomground-ingest``.

Internal by design: a seam re-export, not a console or MCP surface.

This is the one sanctioned place that imports ``loomground_ingest.governance``
directly; the rest of ``workspaces`` reaches the compiler, its genre router, the
norm splitter, and the ``GovernanceIngester`` through here, never through the
upstream package. RVND owns application policy and integration; the policy
grammar/vocabulary authority is ``loomground-governance`` and the compiler that
lowers it (policy text -> express primitives -> validated ``.lg`` patch -> nD
projection) lives in ``loomground-ingest``.
"""
from __future__ import annotations

# Submodules re-exported as attributes so a caller can keep a module alias
# (e.g. ``from .adapters.ingest.governance import compiler as policy_ingest``)
# and reach the compiler's helpers, exactly as it did for the RVND-local twin.
from loomground_ingest.governance import (  # noqa: F401
    compiler,
    genre_router,
    legal_norm_splitter,
    policy_normalise,
)
from loomground_ingest.governance import (  # noqa: F401
    GovernanceIngester,
    ingest,
    set_default_proposer,
)

__all__ = [
    "compiler",
    "genre_router",
    "legal_norm_splitter",
    "policy_normalise",
    "GovernanceIngester",
    "ingest",
    "set_default_proposer",
]
