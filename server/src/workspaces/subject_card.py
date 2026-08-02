# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Subject card — the structured description of the thing being assessed.

The instrument side (the AI Act) is already typed obligation pairs. To produce a
"where does my system sit" memo, the *subject* side must be typed too, in the
SAME controlled vocabulary the obligations key on — otherwise matching is
prose-against-prose guesswork.

EU regulation is written as **role × artifact-class × activity** (Article 6,
Annex III, and the obligation articles all key on those). The card mirrors that
shape, so matching becomes set-against-set (deterministic, auditable) rather
than a model deciding "does this apply".

This module is domain-parameterised. The AI Act vocabulary ships here as the
first instance (:data:`AI_ACT_VOCAB`); other domains (GDPR processing activity,
a contract, a trademark) register their own vocabulary and reuse the same card
shape + the matcher unchanged. That is what makes the memo generalise.

The card is also the single place a human corrects the assessment, and the
record the audit log pins as "what we assessed".
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# Sentinel for "the user did not tell us this facet". Distinct from a facet that
# is positively false — the three-valued matcher treats UNKNOWN as "may apply",
# a positive False as "not triggered". Never collapse the two.
UNKNOWN = None


@dataclass
class FacetSpec:
    """One facet the subject can carry, and the values it may take."""
    name: str
    values: tuple[str, ...]          # the controlled value set
    description: str = ""
    multi: bool = False              # may the subject hold several values at once


@dataclass
class DomainVocabulary:
    """The controlled facet vocabulary for one regulatory domain.

    ``subsumption`` is the structural taxonomy: child -> parent value pairs, so
    a subject tagged with a narrow class (Annex-III-4(a)) satisfies an
    obligation keyed to a broad class (high-risk). The matcher walks this.
    """
    domain: str
    facets: tuple[FacetSpec, ...]
    subsumption: tuple[tuple[str, str], ...] = ()   # (child_value, parent_value)

    def facet(self, name: str) -> Optional[FacetSpec]:
        return next((f for f in self.facets if f.name == name), None)

    def ancestors(self, value: str) -> set[str]:
        """All values ``value`` subsumes under (including itself), transitively."""
        seen = {value}
        frontier = [value]
        while frontier:
            cur = frontier.pop()
            for child, parent in self.subsumption:
                if child == cur and parent not in seen:
                    seen.add(parent)
                    frontier.append(parent)
        return seen


@dataclass
class SubjectCard:
    """A typed description of the subject under assessment.

    Capture-first: a card is valid with **zero facets**. The minimum is some
    free description / PoC notes / contact — anything the user has. Typed facets
    narrow the assessment; their absence does not block it (every unset facet is
    UNKNOWN → "may apply", so an empty card yields a memo that is mostly "here is
    what I need from you to narrow this down"). This matches how intake really
    happens: a user often has a description and a contact long before they can
    answer "are you the provider or the deployer".

    Fields:
      facets       typed, in-vocabulary facets (may be empty).
      description  the free-text system description / PoC summary.
      notes        anything that did not map to a facet — kept verbatim, never
                   discarded, so nothing the user typed is lost.
      contact      point-of-contact info (name/email/team) for follow-up.
      attachments  paths/ids of any PoC files, specs, screenshots stored.
    """
    domain: str
    facets: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    notes: str = ""
    contact: str = ""
    attachments: list[str] = field(default_factory=list)
    subject_id: str = ""

    def get(self, name: str) -> Any:
        return self.facets.get(name, UNKNOWN)

    def is_empty(self) -> bool:
        """True when no typed facets are set (assessment will be all may-apply)."""
        return not self.facets

    def completeness(self) -> float:
        """Fraction of the domain's facets that are filled — a UI progress hint."""
        vocab = get_vocabulary(self.domain)
        total = len(vocab.facets) if vocab else 0
        return round(len(self.facets) / total, 2) if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# AI Act vocabulary (first domain instance)
# ---------------------------------------------------------------------------

AI_ACT_VOCAB = DomainVocabulary(
    domain="ai-act",
    facets=(
        FacetSpec("role",
                  ("provider", "deployer", "importer", "distributor",
                   "authorised-representative", "product-manufacturer"),
                  "Which actor in the AI value chain the subject is."),
        FacetSpec("risk_tier",
                  ("prohibited", "high-risk", "limited-risk", "minimal-risk", "gpai"),
                  "The AI Act risk classification of the system."),
        FacetSpec("annex_iii_area",
                  ("biometrics", "critical-infrastructure", "education",
                   "employment", "essential-services", "law-enforcement",
                   "migration-border", "justice-democracy"),
                  "Annex III high-risk use-case area, if any.", multi=True),
        FacetSpec("gpai", ("yes", "no"),
                  "Is the system a general-purpose AI model?"),
        FacetSpec("processes_personal_data", ("yes", "no"),
                  "Does the system process personal data (pulls in GDPR)."),
        FacetSpec("deployment_context",
                  ("public-authority", "workplace", "law-enforcement",
                   "private-commercial", "research"),
                  "Where/how the system is deployed.", multi=True),
    ),
    # Structural taxonomy. Each Annex III area IS-A high-risk class; gpai is its
    # own tier. This is the genus/species relation the matcher subsumes over.
    subsumption=(
        ("biometrics", "high-risk"),
        ("critical-infrastructure", "high-risk"),
        ("education", "high-risk"),
        ("employment", "high-risk"),
        ("essential-services", "high-risk"),
        ("law-enforcement", "high-risk"),
        ("migration-border", "high-risk"),
        ("justice-democracy", "high-risk"),
    ),
)


