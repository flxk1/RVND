# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Use-case ND — read a system / POC *description* into the canonical facet
shape so it can be subsumed under the deontic duties already in folder memory.

This closes the join the three-doc test found missing: statute prose became
structured duties, but a POC description had no shared vocabulary to match them
on, so the edge composer produced zero cross-document inferences. The fix is a
*facet vocabulary* both sides land in — NOT a JSON the user must write.

One schema, two doors:

- **Front door (default):** prose → :func:`extract_use_case_facets`. Mirrors the
  Rule ND: descriptive text in, structured facets out, each with a confidence and
  the source span it was read from. The ND *is* the normaliser.
- **Side door (optional):** a dict validating the schema → :func:`facets_from_json`
  (origin ``provided``, confidence 1.0). For CI / structured intake; never required.

The facets reuse the SAME literals the duty side (rule facets) and the action
gate already use — ``role`` ↔ duty ``subject``; ``footprint`` ↔
``action_gate`` tags + the conformity pack's ``footprint_instruments``;
``autonomy_grade`` ↔ the gate's L0–L4 — so :func:`subsume` matches instead of
guessing.

Design constraints (match the manifest's "ship mechanism, not judgment"):

- **Decidable facts are extracted; contestable classifications are routed.**
  Whether an Annex-III ``purpose_tag`` makes the system *high-risk* is a legal
  judgment, so :func:`subsume` emits it as a residual DECISION pair, never as an
  asserted fact.
- **Heuristic, honest confidence.** The front-door reader is keyword/cue based;
  it reports modest confidence and the span, so low-confidence facets fall to the
  oversight floor like every other extractor output. No model is required; a
  model (local→cloud cascade) can later replace the heuristic behind the same
  interface.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from rvnd.adapters.solver.dimensions import Dimension
from .nd_routing import BaseNDDispatcher, Classification

__all__ = [
    "UseCaseFacets",
    "extract_use_case_facets",
    "facets_from_json",
    "UseCaseND",
    "register_use_case_nd",
    "subsume",
    "FOOTPRINT_TAGS",
    "ROLES",
]

# Canonical literals — kept in lock-step with action_gate + the conformity pack.
FOOTPRINT_TAGS = (
    "personal-data", "financial", "irreversible", "external-publish",
    "security-control",
)
ROLES = ("provider", "deployer", "importer", "distributor", "unknown")
_ANNEX_III_TAGS = (
    "employment-screening", "biometric-identification", "credit-scoring",
    "education-access", "essential-private-or-public-services",
    "law-enforcement", "migration-asylum-border", "critical-infrastructure",
    "administration-of-justice",
)

# Confidence floor below which a facet is advisory only (mirrors the project
# floor used elsewhere; subsume() sends sub-floor + contestable facets to the
# residual rather than asserting them).
CONFIDENCE_FLOOR = 0.85


# ---------------------------------------------------------------------------
# Facet container
# ---------------------------------------------------------------------------

@dataclass
class _Facet:
    """A single facet value with provenance."""
    value: Any
    confidence: float = 0.0
    source_span: str = ""
    origin: str = "extracted"   # extracted | provided

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "confidence": round(self.confidence, 2),
                "source_span": self.source_span[:160], "origin": self.origin}


@dataclass
class UseCaseFacets:
    """The canonical use-case shape. Values are plain; provenance is parallel."""
    system_name: str = ""
    description: str = ""
    role: str = "unknown"
    lifecycle_stage: str = "unknown"
    purpose: str = ""
    purpose_tags: list[str] = field(default_factory=list)
    domain: str = ""
    footprint: list[str] = field(default_factory=list)
    autonomy_grade: str = "L1"
    affected_parties: list[str] = field(default_factory=list)
    affected_party_scale: Any = None
    human_review: str = "unknown"
    stop_capability: Any = "unknown"
    overseer_competence: str = "unspecified"
    provenance: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "system_name": self.system_name, "description": self.description,
            "role": self.role, "lifecycle_stage": self.lifecycle_stage,
            "purpose": self.purpose, "purpose_tags": self.purpose_tags,
            "domain": self.domain, "footprint": self.footprint,
            "autonomy_grade": self.autonomy_grade,
            "affected_parties": self.affected_parties,
            "affected_party_scale": self.affected_party_scale,
            "human_review": self.human_review,
            "stop_capability": self.stop_capability,
            "overseer_competence": self.overseer_competence,
            "facet_provenance": self.provenance,
        }
        return d


