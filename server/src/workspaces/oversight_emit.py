# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Oversight ND — OUT face: emit accountable interventions.

The IN face (`oversight_extractor`) compiles oversight requirements into
`OversightFacet`s. This module is the emitter: at a decision point it turns a
gate verdict + its grounds into the transport payloads a human acts on —

  * :class:`GroundsBundle` — the informed-ratification payload for the Approve
    path (action, grounds, footprint, reversibility, and, for a residual, the
    options). Notification ≠ decision: the bundle carries notice + grounds + a
    link target; the decision is recorded on the decision surface, never parsed
    back from a ticket reply.
  * :class:`DoubtDossier` — assembled when the context is automated-decision,
    profiling, or high-risk (GDPR Art. 22, AI Act high-risk). It is the
    counter-grounds: *where the agent could be wrong* (taxonomy §7.4). Built in
    the background lane, attached to the human's copy only — never to the
    agent's output, never admitted to learnable state (non-disturbance).

Pure stdlib. Inputs are plain dicts (a gate audit triple, oversight facets,
pair history) so the emitter couples to no internal class it does not own.
Legal hooks: AI Act Art. 14(4)(a)-(b), GDPR Art. 22(3)/Rec. 71, Art. 86 —
confirm article wording against consolidated texts before external citation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Sequence


# Footprints whose presence makes a decision automated-/profiling-/high-risk
# enough to mandate a doubt dossier (an F1 class trigger).
_DOSSIER_FOOTPRINTS = {"personal-data", "irreversible", "security-control"}
# Action-class / scope substrings that name an ADM or profiling context.
_DOSSIER_MARKERS = (
    "profil", "automated-decision", "automated_decision", "adm",
    "scoring", "ranking", "eligibility", "creditworth", "high-risk",
)

# Footprints that are irreversible-by-nature → reversibility window applies.
_IRREVERSIBLE = {"irreversible", "external-publish", "financial"}


@dataclass
class GroundsBundle:
    """Informed-ratification payload — the Approve node's notice.

    ``options`` is empty for a decidable ratification (one determinate output
    to confirm) and populated for a residual (a choice to originate). The
    presence of options is the structural difference between ratification and
    origination and the connector contract keys on it:
    a residual must never render as approve/reject (taxonomy §4.4)."""
    action_class: str
    agent: str
    verdict: str
    footprint: list[str]
    grounds: list[dict[str, Any]]          # cited norm/precedent refs, by id
    reversibility: str                     # "reversible" | "window" | "irreversible"
    reversibility_window: str = ""         # set when reversibility == "window"
    options: list[dict[str, Any]] = field(default_factory=list)
    link_target: str = ""                  # where origination/ratification happens
    requires_origination: bool = False     # residual ⇒ True (no binary control)
    dossier: Optional["DoubtDossier"] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.dossier is not None:
            d["dossier"] = self.dossier.to_dict()
        return d

    def connector_payload(self) -> dict[str, Any]:
        """The shape a ticket/e-mail connector receives. A residual carries no
        approve/reject affordance — only the options and a link out. The
        decision returns through the decision surface, not this payload."""
        base = {
            "kind": "oversight-grounds",
            "action": self.action_class,
            "agent": self.agent,
            "verdict": self.verdict,
            "footprint": self.footprint,
            "reversibility": self.reversibility,
            "grounds": self.grounds,
            "link": self.link_target,
            "render": "options" if self.requires_origination else "ratify",
        }
        if self.requires_origination:
            base["options"] = self.options          # n ≥ 2, no default, no ranking
        if self.dossier is not None:
            base["doubt"] = self.dossier.summary()
        return base


@dataclass
class DoubtDossier:
    """Counter-grounds — where the agent could be wrong (taxonomy §7.4).

    Seven components, each from existing substrate. Any component may be empty
    when its source is unavailable; an empty dossier is still emitted (its
    emptiness is itself information: nothing surfaced as weak)."""
    weakest_citations: list[dict[str, Any]] = field(default_factory=list)
    confidence_profile: dict[str, Any] = field(default_factory=dict)
    precedent_distance: list[dict[str, Any]] = field(default_factory=list)
    distributional_position: dict[str, Any] = field(default_factory=dict)
    override_history: dict[str, Any] = field(default_factory=dict)
    counterfactual_sensitivity: list[dict[str, Any]] = field(default_factory=list)
    blind_spots: list[str] = field(default_factory=list)
    trigger: str = ""                      # why the dossier was mandated

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """Compact form for a connector preview (noise discipline)."""
        return {
            "trigger": self.trigger,
            "weakest_citation_count": len(self.weakest_citations),
            "min_confidence": self.confidence_profile.get("min"),
            "nearest_precedent": (self.precedent_distance[0]
                                  if self.precedent_distance else None),
            "ood_percentile": self.distributional_position.get("percentile"),
            "prior_override_rate": self.override_history.get("rate"),
            "flip_facts": [c.get("fact") for c in
                           self.counterfactual_sensitivity[:3]],
            "blind_spots": self.blind_spots,
        }


