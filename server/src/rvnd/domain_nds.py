# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Concrete domain NDs that turn normative classifications into typed pairs.

Each ND subclasses :class:`BaseNDDispatcher`, registers for ``handles_types
= ["normative"]`` and one or more domain facets, and produces structured
``ProblemSolutionPair`` dicts whose ``solution.body`` contains the extracted
operative structure from :func:`extract_rules`.

The shipped NDs are the minimum useful set for the target working domains:

- :class:`GDPRRuleND`         — fires on ``gdpr`` facet
- :class:`AIActRuleND`        — fires on ``ai-act`` facet
- :class:`MusicRightsRuleND`  — fires on ``music-rights`` facet
- :class:`ContractRuleND`     — generic catch-all when no domain facet
                                 matched but the type is normative

Add more by subclassing :class:`BaseNDDispatcher` directly — these are
just convenience wirings, not load-bearing API.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any

from .nd_routing import BaseNDDispatcher, Classification
from .rule_extractor import RuleFacet, extract_rules
from rvnd.adapters.solver.dimensions import Dimension
from .math_extractor import extract_math
from .oversight_extractor import (
    OversightFacet, extract_oversight, render_oversight)


def _hash_pair(content: str, nd_id: str, source: str | None) -> str:
    h = hashlib.sha256()
    h.update(nd_id.encode("utf-8"))
    h.update(b"|")
    h.update((source or "inline").encode("utf-8"))
    h.update(b"|")
    h.update(content.encode("utf-8"))
    return "sha256:" + h.hexdigest()[:32]


def _edge(subject: str, predicate: str, obj: str, dimension: Dimension) -> dict[str, Any]:
    """A typed KG edge carrying its reasoning dimension (see rvnd.dimensions)."""
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "dimension": dimension.value,
    }


def _build_pairs(
    nd_id: str,
    scope: str,
    domain: str,
    authority_tier: int,
    content: str,
    classification: Classification,
    source_document: str | None,
) -> list[dict[str, Any]]:
    """Run ``extract_rules`` and project each rule to a Problem/Solution pair.

    When no rule is extracted by the regex floor, we still emit one
    "umbrella" pair with the full content and the classification's signals
    so the audit floor is preserved.
    """
    rules = extract_rules(content)
    pair_id_base = _hash_pair(content, nd_id, source_document)

    if not rules:
        return [{
            "id": pair_id_base,
            "problem": {
                "id": f"{pair_id_base}-p",
                "scope": scope,
                "type": "normative-untyped",
                "summary": f"{domain}: normative content without extracted rule",
                "facets": {
                    "domain": domain,
                    "normative_signals": classification.metadata.get(
                        "normative_signals", {}
                    ),
                    "primary_type": classification.primary_type,
                },
            },
            "solution": {
                "id": pair_id_base,
                "problem_id": f"{pair_id_base}-p",
                "body": content[:2000],
                "body_format": "prose",
                "authority_tier": authority_tier,
                "confidence": classification.confidence,
            },
            "edges": [_edge(pair_id_base, "belongs-to", domain, Dimension.STRUCTURAL)],
        }]

    out: list[dict[str, Any]] = []
    for idx, rule in enumerate(rules):
        pid = f"{pair_id_base}-r{idx}"
        out.append({
            "id": pid,
            "problem": {
                "id": f"{pid}-p",
                "scope": scope,
                "type": "rule",
                "summary": f"{domain}: {rule.modal} — {rule.subject}",
                "facets": {
                    "domain": domain,
                    "subject": rule.subject,
                    "modal": rule.modal,
                    "modal_phrase": rule.modal_phrase,
                    "language": rule.language,
                    "has_condition": bool(rule.condition),
                    "has_exception": bool(rule.exception),
                },
            },
            "solution": {
                "id": pid,
                "problem_id": f"{pid}-p",
                "body": _render_rule(rule),
                "body_format": "structured-rule",
                "authority_tier": authority_tier,
                "confidence": rule.confidence,
                "rule": asdict(rule),
            },
            "edges": _rule_edges(pid, domain, rule),
        })
    return out


def _rule_edges(pid: str, domain: str, rule: RuleFacet) -> list[dict[str, Any]]:
    """Dimensioned edges for a normative rule.

    A rule belongs to a domain (structural), governs a subject — that is its
    purpose (intentional), and applies under a condition that triggers it
    (causal). The domain ND assigns these dimensions because it knows what
    its edges mean; the generic classifier is only a fallback.
    """
    edges = [_edge(pid, "belongs-to", domain, Dimension.STRUCTURAL)]
    if rule.subject:
        edges.append(_edge(pid, "governs", rule.subject, Dimension.INTENTIONAL))
    if rule.condition:
        edges.append(_edge(pid, "applies-when", rule.condition, Dimension.CAUSAL))
    return edges