# ---------------------------------------------------------------------------
# Front door — prose → facets (heuristic, honest confidence)
# ---------------------------------------------------------------------------

# Cue tables. Each maps a regex to (value, confidence). First match wins per
# facet unless the facet is a list (then all matches accumulate).
_ROLE_CUES = [
    (re.compile(r"\bwe (?:are building|build|develop|provide|are the provider)\b", re.I), "provider", 0.7),
    (re.compile(r"\bprovider\b", re.I), "provider", 0.6),
    (re.compile(r"\bwe (?:deploy|use|operate|run|are deploying)\b", re.I), "deployer", 0.7),
    (re.compile(r"\bdeployer\b|\bdeployment target\b|\bproduction pilot\b|\binternal use\b", re.I), "deployer", 0.65),
    (re.compile(r"\b(?:import|distribute)\b", re.I), "importer", 0.4),
]
_LIFECYCLE_CUES = [
    (re.compile(r"\b(?:proof of concept|POC|prototype)\b", re.I), "poc", 0.8),
    (re.compile(r"\b(?:pilot)\b", re.I), "pilot", 0.7),
    (re.compile(r"\b(?:in production|live system|deployed in production)\b", re.I), "production", 0.7),
]
_PURPOSE_TAG_CUES = [
    # No trailing \b on stems — plurals/inflections ("applications", "candidates")
    # otherwise defeat the match (same bug class as the footprint cues).
    (re.compile(r"\b(?:job application|candidate|recruit|CV\b|résumé|resume|hiring|screen\w*\s+\w*\s*applicant)", re.I), "employment-screening", 0.8),
    (re.compile(r"\b(?:face|facial|biometric|fingerprint|iris)\b", re.I), "biometric-identification", 0.7),
    (re.compile(r"\b(?:credit score|creditworth|loan eligib|lending decision)\b", re.I), "credit-scoring", 0.8),
    (re.compile(r"\b(?:exam|student|admission|grading|education)\b", re.I), "education-access", 0.6),
    (re.compile(r"\b(?:benefit|welfare|emergency service|essential service)\b", re.I), "essential-private-or-public-services", 0.6),
    (re.compile(r"\b(?:police|law enforcement|crime|predictive policing)\b", re.I), "law-enforcement", 0.7),
    (re.compile(r"\b(?:asylum|migration|visa|border)\b", re.I), "migration-asylum-border", 0.7),
    (re.compile(r"\b(?:recommend|ranking feed|content feed|moderation)\b", re.I), "content-recommendation", 0.5),
]
# Note: no trailing \b on stems — it breaks on plurals/inflections
# ("email" before "emails", "auto-archive" before "auto-archived").
_FOOTPRINT_CUES = [
    (re.compile(r"\b(?:personal data|CV\b|résumé|resume|applicant|candidate|email|name\b|profile|user data|PII)", re.I), "personal-data", 0.7),
    (re.compile(r"\b(?:payment|invoice|price|salary|financial|transaction|account balance)", re.I), "financial", 0.6),
    (re.compile(r"\b(?:delete|irreversible|permanent|cannot be undone|auto-archiv|purge)", re.I), "irreversible", 0.55),
    (re.compile(r"\b(?:send\w*\s+\w*\s*email|sends?\s+\w+\s+email|publish|posts?\b|notif|rejection email|external)", re.I), "external-publish", 0.6),
    (re.compile(r"\b(?:access control|authenticat|security|firewall|credential)", re.I), "security-control", 0.5),
]
# Autonomy: cues for how unattended the system runs → L0..L4.
_AUTONOMY_CUES = [
    (re.compile(r"\b(?:without supervision|unattended|overnight|automatically|autonomous|no human|fully automated)\b", re.I), "L4", 0.7),
    (re.compile(r"\b(?:on the loop|monitors?|review the (?:list|output)|can override|oversee)\b", re.I), "L3", 0.5),
    (re.compile(r"\b(?:approves each|in the loop|human approves|sign-?off before)\b", re.I), "L1", 0.6),
]
_HUMAN_REVIEW_CUES = [
    (re.compile(r"\b(?:approves each|sign-?off before|in the loop|human approval before)\b", re.I), "in-the-loop-approval", 0.7),
    (re.compile(r"\b(?:can override|review the (?:list|ranked|output)|overseen by|monitors)\b", re.I), "post-hoc-override", 0.6),
    (re.compile(r"\b(?:no human|without supervision|fully automated|no oversight)\b", re.I), "none", 0.6),
]
_AFFECTED_CUES = [
    (re.compile(r"\b(applicant|candidate)s?\b", re.I), "job applicants"),
    (re.compile(r"\b(patient)s?\b", re.I), "patients"),
    (re.compile(r"\b(customer|consumer)s?\b", re.I), "customers"),
    (re.compile(r"\b(student|pupil)s?\b", re.I), "students"),
    (re.compile(r"\b(citizen|resident)s?\b", re.I), "citizens"),
    (re.compile(r"\b(employee|worker|staff)s?\b", re.I), "employees"),
    (re.compile(r"\b(user)s?\b", re.I), "users"),
]
_SCALE_RE = re.compile(r"\b([\d,]{2,})\s*(?:applications?|candidates?|users?|people|requests?|per week|per day|/week|/day)", re.I)
_STOP_CUES = re.compile(r"\b(?:stop button|interrupt|kill switch|halt the system|suspend)\b", re.I)
_TRAINED_CUES = re.compile(r"\b(?:trained|competence|qualified overseer|necessary training)\b", re.I)
_UNTRAINED_CUES = re.compile(r"\b(?:not (?:yet )?decided how.{0,30}train|no training|untrained)\b", re.I)