def needs_dossier(footprint: Sequence[str], action_class: str = "",
                  scope: str = "") -> Optional[str]:
    """Return the trigger string if a doubt dossier is mandated, else None."""
    fp = set(footprint or ())
    hit = fp & _DOSSIER_FOOTPRINTS
    text = f"{action_class} {scope}".lower()
    marker = next((m for m in _DOSSIER_MARKERS if m in text), None)
    if marker:
        return f"automated-decision/profiling context ({marker!r})"
    if "personal-data" in fp and ("irreversible" in fp or "security-control" in fp):
        return f"high-risk footprint combination ({sorted(hit)})"
    return None


def _reversibility(footprint: Sequence[str],
                   window: str = "") -> tuple[str, str]:
    fp = set(footprint or ())
    if "irreversible" in fp and not window:
        return "irreversible", ""
    if fp & _IRREVERSIBLE:
        return "window", (window or "cooling-off until released")
    return "reversible", ""


def build_dossier(
    *,
    grounds: Sequence[dict[str, Any]] = (),
    confidences: Sequence[float] = (),
    precedents: Sequence[dict[str, Any]] = (),
    ood_percentile: Optional[float] = None,
    override_history: Sequence[dict[str, Any]] = (),
    decisive_facts: Sequence[dict[str, Any]] = (),
    blind_spots: Sequence[str] = (),
    trigger: str = "",
) -> DoubtDossier:
    """Assemble the seven-component dossier from background-lane material.

    Each argument is optional; the assembler degrades gracefully — a thin
    dossier is honest about what could not be gathered, never invented."""
    # 1. weakest citations: lowest authority tier / stale / unverified first.
    weak = sorted(
        (g for g in grounds
         if g.get("authority_tier", 1) >= 4
         or g.get("currency") in ("stale", "superseded", "unverified")
         or not g.get("verified", True)),
        key=lambda g: (-g.get("authority_tier", 1),
                       0 if g.get("verified", True) else -1))
    # 2. confidence profile.
    conf = sorted(c for c in confidences if c is not None)
    profile: dict[str, Any] = {}
    if conf:
        profile = {"min": round(conf[0], 3),
                   "below_floor": [round(c, 3) for c in conf if c < 0.85],
                   "n": len(conf)}
    # 3. precedent distance (nearest first).
    prec = sorted(precedents,
                  key=lambda p: p.get("distance", 1.0))[:5]
    # 4. distributional position.
    dist = ({"percentile": round(ood_percentile, 1),
             "unusual": ood_percentile is not None and ood_percentile >= 90}
            if ood_percentile is not None else {})
    # 5. override history.
    oh = list(override_history)
    n_over = sum(1 for o in oh if o.get("overridden"))
    history: dict[str, Any] = {}
    if oh:
        history = {"n": len(oh), "overridden": n_over,
                   "rate": round(n_over / len(oh), 3),
                   "reasons": [o.get("reason") for o in oh
                               if o.get("overridden") and o.get("reason")][:5]}
    # 6. counterfactual sensitivity (decisive facts, most sensitive first).
    cf = sorted(decisive_facts,
                key=lambda f: f.get("sensitivity", 0.0), reverse=True)[:5]
    return DoubtDossier(
        weakest_citations=list(weak),
        confidence_profile=profile,
        precedent_distance=prec,
        distributional_position=dist,
        override_history=history,
        counterfactual_sensitivity=cf,
        blind_spots=list(blind_spots),
        trigger=trigger,
    )


def build_grounds_bundle(
    audit_triple: dict[str, Any],
    *,
    grounds: Sequence[dict[str, Any]] = (),
    options: Sequence[dict[str, Any]] = (),
    link_target: str = "",
    scope: str = "",
    reversibility_window: str = "",
    dossier: Optional[DoubtDossier] = None,
    dossier_material: Optional[dict[str, Any]] = None,
) -> GroundsBundle:
    """Build the Approve/Decide payload from a gate audit triple.

    ``options`` non-empty ⇒ residual ⇒ ``requires_origination`` (no binary
    control downstream). A dossier is attached when the footprint/scope
    mandates one (``needs_dossier``); pass ``dossier`` directly or
    ``dossier_material`` (kwargs for :func:`build_dossier`)."""
    footprint = list(audit_triple.get("footprint", []))
    action_class = audit_triple.get("object", "")
    rev, window = _reversibility(footprint, reversibility_window)
    is_residual = bool(options)

    if dossier is None:
        trig = needs_dossier(footprint, action_class, scope)
        if trig is not None:
            mat = dict(dossier_material or {})
            mat.setdefault("trigger", trig)
            mat.setdefault("grounds", grounds)
            dossier = build_dossier(**mat)

    return GroundsBundle(
        action_class=action_class,
        agent=audit_triple.get("subject", ""),
        verdict=audit_triple.get("predicate", ""),
        footprint=footprint,
        grounds=list(grounds),
        reversibility=rev,
        reversibility_window=window,
        options=list(options),
        link_target=link_target,
        requires_origination=is_residual,
        dossier=dossier,
    )