# ---------------------------------------------------------------------------
# Neutral vocabulary — the instrument-AGNOSTIC facet NAMES that fit ANY policy.
# Values are representative supersets (extensible; make_card is lenient); an
# instrument pack (AI_ACT_VOCAB, a GDPR pack) is the per-domain specialisation.
# `category` is the escalated axis — a determination Rvnd carries, never makes.
# ---------------------------------------------------------------------------
NEUTRAL_VOCAB = DomainVocabulary(
    domain="neutral",
    facets=(
        FacetSpec("role",
                  ("provider", "deployer", "importer", "distributor", "authorised-representative",
                   "product-manufacturer", "controller", "processor", "operator", "employer",
                   "licensee", "supplier", "other"),
                  "Your capacity under the instrument.", multi=True),
        FacetSpec("sector",
                  ("employment", "health", "finance", "biometrics", "education", "law-enforcement",
                   "critical-infrastructure", "public-services", "consumer", "marketing", "other"),
                  "Sector / field of application.", multi=True),
        FacetSpec("jurisdiction",
                  ("DE", "EU", "UK", "US", "FR", "CH", "JP", "CA", "AU", "global", "other"),
                  "Territory / legal order that governs.", multi=True),
        FacetSpec("category",
                  ("prohibited", "high-risk", "limited-risk", "minimal-risk", "gpai",
                   "special-category", "systemic", "none", "undetermined"),
                  "The instrument's risk/class — a determination, not a fact."),
        FacetSpec("scope",
                  ("personal_data", "automated_decision", "minors", "cross_border",
                   "biometric", "special_category", "safety_component"),
                  "Open scope flags that pull in obligations / other instruments.", multi=True),
    ),
)


_VOCAB_REGISTRY: dict[str, DomainVocabulary] = {
    "ai-act": AI_ACT_VOCAB,
    "neutral": NEUTRAL_VOCAB,
}


def register_vocabulary(vocab: DomainVocabulary) -> None:
    """Register a domain vocabulary so the matcher can resolve it by name."""
    _VOCAB_REGISTRY[vocab.domain] = vocab


def get_vocabulary(domain: str) -> Optional[DomainVocabulary]:
    return _VOCAB_REGISTRY.get(domain)


def make_card(domain: str, *, description: str = "", notes: str = "",
              contact: str = "", attachments: Optional[list[str]] = None,
              subject_id: str = "", strict: bool = False,
              **facets: Any) -> SubjectCard:
    """Build a subject card — capture-first.

    A card needs NOTHING but a domain. Pass any of ``description`` (free-text
    system / PoC summary), ``notes``, ``contact``, ``attachments`` and/or typed
    facets. An empty card is valid and assessable (everything → "may apply").

    Facet handling, lenient by default (``strict=False``):
      - a facet name not in the vocabulary, or a value outside its controlled
        set, is NOT discarded and does NOT raise — it is appended to ``notes``
        (prefixed ``[unmapped] name=value``) so nothing the user gave is lost,
        and the human/Layer-2 can map it later.
      - ``strict=True`` restores hard validation (raises on unknown name/value)
        — use it for programmatic callers that want a typo guard.
    Omitted facets are simply UNKNOWN downstream (the point of three-valued).
    """
    vocab = get_vocabulary(domain)
    if vocab is None:
        raise ValueError(f"no vocabulary registered for domain {domain!r}")
    clean: dict[str, Any] = {}
    unmapped: list[str] = []
    for name, value in facets.items():
        spec = vocab.facet(name)
        if spec is None:
            if strict:
                raise ValueError(f"{name!r} is not a facet of {domain!r}")
            unmapped.append(f"[unmapped] {name}={value}")
            continue
        if value is UNKNOWN:
            continue
        vals = value if isinstance(value, (list, tuple)) else [value]
        bad = [v for v in vals if v not in spec.values]
        if bad:
            if strict:
                raise ValueError(
                    f"{bad!r} not valid for {name!r} (allowed: {spec.values})")
            unmapped.append(f"[unmapped] {name}={value}")
            # keep any in-vocabulary values that were alongside the bad ones
            vals = [v for v in vals if v in spec.values]
            if not vals:
                continue
        if spec.multi:
            clean[name] = list(vals)
        else:
            if len(vals) != 1:
                if strict:
                    raise ValueError(f"{name!r} is single-valued")
                unmapped.append(f"[unmapped] {name}={value}")
                continue
            clean[name] = vals[0]
    full_notes = "\n".join([n for n in (notes, *unmapped) if n]).strip()
    return SubjectCard(domain=domain, facets=clean,
                       description=description, notes=full_notes,
                       contact=contact, attachments=list(attachments or []),
                       subject_id=subject_id)