# Whether content looks like SOMEONE DESCRIBING THEIR system (a use case), as
# opposed to law describing systems in general. The distinctive markers are
# first-person build/deploy language, POC/pilot framing, and a deployment
# target — NOT the bare phrase "AI system" (which saturates statutes). The
# self-gate requires at least one DISTINCTIVE marker so statute/standard text
# does not trip it.
_USECASE_DISTINCTIVE = re.compile(
    r"(?:\bwe (?:are building|build|are developing|develop|deploy|use|are deploying|plan to)\b"
    r"|\bproof of concept\b|\bour (?:system|product|assistant|model|tool|POC)\b"
    r"|\bdeployment target\b|\bproduction pilot\b|\bgoing to (?:build|deploy))",
    re.I,
)
# Supporting (non-sufficient) signal — present in use cases but also in statutes.
_USECASE_SUPPORT = re.compile(
    r"\b(?:AI (?:system|assistant|model|tool|agent)|machine learning|"
    r"the system (?:parses|scores|ranks|processes|runs|sends|classif))",
    re.I,
)
# Strong normative markers — if these dominate and no distinctive marker is
# present, it is law, not a use case.
_NORMATIVE_DOMINANT = re.compile(
    r"\b(?:shall|must not|Article\s+\d+|Annex\s+[IVX]+|is prohibited|Clause\s+\d)\b",
)


def _span(content: str, m: Optional[re.Match]) -> str:
    if not m:
        return ""
    a = max(0, m.start() - 25)
    b = min(len(content), m.end() + 25)
    return content[a:b].replace("\n", " ").strip()


