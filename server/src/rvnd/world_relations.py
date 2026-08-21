# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-legal's relational enrichment — RVND owns neither.

RVND's parallel relational pass — the curated membership / treaty / adequacy /
regulator DATA and the ``enrich()`` that derives the law *between* the nodes of
the world corpus (memberships, ``enforces`` / ``supervises``, ``party_to`` /
``bound_by``, adequacy ``equivalent_to``, inter-instrument lineage,
``presumes_conformity``) — is RETIRED into ``loomground-legal`` and consumed
through the ``adapters/legal`` seam (the workspaces boundary rule confines every
upstream import there). The seam wires RVND's env-configured EU-acquis registry
into ``enrich`` as its ``instruments`` port.

This module re-exports ``enrich`` behind its historical name and keeps one thin
RVND convenience local: ``build_enriched_world()`` — loader + relational pass in
one call — which wires the env-configured reference dir and instrument CSV. It
is the sole entry ``legal_corpus.seed_registry`` uses.
"""
from __future__ import annotations

from .adapters.legal import enrich

__all__ = ["enrich", "build_enriched_world"]


def build_enriched_world():
    """Convenience: loader + relational pass in one call. Wires RVND's
    env-configured world-map reference dir (via ``world_corpus_loader``) and the
    instrument CSV (via ``enrich`` → ``regulatory_population.default_csv``)."""
    from .world_corpus_loader import build_world
    w = build_world()
    stats = enrich(w)
    return w, stats
