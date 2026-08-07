# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-norm's rule registry — RVND owns neither.

RVND's parallel span-norm registry is RETIRED. The typed, persisted, audited
span-placement store — span placement, per-user cross-folder indexing, document
re-anchoring, orphan tracking, the reverse/search queries and the article-aware
legal-text path — now lives in ``loomground-norm``
(``loomground_norm.rule_registry``); this module re-exports it behind the
historical import names (``Anchor``, ``SpanNorm``, ``RuleRegistry``,
``place_into_registry``) through the ``adapters/norm`` seam.

The plane's placement runs on injected ports; the seam wires RVND's providers in
— legal-domain anchoring (``legal_world`` + ``corpus.ingest``), the identity
spine (``urn.mint_canonical``), the signed mutation log, the consumed ingest
legal-norm splitter, and the per-user ``~/.workspace/log`` mirror — so
``RuleRegistry(folder, user=..., user_root=..., log_root=...)`` and
``place_into_registry(...)`` keep their historical shape and behavior. No
registry mechanics live here. Callers are unchanged; this shim is deleted once
they migrate to the plane directly.
"""
from __future__ import annotations

from .adapters.norm import (
    Anchor,
    SpanNorm,
    RuleRegistry,
    place_into_registry,
)

__all__ = ["Anchor", "SpanNorm", "RuleRegistry", "place_into_registry"]