def looks_like_use_case(content: str) -> float:
    """Cheap detector — returns a 0..1 score that content is a use-case desc.

    Requires at least one DISTINCTIVE marker (first-person build/deploy, POC,
    deployment target). Bare "AI system" support signal is not sufficient — it
    saturates statute and standard text. When normative markers dominate and no
    distinctive marker is present, returns 0 (it is law, not a use case).
    """
    text = content or ""
    distinctive = len(_USECASE_DISTINCTIVE.findall(text))
    if distinctive == 0:
        return 0.0
    norm = len(_NORMATIVE_DOMINANT.findall(text))
    support = len(_USECASE_SUPPORT.findall(text))
    score = 0.5 + 0.15 * distinctive + 0.05 * support
    # If the doc is heavily normative (a statute that happens to say "we deploy"
    # in a recital), damp the score.
    if norm >= 5 and distinctive < 2:
        score -= 0.3
    return max(0.0, min(score, 0.95))


def _first(content: str, cues) -> tuple[Any, float, str]:
    for rx, val, conf in cues:
        m = rx.search(content)
        if m:
            return val, conf, _span(content, m)
    return None, 0.0, ""


def _all(content: str, cues) -> list[tuple[str, float, str]]:
    out, seen = [], set()
    for rx, val, conf in cues:
        m = rx.search(content)
        if m and val not in seen:
            seen.add(val)
            out.append((val, conf, _span(content, m)))
    return out


def extract_use_case_facets(content: str, *, system_name: str = "") -> UseCaseFacets:
    """Front door: read a system/POC description into canonical facets.

    Heuristic and deliberately conservative. Every facet carries the confidence
    and the source span it was read from; downstream (:func:`subsume`) decides
    what is asserted vs routed to the residual.
    """
    f = UseCaseFacets(description=content[:2000])
    prov: dict[str, dict] = {}

    # name: first heading or first sentence fragment (use the ORIGINAL content
    # so the heading line survives).
    if system_name:
        f.system_name = system_name
    else:
        mh = re.search(r"^#\s*(.+)$", content, re.M)
        if mh:
            f.system_name = re.split(r"[—\-(]", mh.group(1))[0].strip()[:80]

    # Cue matching runs on a whitespace-flattened copy so multi-line phrases
    # ("sends rejection\nemails") match; spans are drawn from this copy too.
    content = re.sub(r"\s+", " ", content)

    role, c, sp = _first(content, _ROLE_CUES)
    if role:
        f.role = role
        prov["role"] = _Facet(role, c, sp).to_dict()

    life, c, sp = _first(content, _LIFECYCLE_CUES)
    if life:
        f.lifecycle_stage = life
        prov["lifecycle_stage"] = _Facet(life, c, sp).to_dict()

    tags = _all(content, _PURPOSE_TAG_CUES)
    if tags:
        f.purpose_tags = [t for t, _, _ in tags]
        prov["purpose_tags"] = _Facet(f.purpose_tags,
                                      max(c for _, c, _ in tags),
                                      tags[0][2]).to_dict()

    fps = _all(content, _FOOTPRINT_CUES)
    if fps:
        f.footprint = [t for t, _, _ in fps]
        prov["footprint"] = _Facet(f.footprint,
                                   max(c for _, c, _ in fps),
                                   fps[0][2]).to_dict()

    grade, c, sp = _first(content, _AUTONOMY_CUES)
    if grade:
        f.autonomy_grade = grade
        prov["autonomy_grade"] = _Facet(grade, c, sp).to_dict()

    hr, c, sp = _first(content, _HUMAN_REVIEW_CUES)
    if hr:
        f.human_review = hr
        prov["human_review"] = _Facet(hr, c, sp).to_dict()

    affected = []
    for rx, val in _AFFECTED_CUES:
        if rx.search(content) and val not in affected:
            affected.append(val)
    if affected:
        f.affected_parties = affected
        prov["affected_parties"] = _Facet(affected, 0.6, "").to_dict()

    ms = _SCALE_RE.search(content)
    if ms:
        try:
            f.affected_party_scale = int(ms.group(1).replace(",", ""))
        except ValueError:
            f.affected_party_scale = ms.group(1)
        prov["affected_party_scale"] = _Facet(f.affected_party_scale, 0.6,
                                              _span(content, ms)).to_dict()

    if _STOP_CUES.search(content):
        f.stop_capability = True
        prov["stop_capability"] = _Facet(True, 0.6, _span(content, _STOP_CUES.search(content))).to_dict()

    if _UNTRAINED_CUES.search(content):
        f.overseer_competence = "named-untrained"
        prov["overseer_competence"] = _Facet("named-untrained", 0.6,
                                             _span(content, _UNTRAINED_CUES.search(content))).to_dict()
    elif _TRAINED_CUES.search(content):
        f.overseer_competence = "named-trained"
        prov["overseer_competence"] = _Facet("named-trained", 0.5,
                                             _span(content, _TRAINED_CUES.search(content))).to_dict()

    f.provenance = prov
    return f


