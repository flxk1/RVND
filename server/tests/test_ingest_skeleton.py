# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Ingestion-plane skeleton: dispatch, the policy ingester, and the writer seam.

Run: python -m pytest server/tests/test_ingest_skeleton.py -q
"""
from __future__ import annotations

from workspaces.ingest import (
    CollectingWriter,
    Subgraph,
    default_registry,
    ingest_text,
    versum_writer,
)
from workspaces.ingest import IngesterRegistry
from workspaces.ingest import Ingester

_POLICY = ("A human must approve every automated refund over 500 EUR. "
           "Users may appeal a decision within 14 days.")
# A judgment carries ≥3 co-occurring court-decision markers (jurisdiction-pack
# data); the policy mapper quarantines it — a holding interprets norms, it does
# not enact them, so nothing is lowered into the graph.
_CASE_LAW = ("Bundesgerichtshof, Urteil vom 12. Mai. Die Revision wird "
             "zurückgewiesen. Der Leitsatz: die Klausel ist unwirksam. "
             "Tatbestand und Entscheidungsgründe folgen.")


def test_policy_ingester_satisfies_protocol():
    from workspaces.ingest.policy import PolicyIngester
    assert isinstance(PolicyIngester(), Ingester)


def test_dispatch_routes_governance_policy_to_policy_ingester():
    reg = default_registry()
    ing = reg.dispatch(_POLICY)
    assert ing is not None and ing.id == "policy"


def test_dispatch_routes_general_normative_text_to_deontic_ingester():
    reg = default_registry()
    ing = reg.dispatch("Controller must notify the operator.")
    assert ing is not None and ing.id == "deontic"


def test_grammar_ingester_wins_over_fallback():
    """A registered grammar predicate is dispatched ahead of the best-guess
    fallback; governance and deontic grammars retain their own inputs."""
    reg = default_registry()

    class _ClaimIngester:
        id = "claim"

        def grammar(self):
            return lambda t: t.strip().lower().startswith("claim:")

        def ingest(self, text, ctx):
            return Subgraph(dimension="5D")

    reg.register(_ClaimIngester())
    assert reg.dispatch("claim: water boils at 100C").id == "claim"
    assert reg.dispatch(_POLICY).id == "policy"


def test_policy_lowers_to_an_nd_subgraph():
    from workspaces.ingest.policy import PolicyIngester
    sg = PolicyIngester().ingest(_POLICY, {})
    assert isinstance(sg, Subgraph)
    assert sg.dimension == "nD"
    assert not sg.quarantined
    assert sg.nodes, "a policy with an obligation projects at least one node"
    assert sg.provenance["ingester"] == "policy"
    assert sg.provenance["language_chain"] == {
        "governance": {
            "package": "loomground-governance",
            "version": sg.provenance["language_chain"]["governance"]["version"],
            "status": "stable",
            "role": "authoritative policy grammar and vocabulary",
        },
        "deontic": {
            "package": "loomground-deontic",
            "version": sg.provenance["language_chain"]["deontic"]["version"],
            "status": "draft",
            "role": "normative classification",
            "recognised": 2,
            "lowered": 2,
            "rejected": 0,
        },
    }


def test_pipeline_writes_deterministically():
    reg = default_registry()
    w1, w2 = CollectingWriter(), CollectingWriter()
    r1 = ingest_text(_POLICY, registry=reg, writer=w1)
    r2 = ingest_text(_POLICY, registry=reg, writer=w2)
    assert r1["ok"] and r1["ingester"] == "policy" and r1["dimension"] == "nD"
    assert r1["write"]["written"] is True
    # LLM off ⇒ same text yields the same subgraph
    assert r1 == r2
    assert len(w1.written) == 1


def test_case_law_is_quarantined_not_written():
    reg = default_registry()
    w = CollectingWriter()
    r = ingest_text(_CASE_LAW, registry=reg, writer=w)
    assert r["ok"] is False and r["reason"] == "quarantined"
    assert r["quarantined"] is True
    assert r["write"]["written"] is False
    assert r["write"]["reason"] == "quarantined"
    assert w.written == []


def test_unclaimed_text_reports_no_ingester():
    # An empty registry claims nothing; dispatch returns None, no guessed write.
    reg = IngesterRegistry()
    r = ingest_text("plain descriptive prose with no modal verbs", registry=reg,
                    writer=CollectingWriter())
    assert r == {"ok": False, "reason": "no_ingester"}


def test_versum_writer_is_the_live_host_injected_seam():
    assert callable(versum_writer)
