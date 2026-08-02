# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The relational pass wired into routine ingest:
seed_registry persists the enriched reference corpus by default — regulators
stop floating, treaties bind, lineage and conformity edges exist — and the
bare seed remains available and unchanged."""

import pytest

from workspaces.legal_corpus import seed_registry
from workspaces.world_corpus_loader import _default_refdir

if not _default_refdir().is_dir():
    pytest.skip(
        "world-map corpus not installed — set WORKSPACE_WORLD_MAP_DIR or seed "
        "~/.workspace/world-map (ships with the eu-regulatory-companion)",
        allow_module_level=True,
    )


class TestEnrichedSeed:
    def test_enriched_is_default_and_substantial(self, tmp_path):
        reg = seed_registry(tmp_path)
        assert len(reg.entities) > 400          # the reference corpus, not just the seed
        assert len(reg.edges) > 500

    def test_relational_edge_families_present(self, tmp_path):
        reg = seed_registry(tmp_path)
        kinds = {r["connection"] for r in reg.edges.values()}
        for k in ("enforces", "party_to", "member_of", "bound_by", "applies_in"):
            assert k in kinds, f"missing relational family {k!r}"

    def test_edges_carry_basis(self, tmp_path):
        reg = seed_registry(tmp_path)
        member = [r for r in reg.edges.values() if r["connection"] == "member_of"]
        assert member and all(r.get("basis") for r in member[:20])

    def test_bare_seed_still_available(self, tmp_path):
        reg = seed_registry(tmp_path, enriched=False)
        assert 0 < len(reg.entities) < 100      # the original digital-law seed only

    def test_idempotent_reseed(self, tmp_path):
        a = seed_registry(tmp_path)
        n_ent, n_edge = len(a.entities), len(a.edges)
        b = seed_registry(tmp_path)
        assert (len(b.entities), len(b.edges)) == (n_ent, n_edge)

    def test_bulk_audit_event_logged(self, tmp_path):
        seed_registry(tmp_path, log_root=tmp_path / "log")
        from workspaces.mutation_log import MutationLog
        ops = [e.extra.get("op") for e in MutationLog(tmp_path, log_root=tmp_path / "log").replay()
               if e.extra.get("kind") == "legal-corpus"]
        assert "corpus.bulk" in ops