def _render_rule(rule: RuleFacet) -> str:
    """Human-readable rendering of the structured rule."""
    parts = [
        f"SUBJECT: {rule.subject}",
        f"MODAL:   {rule.modal} ({rule.modal_phrase})",
        f"ACTION:  {rule.action}",
    ]
    if rule.condition:
        parts.append(f"COND:    {rule.condition}")
    if rule.exception:
        parts.append(f"EXCEPT:  {rule.exception}")
    parts.append(f"LANG:    {rule.language}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Concrete NDs
# ---------------------------------------------------------------------------


class GDPRRuleND(BaseNDDispatcher):
    """ND for GDPR / BDSG operative content."""

    nd_id = "nd-gdpr"
    # Fire on the GDPR FACET only, not on bare "normative" — otherwise every
    # domain ND claims every normative doc (the ×4 over-fire). The deontic /
    # decision / cross-ref NDs still cover normative content generally.
    handles_types: list[str] = []
    handles_facets = ["gdpr"]
    confidence_floor = 0.45

    def extract(self, content, classification, *, source_document=None):
        return _build_pairs(
            self.nd_id, "gdpr", "gdpr", authority_tier=1,
            content=content, classification=classification,
            source_document=source_document,
        )


class AIActRuleND(BaseNDDispatcher):
    """ND for EU AI Act operative content."""

    nd_id = "nd-ai-act"
    handles_types: list[str] = []   # fire on the ai-act facet only (see GDPRRuleND)
    handles_facets = ["ai-act"]
    confidence_floor = 0.45

    def extract(self, content, classification, *, source_document=None):
        return _build_pairs(
            self.nd_id, "ai-act", "ai-act", authority_tier=1,
            content=content, classification=classification,
            source_document=source_document,
        )


class MusicRightsRuleND(BaseNDDispatcher):
    """ND for UrhG / DSM / publishing / mechanical-royalty operative content."""

    nd_id = "nd-music-rights"
    handles_types: list[str] = []   # fire on the music-rights facet only (see GDPRRuleND)
    handles_facets = ["music-rights"]
    confidence_floor = 0.45

    def extract(self, content, classification, *, source_document=None):
        return _build_pairs(
            self.nd_id, "music-rights", "music-rights", authority_tier=2,
            content=content, classification=classification,
            source_document=source_document,
        )


class ContractRuleND(BaseNDDispatcher):
    """Generic ND for operative contract clauses with no domain facet.

    Fires on normative content where none of the domain-specific NDs claim
    the document. Lower confidence floor than the domain NDs because it's
    the catch-all.
    """

    nd_id = "nd-contracts"
    handles_types: list[str] = []
    handles_facets = ["contracts"]
    confidence_floor = 0.45

    # The other domain facets — if any of these is present, a domain ND owns the
    # doc and the contracts fallback stays silent (no more wrong-scope ×N).
    _DOMAIN_FACETS = ("gdpr", "ai-act", "music-rights")

    def can_handle(self, classification) -> bool:
        if classification.confidence < self.confidence_floor:
            return False
        # Explicit contracts facet → fire.
        if "contracts" in classification.facets:
            return True
        # True fallback: normative content that no domain facet claimed.
        if classification.primary_type == "normative" and not any(
                f in classification.facets for f in self._DOMAIN_FACETS):
            return True
        return False

    def extract(self, content, classification, *, source_document=None):
        return _build_pairs(
            self.nd_id, "contracts", "contracts", authority_tier=3,
            content=content, classification=classification,
            source_document=source_document,
        )


def _build_math_pairs(
    nd_id: str,
    content: str,
    classification: Classification,
    source_document: str | None,
) -> list[dict[str, Any]]:
    """Project mathematical content to typed, dimensioned problem/solution pairs."""
    problems = extract_math(content)
    pair_id_base = _hash_pair(content, nd_id, source_document)

    if not problems:
        return [{
            "id": pair_id_base,
            "problem": {
                "id": f"{pair_id_base}-p",
                "scope": "math",
                "type": "math-untyped",
                "summary": "math content without extracted structure",
                "facets": {"domain": "math", "primary_type": classification.primary_type},
            },
            "solution": {
                "id": pair_id_base,
                "problem_id": f"{pair_id_base}-p",
                "body": content[:2000],
                "body_format": "prose",
                "authority_tier": 1,
                "confidence": classification.confidence,
            },
            "edges": [_edge(pair_id_base, "in-domain", "math", Dimension.STRUCTURAL)],
        }]

    out: list[dict[str, Any]] = []
    for idx, prob in enumerate(problems):
        pid = f"{pair_id_base}-m{idx}"
        out.append({
            "id": pid,
            "problem": {
                "id": f"{pid}-p",
                "scope": "math",
                "type": "math-problem",
                "summary": prob.statement[:200],
                "facets": {
                    "domain": prob.domain,
                    "given": prob.given,
                    "find": prob.find,
                },
            },
            "solution": {
                "id": pid,
                "problem_id": f"{pid}-p",
                "body": "\n".join(prob.steps) if prob.steps else prob.statement,
                "body_format": prob.body_format,
                "authority_tier": 1,
                "confidence": prob.confidence,
            },
            "edges": _math_edges(pid, prob),
        })
    return out


def _math_edges(pid, prob) -> list[dict[str, Any]]:
    """Dimensioned edges for a math problem.

    The problem sits in a domain and is built from its givens (structural); it
    aims at a goal (intentional); the proof unfolds step by step (temporal) and
    the steps conclude the goal (causal).
    """
    edges = [_edge(pid, "in-domain", prob.domain, Dimension.STRUCTURAL)]
    for g in prob.given:
        edges.append(_edge(pid, "given", g, Dimension.STRUCTURAL))
    if prob.find:
        edges.append(_edge(pid, "find", prob.find, Dimension.INTENTIONAL))
    for step in prob.steps[:20]:
        edges.append(_edge(pid, "then", step, Dimension.TEMPORAL))
    if prob.find and prob.steps:
        edges.append(_edge(pid, "concludes", prob.find, Dimension.CAUSAL))
    return edges


class MathND(BaseNDDispatcher):
    """ND for mathematical content — theorems, proofs, and worked problems."""

    nd_id = "nd-math"
    handles_types = ["math"]
    handles_facets: list[str] = []
    confidence_floor = 0.25

    def extract(self, content, classification, *, source_document=None):
        return _build_math_pairs(
            self.nd_id, content=content, classification=classification,
            source_document=source_document,
        )


def _build_oversight_pairs(
    nd_id: str,
    content: str,
    classification: Classification,
    source_document: str | None,
) -> list[dict[str, Any]]:
    """Project oversight requirements to typed, dimensioned pairs.

    Emits NOTHING when no oversight signal fires — the Rule NDs already
    preserve the audit floor with umbrella pairs; Oversight ND never
    duplicates content it cannot type.
    """
    facets = extract_oversight(content)
    if not facets:
        return []
    pair_id_base = _hash_pair(content, nd_id, source_document)
    tier = 1 if any(f in ("ai-act", "gdpr")
                    for f in classification.facets) else 2

    out: list[dict[str, Any]] = []
    for idx, facet in enumerate(facets):
        pid = f"{pair_id_base}-o{idx}"
        out.append({
            "id": pid,
            "problem": {
                "id": f"{pid}-p",
                "scope": "oversight",
                "type": "oversight-requirement",
                "summary": (f"oversight: floor {facet.min_level or '—'} — "
                            f"{facet.overseer or 'unspecified overseer'}"),
                "facets": {
                    "domain": "oversight",
                    "min_level": facet.min_level,
                    "grade_ceiling": facet.grade_ceiling,
                    "personal": facet.personal,
                    "language": facet.language,
                    "has_cadence": bool(facet.cadence),
                    "has_measure": bool(facet.measure),
                },
            },
            "solution": {
                "id": pid,
                "problem_id": f"{pid}-p",
                "body": render_oversight(facet),
                "body_format": "structured-rule",
                "authority_tier": tier,
                "confidence": facet.confidence,
                "oversight": asdict(facet),
            },
            "edges": _oversight_edges(pid, facet),
        })
    return out


def _oversight_edges(pid: str, facet: OversightFacet) -> list[dict[str, Any]]:
    """Dimensioned edges for an oversight requirement.

    The requirement sits in the oversight domain (structural); it names who
    watches (intentional); it engages under a condition (causal) and recurs
    on a cadence (temporal)."""
    edges = [_edge(pid, "belongs-to", "oversight", Dimension.STRUCTURAL)]
    if facet.overseer:
        edges.append(_edge(pid, "overseen-by", facet.overseer,
                           Dimension.INTENTIONAL))
    if facet.min_level:
        edges.append(_edge(pid, "floors-level", facet.min_level,
                           Dimension.INTENTIONAL))
    if facet.grade_ceiling:
        edges.append(_edge(pid, "caps-grade", facet.grade_ceiling,
                           Dimension.INTENTIONAL))
    if facet.trigger:
        edges.append(_edge(pid, "engages-when", facet.trigger,
                           Dimension.CAUSAL))
    if facet.cadence:
        edges.append(_edge(pid, "recurs", facet.cadence, Dimension.TEMPORAL))
    return edges


class OversightND(BaseNDDispatcher):
    """ND for oversight requirements — who must watch, at what level.

    Complement of the Rule NDs: fires on the same normative content but
    extracts the oversight dimension (floors, ceilings, overseers, measures,
    cadences, non-delegability) rather than the operative rule."""

    nd_id = "nd-oversight"
    handles_types = ["normative"]
    handles_facets = ["oversight"]
    confidence_floor = 0.45

    def extract(self, content, classification, *, source_document=None):
        return _build_oversight_pairs(
            self.nd_id, content=content, classification=classification,
            source_document=source_document,
        )


def register_default_domain_nds(router) -> None:
    """Register the default NDs on a router.

    Convenience helper for the common "give me the full domain suite" wiring.
    Callers wanting selective registration should instantiate + register
    individual NDs directly.
    """
    router.register(GDPRRuleND())
    router.register(AIActRuleND())
    router.register(MusicRightsRuleND())
    router.register(ContractRuleND())
    router.register(MathND())
    router.register(OversightND())