# ---------------------------------------------------------------------------
# Side door — JSON → facets (validated against the same schema)
# ---------------------------------------------------------------------------

def facets_from_json(obj: dict[str, Any]) -> UseCaseFacets:
    """Side door: accept a pre-structured facet object (CI / intake form).

    Required keys mirror the JSON Schema (system_name, role, footprint). Values
    are taken as authoritative: origin ``provided``, confidence 1.0, no span.
    Unknown keys are ignored; bad enums raise ValueError.
    """
    for req in ("system_name", "role", "footprint"):
        if req not in obj:
            raise ValueError(f"use-case JSON missing required key: {req}")
    if obj["role"] not in ROLES:
        raise ValueError(f"invalid role: {obj['role']}")
    for tag in obj.get("footprint", []):
        if tag not in FOOTPRINT_TAGS:
            raise ValueError(f"invalid footprint tag: {tag}")

    f = UseCaseFacets(
        system_name=str(obj["system_name"]),
        description=str(obj.get("description", "")),
        role=obj["role"],
        lifecycle_stage=obj.get("lifecycle_stage", "unknown"),
        purpose=obj.get("purpose", ""),
        purpose_tags=list(obj.get("purpose_tags", [])),
        domain=obj.get("domain", ""),
        footprint=list(obj.get("footprint", [])),
        autonomy_grade=obj.get("autonomy_grade", "L1"),
        affected_parties=list(obj.get("affected_parties", [])),
        affected_party_scale=obj.get("affected_party_scale"),
        human_review=obj.get("human_review", "unknown"),
        stop_capability=obj.get("stop_capability", "unknown"),
        overseer_competence=obj.get("overseer_competence", "unspecified"),
    )
    f.provenance = {k: {"value": getattr(f, k), "confidence": 1.0,
                        "source_span": "", "origin": "provided"}
                    for k in ("role", "footprint", "autonomy_grade",
                              "purpose_tags", "affected_parties")}
    return f


# ---------------------------------------------------------------------------
# The ND wrapper (front door, registered in make_full_extractor)
# ---------------------------------------------------------------------------

def _hash(content: str, source: Optional[str]) -> str:
    h = hashlib.sha256()
    h.update(b"nd-use-case|")
    h.update((source or "").encode())
    h.update(b"|")
    h.update(content.encode("utf-8", "replace"))
    return "sha256:" + h.hexdigest()[:32]


