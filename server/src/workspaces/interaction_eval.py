# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Eval gold-set harness for the interaction layer.

The concept's key observation: *labelling a conflict IS admitting an
interaction pair* — so the eval gold-set and the interaction-ND seed are the
same artifact. This module both:

  1. measures the interaction extractor's retrieve-and-propose against a
     held-out gold-set (precision / recall / F1 on co-applicability detection
     and on predicate correctness), and
  2. converts a gold-set into interaction-ND precedent records
     (``gold_to_interaction_nd``) — the seed that guides future proposals.

A gold item is a human-validated relationship between two obligation pairs:

    {"pair_a": <id>, "pair_b": <id>,
     "domains": [...], "topics": [...],
     "predicate": "co-applies-with" | "may-conflict-with" | "supersedes",
     "resolution": "cumulative" | "a-overrides-b" | "genuine-conflict-escalate",
     "dimension": "relational" | "temporal" | ...,
     "authority_tier": 1}

Train/test split: train items seed the interaction ND (precedents); the
extractor is then run over the test pairs and scored against the test labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional

from .interaction_extractor import extract_interactions, InteractionProposal


def _key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def gold_to_interaction_nd(gold: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project gold labels into interaction-ND precedent records.

    Identical instrument-pair + topic labels collapse into one precedent whose
    ``recurrence`` counts how many times it was validated — the consistency
    signal (which, per the confidence trap, never promotes authority)."""
    by_key: dict[tuple, dict[str, Any]] = {}
    for g in gold:
        domains = tuple(sorted(g["domains"]))
        topics = tuple(sorted(g["topics"]))
        k = (domains, topics, g["predicate"], g["resolution"])
        if k not in by_key:
            by_key[k] = {
                "id": f"seed-{len(by_key)+1}",
                "domains": list(domains), "topics": list(topics),
                "predicate": g["predicate"], "resolution": g["resolution"],
                "dimension": g.get("dimension", "relational"),
                "authority_tier": int(g.get("authority_tier", 2)),
                "recurrence": 0,
            }
        by_key[k]["recurrence"] += 1
    return list(by_key.values())


@dataclass
class EvalResult:
    n_gold: int = 0
    n_proposed_on_gold: int = 0      # gold pairs we produced any proposal for
    n_correct_predicate: int = 0     # …and the predicate matched
    misses: list[tuple[str, str]] = field(default_factory=list)
    wrong_predicate: list[dict[str, Any]] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.n_proposed_on_gold / self.n_gold if self.n_gold else 0.0

    @property
    def precision(self) -> float:
        # of the gold pairs we proposed on, how many predicates were right
        return (self.n_correct_predicate / self.n_proposed_on_gold
                if self.n_proposed_on_gold else 0.0)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(recall=round(self.recall, 3), precision=round(self.precision, 3),
                 f1=round(self.f1, 3))
        return d


def evaluate(
    pairs: list[dict[str, Any]], gold: list[dict[str, Any]],
    *, interaction_nd: Optional[Iterable[dict[str, Any]]] = None,
) -> EvalResult:
    """Run the extractor over ``pairs`` and score proposals against ``gold``.

    ``interaction_nd`` (typically the train split via ``gold_to_interaction_nd``)
    guides retrieval. Recall = gold pairs we proposed any edge for; precision =
    of those, the share whose predicate matched the gold label."""
    proposals = extract_interactions(pairs, interaction_nd=interaction_nd)
    by_pair: dict[tuple[str, str], InteractionProposal] = {
        _key(p.subject, p.object): p for p in proposals}

    res = EvalResult(n_gold=len(gold))
    for g in gold:
        k = _key(g["pair_a"], g["pair_b"])
        prop = by_pair.get(k)
        if prop is None:
            res.misses.append(k)
            continue
        res.n_proposed_on_gold += 1
        if prop.predicate == g["predicate"]:
            res.n_correct_predicate += 1
        else:
            res.wrong_predicate.append(
                {"pair": k, "got": prop.predicate, "expected": g["predicate"]})
    return res
