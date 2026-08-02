# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The matcher — does THIS subject trigger THIS obligation?

Deterministic, LLM-free. For each obligation (carrying applicability facets from
:mod:`.applicability`) and a :class:`~workspaces.subject_card.SubjectCard`, decide
whether the obligation applies, by comparing facet sets through the domain's
**subsumption taxonomy** — not string equality.

The match question is genus/species: a system tagged ``employment`` satisfies an
obligation keyed to ``high-risk`` because ``employment`` IS-A high-risk class
(``DomainVocabulary.subsumption``). So the matcher walks the structural taxonomy,
exactly the structural dimension of the 5D edge model.

Three-valued result — never a silent boolean:

  APPLIES        every trigger facet is positively satisfied by the card.
  MAY_APPLY      a trigger facet is UNKNOWN on the card, or the card is at a
                 boundary — surfaced for the human, not silently in/out.
  NOT_TRIGGERED  a trigger facet is positively contradicted by the card.

This is the architectural form of "ship the law, never the resolution": the
uncertain cases route to oversight; the engine never resolves them itself.
Each verdict names the deciding facet (why) so it is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

from .subject_card import SubjectCard, DomainVocabulary, get_vocabulary, UNKNOWN


class Match(str, Enum):
    APPLIES = "applies"
    MAY_APPLY = "may-apply"
    NOT_TRIGGERED = "not-triggered"


@dataclass
class FacetVerdict:
    facet: str
    trigger_value: Any
    subject_value: Any
    result: Match
    reason: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self); d["result"] = self.result.value
        return d


@dataclass
class ObligationMatch:
    """The matcher's verdict on one obligation against the card."""
    pair_id: str
    result: Match
    bearer: str
    action: str
    operator: str
    source: str                       # source_document / eId / citation
    facet_verdicts: list[FacetVerdict] = field(default_factory=list)
    missing_facets: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["result"] = self.result.value
        d["facet_verdicts"] = [v.to_dict() for v in self.facet_verdicts]
        return d


def _match_one_facet(
    name: str, trigger_value: Any, card: SubjectCard, vocab: DomainVocabulary,
) -> FacetVerdict:
    """Compare one trigger facet against the card, via subsumption.

    The obligation is satisfied on this facet iff some subject value subsumes
    (is-a, transitively) the trigger value — i.e. the trigger value is an
    ancestor of a subject value, or equals it.
    """
    subj = card.get(name)
    trig_vals = trigger_value if isinstance(trigger_value, (list, tuple)) else [trigger_value]

    if subj is UNKNOWN:
        return FacetVerdict(name, trigger_value, UNKNOWN, Match.MAY_APPLY,
                            f"subject's {name!r} is unknown")

    subj_vals = subj if isinstance(subj, (list, tuple)) else [subj]
    # subject satisfies trigger if any subject value subsumes any trigger value
    for sv in subj_vals:
        ancestors = vocab.ancestors(sv)            # {sv, parents...}
        for tv in trig_vals:
            if tv in ancestors:
                return FacetVerdict(name, trigger_value, subj, Match.APPLIES,
                                    f"{sv!r} is-a {tv!r}" if sv != tv else f"{sv!r} matches")
    # no subject value reaches any trigger value → positively excluded
    return FacetVerdict(name, trigger_value, subj, Match.NOT_TRIGGERED,
                        f"subject {subj!r} is not / not-a {trigger_value!r}")


def match_obligation(pair: dict[str, Any], card: SubjectCard,
                     vocab: DomainVocabulary) -> ObligationMatch:
    """Match one enriched obligation pair against the card.

    Combination rule across facets:
      - any facet NOT_TRIGGERED  → the whole obligation is NOT_TRIGGERED
        (a positively-contradicted trigger means it does not bind this subject).
      - else any facet MAY_APPLY → MAY_APPLY (an unknown blocks certainty).
      - else (all APPLIES, or no trigger facets at all) → APPLIES.

    An obligation with NO applicability facets applies unconditionally (it
    constrains no dimension) → APPLIES, the conservative legal default.
    """
    sol = pair.get("solution") or {}
    prob = pair.get("problem") or {}
    trigger = pair.get("applicability") or {}

    verdicts = [_match_one_facet(name, val, card, vocab)
                for name, val in trigger.items()]

    results = {v.result for v in verdicts}
    if Match.NOT_TRIGGERED in results:
        overall = Match.NOT_TRIGGERED
    elif Match.MAY_APPLY in results:
        overall = Match.MAY_APPLY
    else:
        overall = Match.APPLIES

    missing = [v.facet for v in verdicts if v.result is Match.MAY_APPLY]

    # confidence: 1.0 when fully determined; decays with unknown facets.
    base = float(sol.get("confidence", 0.7) or 0.7)
    conf = base if overall is not Match.MAY_APPLY else round(base * (0.6 ** max(1, len(missing))), 3)

    return ObligationMatch(
        pair_id=str(pair.get("id", "")),
        result=overall,
        bearer=sol.get("bearer", prob.get("facets", {}).get("bearer", "")),
        action=sol.get("action", ""),
        operator=sol.get("operator", ""),
        source=str(prob.get("source_document")
                   or sol.get("source_eId")
                   or (sol.get("cited_sources") or [""])[0]
                   or prob.get("scope", "")),
        facet_verdicts=verdicts,
        missing_facets=missing,
        confidence=conf,
    )


@dataclass
class AssessmentResult:
    """All obligation matches for one subject, grouped by verdict."""
    domain: str
    subject_id: str
    applies: list[ObligationMatch] = field(default_factory=list)
    may_apply: list[ObligationMatch] = field(default_factory=list)
    not_triggered: list[ObligationMatch] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "subject_id": self.subject_id,
            "applies": [m.to_dict() for m in self.applies],
            "may_apply": [m.to_dict() for m in self.may_apply],
            "not_triggered": [m.to_dict() for m in self.not_triggered],
        }


def assess(pairs: list[dict[str, Any]], card: SubjectCard,
           *, vocab: Optional[DomainVocabulary] = None) -> AssessmentResult:
    """Match every obligation pair against the card and bucket the verdicts.

    ``pairs`` should already be enriched (applicability facets attached); if a
    pair has no ``applicability`` key it is treated as unconditional.
    """
    vocab = vocab or get_vocabulary(card.domain)
    if vocab is None:
        raise ValueError(f"no vocabulary for domain {card.domain!r}")
    res = AssessmentResult(domain=card.domain, subject_id=card.subject_id)
    for p in pairs:
        m = match_obligation(p, card, vocab)
        if m.result is Match.APPLIES:
            res.applies.append(m)
        elif m.result is Match.MAY_APPLY:
            res.may_apply.append(m)
        else:
            res.not_triggered.append(m)
    return res