class UseCaseND(BaseNDDispatcher):
    """ND that lands a system/POC description as one canonical use-case pair.

    Unlike the normative NDs it does NOT fire on legal text — it fires on
    *descriptive* content (a system that does X, deployment, users). It overrides
    ``can_handle`` with its own cue detector so it is independent of the
    normative classifier (a POC is not normative content).
    """

    nd_id = "nd-use-case"
    handles_types = ["use-case"]
    handles_facets: list[str] = []
    confidence_floor = 0.0

    def can_handle(self, classification: Classification) -> bool:
        # The default classifier has no "use-case" type, and dispatch() only
        # passes the Classification here, not the raw content. So claim broadly
        # and SELF-GATE in extract(): extract() runs the cheap use-case detector
        # on the actual content and returns [] when it is not a use-case
        # description. This keeps the ND independent of the classifier without
        # touching it; the cost is one regex findall on non-use-case docs.
        return True

    def extract(self, content, classification, *, source_document=None):
        if looks_like_use_case(content) < 0.6:
            return []
        f = extract_use_case_facets(content)
        pid = _hash(content, source_document)
        edges = _facet_edges(pid, f)
        return [{
            "id": pid,
            "problem": {
                "id": f"{pid}-p",
                "kind": "use-case-profile",
                "scope": "use-case",
                "type": "system-profile",
                "summary": f"use-case: {f.system_name or 'system'} "
                           f"(role={f.role}, autonomy={f.autonomy_grade})",
                "facets": {
                    "role": f.role,
                    "autonomy_grade": f.autonomy_grade,
                    "footprint": f.footprint,
                    "purpose_tags": f.purpose_tags,
                    "lifecycle_stage": f.lifecycle_stage,
                },
                "context": {"kind_of_model": "use-case-facets"},
            },
            "solution": {
                "id": pid,
                "problem_id": f"{pid}-p",
                "body": _render_facets(f),
                "body_format": "structured-use-case",
                "authority_tier": 4,
                "confidence": looks_like_use_case(content),
                "facets": f.to_dict(),
            },
            "edges": edges,
        }]


def _facet_edges(pid: str, f: UseCaseFacets) -> list[dict[str, Any]]:
    """Edges that give the join keys to the edge composer."""
    edges = []
    sys = f.system_name or "the system"
    if f.role != "unknown":
        edges.append({"subject": sys, "predicate": "acts-as-role",
                      "object": f.role, "dimension": Dimension.RELATIONAL.value,
                      "weight": 0.8, "source_pair": pid})
    for tag in f.footprint:
        edges.append({"subject": sys, "predicate": "has-footprint",
                      "object": tag, "dimension": Dimension.STRUCTURAL.value,
                      "weight": 0.7, "source_pair": pid})
    for tag in f.purpose_tags:
        edges.append({"subject": sys, "predicate": "has-purpose",
                      "object": tag, "dimension": Dimension.INTENTIONAL.value,
                      "weight": 0.7, "source_pair": pid})
    for who in f.affected_parties:
        edges.append({"subject": sys, "predicate": "affects",
                      "object": who, "dimension": Dimension.RELATIONAL.value,
                      "weight": 0.6, "source_pair": pid})
    return edges


def _render_facets(f: UseCaseFacets) -> str:
    lines = ["USE-CASE PROFILE",
             f"system:    {f.system_name or '(unnamed)'}",
             f"role:      {f.role}",
             f"lifecycle: {f.lifecycle_stage}",
             f"purpose:   {', '.join(f.purpose_tags) or '(untagged)'}",
             f"footprint: {', '.join(f.footprint) or '(none detected)'}",
             f"autonomy:  {f.autonomy_grade}",
             f"affected:  {', '.join(f.affected_parties) or '(none detected)'}"
             + (f" (~{f.affected_party_scale})" if f.affected_party_scale else ""),
             f"oversight: review={f.human_review} stop={f.stop_capability} "
             f"competence={f.overseer_competence}"]
    return "\n".join(lines)


def register_use_case_nd(router) -> None:
    router.register(UseCaseND())


# ---------------------------------------------------------------------------
# Subsumption — the projection that joins a use case to its duties
# ---------------------------------------------------------------------------

@dataclass
class Applicability:
    """One applicable-duty link or residual produced by subsumption."""
    kind: str                     # "duty" | "residual" | "oversight-gap" | "gate"
    title: str
    detail: str
    basis: str = ""
    confidence: float = 0.0


