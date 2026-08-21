# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Graph export: the whole corpus — not just the EU digital acts — with
typed, basis-carrying edges, plus span-norm dots anchored into the map."""

import pytest

from rvnd.contracts.extractor import ingest_contract
from rvnd.graph_export import export_graph
from rvnd.legal_corpus import seed_registry
from rvnd.world_corpus_loader import _default_refdir

if not _default_refdir().is_dir():
    pytest.skip(
        "world-map corpus not installed — set WORKSPACE_WORLD_MAP_DIR or seed "
        "~/.workspace/world-map (ships with the eu-regulatory-companion)",
        allow_module_level=True,
    )

DPA = ('AGREEMENT between Norddata GmbH (the "Processor") and Beispiel AG '
       '(the "Controller").\n\n2. The Processor shall notify the Controller '
       'of a personal data breach without undue delay.\n')


class TestGraphExport:
    def test_full_world_not_just_eu_acts(self, tmp_path):
        seed_registry(tmp_path)
        g = export_graph(tmp_path)
        groups = {n["group"] for n in g["nodes"]}
        assert {"jurisdiction", "instrument", "regulator", "standards"} <= groups
        # beyond the EU digital acts: treaties/organisations from the
        # relational pass are present (party_to / member_of edges exist)
        kinds = {l["kind"] for l in g["links"]}
        assert {"member_of", "party_to", "enforces",
                "presumes_conformity"} <= kinds

    def test_edges_carry_basis(self, tmp_path):
        seed_registry(tmp_path)
        g = export_graph(tmp_path)
        with_basis = [l for l in g["links"] if l.get("basis")]
        assert len(with_basis) > 100          # typed AND grounded, not wiki-links

    def test_clauses_anchor_into_the_map(self, tmp_path):
        seed_registry(tmp_path)
        ingest_contract(tmp_path, DPA, contract_id="dpa-x",
                        log_root=tmp_path / "log")
        g = export_graph(tmp_path)
        clause_nodes = [n for n in g["nodes"] if n["group"] == "clause"]
        assert clause_nodes
        assert any(n["group"] == "contract" for n in g["nodes"])
        clause_ids = {n["id"] for n in clause_nodes}
        assert any((l["source"] in clause_ids) for l in g["links"])

    def test_per_statute_focus_subgraph(self, tmp_path):
        """The Adrian-vault view, grounded: real GDPR articles (verbatim
        fixture) become clause dots with Fundstellen, focused to one act."""
        import sys
        sys.path.insert(0, str((__import__("pathlib").Path(__file__).parent)))
        from test_legal_norm_splitter import GDPR
        from rvnd.rule_registry import RuleRegistry
        seed_registry(tmp_path)
        RuleRegistry(tmp_path, log_root=tmp_path / "log").place_legal_text(
            GDPR, "gdpr", source_document="gdpr.txt")
        g = export_graph(tmp_path, focus="gdpr")
        assert g["focus"] == "gdpr"
        labels = {n["label"] for n in g["nodes"] if n["group"] == "clause"}
        assert {"Art. 5(1)", "Art. 17(1)", "Art. 33(1)"} <= labels
        ids = {n["id"] for n in g["nodes"]}
        assert all(l["source"] in ids and l["target"] in ids for l in g["links"])
        # the right keeps its Hohfeld reading even inside a statute
        art17 = next(n for n in g["nodes"] if n["label"] == "Art. 17(1)")
        assert art17["incident"] == "privilege"

    def test_norm_to_norm_cross_act_references(self, tmp_path):
        """Expressis-verbis citations resolve clause → clause ACROSS acts when
        the cited article is itself placed; a citation naming only the
        instrument stays instrument-level (no invented precision)."""
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
        from test_legal_norm_splitter import GDPR
        from rvnd.rule_registry import RuleRegistry
        citing = ("TEST ACT (synthetic cross-reference fixture)\n\n"
                  "Article 26\nObligations of deployers\n"
                  "1. Deployers shall inform natural persons exposed to the "
                  "system; this obligation is without prejudice to Article 33 "
                  "of Regulation (EU) 2016/679.\n")
        seed_registry(tmp_path)
        reg = RuleRegistry(tmp_path, log_root=tmp_path / "log")
        reg.place_legal_text(GDPR, "gdpr", source_document="gdpr.txt")
        reg.place_legal_text(citing, "ai-act", source_document="test-act.txt")
        g = export_graph(tmp_path)
        refs = [l for l in g["links"] if l["kind"] == "refers_to"]
        assert len(refs) == 1
        lab = {n["id"]: n["label"] for n in g["nodes"]}
        assert lab[refs[0]["source"]] == "Art. 26(1)"
        assert lab[refs[0]["target"]] == "Art. 33(1)"
        assert "expressis verbis" in refs[0]["basis"]

    def test_clause_cap_respected(self, tmp_path):
        seed_registry(tmp_path)
        g = export_graph(tmp_path, max_clauses=0)
        assert not [n for n in g["nodes"] if n["group"] == "clause"]
