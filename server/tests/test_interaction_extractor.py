# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Implicit cross-instrument edge extraction — the interaction layer.

Covers: co-applicability (cross-instrument, shared topic), the NIS2-vs-DORA
temporal-conflict case (surfaced, never auto-resolved), the precedent/edge
firewall, and the confidence trap (recurrence ≠ authority promotion).
"""

import rvnd.interaction_extractor as ix


def _pair(pid, domain, summary, body, *, authority_tier=1, cited=None):
    return {
        "id": pid,
        "problem": {"id": f"{pid}-p", "scope": domain, "type": "rule",
                    "summary": summary, "facets": {"domain": domain}},
        "solution": {"id": pid, "problem_id": f"{pid}-p", "body": body,
                     "authority_tier": authority_tier,
                     "cited_sources": cited or []},
        "edges": [],
    }


# Two incident-reporting obligations from different instruments, different windows.
_NIS2 = _pair("nis2-1", "nis2",
              "nis2: obligation — operator",
              "The operator shall notify the incident within 24 hours.",
              cited=["CELEX:32022L2555 Art.23"])
_DORA = _pair("dora-1", "dora",
              "dora: obligation — entity",
              "The financial entity shall report the major incident within 4 hours.",
              cited=["CELEX:32022R2554 Art.19"])
_GDPR = _pair("gdpr-1", "gdpr",
              "gdpr: obligation — controller",
              "The controller shall notify the personal data breach within 72 hours.",
              cited=["CELEX:32016R0679 Art.33"])
# A same-topic-less pair that must NOT co-apply with the others.
_TM = _pair("tm-1", "trademark",
            "trademark: obligation — applicant",
            "The applicant shall file the opposition within the period.")


def test_co_applying_pairs_cross_instrument_shared_topic():
    pairs = [_NIS2, _DORA, _GDPR, _TM]
    combos = ix.co_applying_pairs(pairs)
    keys = {(min(a["id"], b["id"]), max(a["id"], b["id"])) for a, b, _ in combos}
    # All three incident-reporting instruments co-apply pairwise.
    assert ("dora-1", "nis2-1") in keys
    assert ("gdpr-1", "nis2-1") in keys
    # Trademark shares no topic → never co-applies.
    assert all("tm-1" not in k for k in keys)


def test_same_instrument_never_co_applies():
    a = _pair("nis2-a", "nis2", "x", "notify the incident within 24 hours")
    b = _pair("nis2-b", "nis2", "y", "report the incident within 48 hours")
    assert ix.co_applying_pairs([a, b]) == []


def test_temporal_conflict_is_escalated_never_resolved():
    [(a, b, topics)] = [(x, y, t) for x, y, t in ix.co_applying_pairs([_NIS2, _DORA])]
    p = ix.propose_edge(a, b, topics)
    assert p.predicate == "may-conflict-with"
    assert p.dimension == "temporal"
    assert p.resolution == "genuine-conflict-escalate"
    assert p.status == "pending"            # never auto-admitted
    assert p.confidence < ix.CONFIDENCE_FLOOR
    # Ship-the-law-not-the-resolution: a conflict never becomes a graph edge.
    assert p.to_graph_edge() is None


def test_cold_co_application_is_low_confidence_pending():
    # Same topic, no deadline conflict → cumulative candidate, below floor.
    a = _pair("nis2-x", "nis2", "x", "the operator shall implement security measures")
    b = _pair("dora-x", "dora", "y", "the entity shall implement security and resilience measures")
    [(pa, pb, t)] = ix.co_applying_pairs([a, b])
    p = ix.propose_edge(pa, pb, t)
    assert p.resolution == "cumulative"
    assert p.status == "pending"
    assert p.to_graph_edge() is None


def test_precedent_guides_and_admits_but_does_not_promote_authority():
    # A validated prior: nis2×dora incident-reporting co-applies cumulatively,
    # signed off at authority tier 2, seen many times.
    precedent = [{
        "id": "iaction-1",
        "domains": ["dora", "nis2"], "topics": ["incident-reporting"],
        "predicate": "co-applies-with", "dimension": "relational",
        "resolution": "cumulative", "authority_tier": 2, "recurrence": 9,
    }]
    [(a, b, t)] = [(x, y, tt) for x, y, tt in ix.co_applying_pairs([_NIS2, _DORA])]
    p = ix.propose_edge(a, b, t, interaction_nd=precedent)
    assert p.proposed_from_precedent == "iaction-1"
    assert p.authority_tier == 2            # inherited, NOT promoted by recurrence
    assert p.recurrence == 10               # consistency signal only
    assert p.status == "admitted"           # cumulative + high consistency
    edge = p.to_graph_edge()
    assert edge is not None and edge["predicate"] == "co-applies-with"
    assert edge["dimension"] == "relational"


def test_firewall_graph_edges_excludes_pending_and_conflicts():
    proposals = ix.extract_interactions([_NIS2, _DORA, _GDPR])
    edges = ix.graph_edges(proposals)
    # Every emitted edge is admitted and non-escalating.
    assert all(e["predicate"] != "may-conflict-with" for e in edges)
    # The NIS2/DORA deadline conflict is among the proposals but NOT an edge.
    assert any(p.resolution == "genuine-conflict-escalate" for p in proposals)