def subsume(facets: UseCaseFacets, duty_pairs: list[dict[str, Any]],
            regime: Optional[dict[str, Any]] = None) -> list[Applicability]:
    """Join a use-case facet object to the duty pairs in folder memory.

    Steps:
      1. role → candidate duties addressed to that role,
      2. footprint → instruments (regime pack) → duties under each tag,
      3. contestable purpose-tag → high-risk = RESIDUAL (human decides),
      4. oversight gap = Art.14-style duties vs human_review/stop/competence,
      5. (gate dry-run is left to the caller — it owns ActionRequest).

    Pure and deterministic: returns a list of :class:`Applicability`; writes
    nothing. The caller records them as pairs/decisions under the oversight dial.
    """
    out: list[Applicability] = []
    instruments = (regime or {}).get("footprint_instruments", {})

    # 2. footprint → instruments → duties
    for tag in facets.footprint:
        insts = instruments.get(tag, [])
        for inst in insts:
            label = inst.split("(")[0].strip().rstrip(":") or inst[:40]
            out.append(Applicability(
                kind="duty",
                title=f"{tag} → {label}",
                detail=inst,
                basis=f"footprint:{tag}",
                confidence=0.7,
            ))

    # 1. role → matching duty pairs already in memory
    role = facets.role
    if role and role != "unknown":
        for p in duty_pairs:
            sol = p.get("solution") or {}
            rule = sol.get("rule") or {}
            subj = str(rule.get("subject", "")).lower()
            bearer = str(sol.get("bearer", "")).lower()
            hay = subj + " " + bearer
            if role in hay or (role == "deployer" and "deployer" in hay) \
                    or (role == "provider" and "provider" in hay):
                act = (rule.get("action") or sol.get("action") or "")[:80]
                out.append(Applicability(
                    kind="duty",
                    title=f"duty addressed to {role}",
                    detail=f"{rule.get('modal','')}: {act}".strip(": "),
                    basis="role-match",
                    confidence=float(sol.get("confidence", 0.6)),
                ))

    # 3. contestable: Annex-III purpose tag → high-risk is a RESIDUAL
    annex_hits = [t for t in facets.purpose_tags if t in _ANNEX_III_TAGS]
    if annex_hits:
        out.append(Applicability(
            kind="residual",
            title="High-risk classification (human decides)",
            detail=(f"purpose tag(s) {', '.join(annex_hits)} match an Annex III "
                    f"category. Whether this system is high-risk — and thus "
                    f"whether the high-risk duties apply — is a legal judgment, "
                    f"not asserted by the extractor."),
            basis="annex-III-candidate",
            confidence=0.0,
        ))

    # 4. oversight gap (Art. 14 / Art. 26(2) shaped)
    gap_bits = []
    if facets.human_review in ("none", "post-hoc-override", "unknown"):
        gap_bits.append(f"human_review={facets.human_review}")
    if facets.stop_capability in (False, "unknown"):
        gap_bits.append(f"stop_capability={facets.stop_capability}")
    if facets.overseer_competence in ("unspecified", "named-untrained"):
        gap_bits.append(f"overseer_competence={facets.overseer_competence}")
    high_autonomy = facets.autonomy_grade in ("L3", "L4")
    if gap_bits and (annex_hits or high_autonomy):
        out.append(Applicability(
            kind="oversight-gap",
            title="Oversight gap → draft a workflow",
            detail=("Against effective-human-oversight duties, the declared "
                    f"oversight is incomplete: {', '.join(gap_bits)}; "
                    f"autonomy={facets.autonomy_grade}. Draft an oversight "
                    "workflow raising review/stop/competence before deployment."),
            basis="oversight-commensurability",
            confidence=0.6,
        ))

    # Dedup: upstream domain-ND over-fire stores each duty up to 4×, so
    # role-match produces duplicates. Collapse on (kind, normalised detail),
    # keeping the highest confidence. This is the subsumption layer being robust
    # to the known upstream duplication, not a substitute for fixing it.
    best: dict[tuple, Applicability] = {}
    for a in out:
        key = (a.kind, " ".join(a.detail.lower().split())[:120])
        if key not in best or a.confidence > best[key].confidence:
            best[key] = a
    return list(best.values())
