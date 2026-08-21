# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Eval gold-set harness — measures retrieve-and-propose; seeds the interaction ND."""

import rvnd.interaction_eval as ev


def _pair(pid, domain, body):
    return {"id": pid, "problem": {"id": f"{pid}-p", "scope": domain,
                                   "facets": {"domain": domain}, "summary": ""},
            "solution": {"id": pid, "body": body, "authority_tier": 1}}


# Pool: incident-reporting obligations from three instruments.
_PAIRS = [
    _pair("nis2", "nis2", "the operator shall notify the incident within 24 hours"),
    _pair("dora", "dora", "the entity shall report the major incident within 4 hours"),
    _pair("gdpr", "gdpr", "the controller shall notify the breach within 72 hours"),
]

# Human-validated labels (the gold-set = the interaction-ND seed).
_GOLD = [
    {"pair_a": "nis2", "pair_b": "dora", "domains": ["nis2", "dora"],
     "topics": ["incident-reporting"], "predicate": "may-conflict-with",
     "resolution": "genuine-conflict-escalate", "dimension": "temporal",
     "authority_tier": 1},
    {"pair_a": "gdpr", "pair_b": "dora", "domains": ["gdpr", "dora"],
     "topics": ["incident-reporting"], "predicate": "may-conflict-with",
     "resolution": "genuine-conflict-escalate", "dimension": "temporal",
     "authority_tier": 1},
    {"pair_a": "gdpr", "pair_b": "nis2", "domains": ["gdpr", "nis2"],
     "topics": ["incident-reporting"], "predicate": "may-conflict-with",
     "resolution": "genuine-conflict-escalate", "dimension": "temporal",
     "authority_tier": 1},
]


def test_gold_to_interaction_nd_seeds_with_recurrence():
    nd = ev.gold_to_interaction_nd(_GOLD + _GOLD)   # each label seen twice
    # 3 distinct (domains, topics, predicate, resolution) precedents.
    assert len(nd) == 3
    assert all(r["recurrence"] == 2 for r in nd)
    assert all(r["predicate"] == "may-conflict-with" for r in nd)
    assert all(r["authority_tier"] == 1 for r in nd)


def test_evaluate_cold_detects_all_and_predicts_conflicts():
    # No precedents: the deadline-conflict heuristic alone should detect the
    # three differing-deadline pairs and label them may-conflict-with.
    res = ev.evaluate(_PAIRS, _GOLD, interaction_nd=None)
    assert res.n_gold == 3
    assert res.recall == 1.0                 # all three co-applying pairs proposed
    assert res.precision == 1.0              # all predicted may-conflict-with
    assert res.f1 == 1.0


def test_evaluate_reports_misses_and_wrong_predicates():
    # A gold pair that does NOT co-apply (no shared topic) → a miss.
    pairs = _PAIRS + [_pair("tm", "trademark", "the applicant shall file the opposition")]
    gold = _GOLD + [
        {"pair_a": "tm", "pair_b": "gdpr", "domains": ["trademark", "gdpr"],
         "topics": ["incident-reporting"], "predicate": "co-applies-with",
         "resolution": "cumulative", "authority_tier": 2}]
    res = ev.evaluate(pairs, gold)
    assert ("gdpr", "tm") in res.misses
    assert res.recall < 1.0                  # the trademark pair was not detected


def test_precision_recall_math():
    # Hand-checked: 4 gold, 3 proposed-on-gold, 3 correct.
    res = ev.EvalResult(n_gold=4, n_proposed_on_gold=3, n_correct_predicate=3)
    assert round(res.recall, 3) == 0.75
    assert round(res.precision, 3) == 1.0
    assert round(res.f1, 3) == round(2 * 1.0 * 0.75 / 1.75, 3)
