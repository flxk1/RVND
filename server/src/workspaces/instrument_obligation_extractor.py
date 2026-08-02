# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Required-artifact extraction — what the instrument makes you *produce*.

A norm that says "the controller shall maintain a record of processing
activities" does not just impose an action; it implies a **deliverable the
organisation must produce and hold**: a RoPA. NotebookLM-grade analysis turns
those buried duties into a checklist of artifacts — contracts, policies,
registers, assessments — so a reader sees "to comply with this document you
must have: a DPA, a privacy policy, a DPIA, …".

This is the "contracts and policies, by auto-scan" surface. It is distinct
from :mod:`.domain_nds` (which extracts the rule) and :mod:`.deontic` (which
formalises it): this module recognises, inside an obligation, the *named
artifact* the obligation presupposes.

Catalogue
---------
Each entry maps trigger phrases (EN + DE) to a canonical artifact with a
category. The catalogue is curated to the target domains (GDPR / AI Act / NIS2 /
DORA / contracts); extend :data:`_ARTIFACTS` to add more. Categories:

    contract     — an agreement with another party (DPA, SCCs, …)
    policy       — an internal/published policy document
    register     — a maintained record/log
    assessment   — a documented evaluation (DPIA, FRIA, conformity assessment)
    appointment  — a role that must be designated (DPO, EU representative)
    technical    — a technical/organisational artifact (audit log, TOMs)

Output
------
One ``kind=required-artifact`` pair per distinct artifact found, carrying the
category, the canonical artifact name, the triggering phrase, and a STRUCTURAL
edge ``(artifact) required-by (host/obligation)`` plus an INTENTIONAL edge
``(artifact) satisfies (obligation-purpose)``. It *flags the requirement*; it
does not draft the artifact (ship the mechanism, not the deliverable).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from workspaces.adapters.solver.dimensions import Dimension
from .nd_routing import BaseNDDispatcher


@dataclass(frozen=True)
class ArtifactSpec:
    key: str
    canonical: str
    category: str
    triggers: tuple[str, ...]   # lowercase phrases; matched as whole-ish substrings


# Curated catalogue. Triggers are matched case-insensitively. Keep them
# specific enough to avoid firing on prose that merely mentions the noun.
_ARTIFACTS: tuple[ArtifactSpec, ...] = (
    ArtifactSpec(
        "dpa", "Data Processing Agreement (Art. 28 GDPR)", "contract",
        ("processing shall be governed by a contract", "data processing agreement",
         "processor shall be bound by a contract", "auftragsverarbeitungsvertrag",
         "governed by a contract or other legal act"),
    ),
    ArtifactSpec(
        "sccs", "Standard Contractual Clauses", "contract",
        ("standard contractual clauses", "standardvertragsklauseln",
         "appropriate safeguards for the transfer"),
    ),
    ArtifactSpec(
        "ropa", "Records of Processing Activities (Art. 30 GDPR)", "register",
        ("record of processing activities", "records of processing",
         "verzeichnis von verarbeitungstätigkeiten", "maintain a record of"),
    ),
    ArtifactSpec(
        "dpia", "Data Protection Impact Assessment (Art. 35 GDPR)", "assessment",
        ("data protection impact assessment", "impact assessment",
         "datenschutz-folgenabschätzung", "carry out an assessment of the impact"),
    ),
    ArtifactSpec(
        "fria", "Fundamental Rights Impact Assessment (Art. 27 AI Act)", "assessment",
        ("fundamental rights impact assessment", "impact assessment on fundamental rights"),
    ),
    ArtifactSpec(
        "conformity-assessment", "Conformity Assessment (AI Act)", "assessment",
        ("conformity assessment", "konformitätsbewertung",
         "undergo the relevant conformity assessment"),
    ),
    ArtifactSpec(
        "privacy-policy", "Privacy Policy / Information Notice (Arts. 13–14 GDPR)", "policy",
        ("privacy policy", "privacy notice", "information to be provided",
         "datenschutzerklärung", "transparency information"),
    ),
    ArtifactSpec(
        "dpo", "Data Protection Officer designation (Art. 37 GDPR)", "appointment",
        ("designate a data protection officer", "data protection officer",
         "datenschutzbeauftragten benennen", "appoint a data protection officer"),
    ),
    ArtifactSpec(
        "eu-representative", "EU Representative designation", "appointment",
        ("designate a representative in the union", "eu representative",
         "appoint a representative", "vertreter in der union"),
    ),
    ArtifactSpec(
        "toms", "Technical and Organisational Measures", "technical",
        ("technical and organisational measures", "technische und organisatorische maßnahmen",
         "appropriate technical and organisational"),
    ),
    ArtifactSpec(
        "incident-register", "Incident / Breach Register", "register",
        ("document any personal data breach", "record of incidents",
         "log of incidents", "register of incidents", "breach notification"),
    ),
    ArtifactSpec(
        "risk-management-system", "Risk Management System (Art. 9 AI Act)", "technical",
        ("risk management system", "risikomanagementsystem",
         "establish, implement, document and maintain a risk management"),
    ),
    ArtifactSpec(
        "technical-documentation", "Technical Documentation (Annex IV AI Act)", "register",
        ("technical documentation", "technische dokumentation",
         "draw up the technical documentation"),
    ),
    ArtifactSpec(
        "logs", "Automatic Logging / Record-keeping (Art. 12 AI Act)", "technical",
        ("automatic recording of events", "logging capabilities",
         "keep the logs", "record-keeping"),
    ),
)


# Obligation cue near a trigger raises confidence (the artifact is *required*,
# not merely mentioned). Shared with the rule extractor's spirit.
_OBLIGATION_CUE = re.compile(
    r"\b(shall|must|is\s+required\s+to|are\s+required\s+to|obliged\s+to|"
    r"muss|müssen|ist\s+verpflichtet|sind\s+verpflichtet|hat\s+zu|haben\s+zu)\b",
    re.IGNORECASE,
)
_OBLIGATION_WINDOW = 160   # chars around the trigger to look for an obligation cue


@dataclass
class RequiredArtifact:
    key: str
    canonical: str
    category: str
    trigger_phrase: str
    obligated: bool          # an obligation cue sits near the trigger
    snippet: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_required_artifacts(content: str) -> list[RequiredArtifact]:
    """Scan content for obligations that imply a required artifact.

    Deduplicates by artifact key; the highest-confidence hit per artifact
    wins. An artifact mentioned with a nearby obligation cue ("shall maintain
    a record of processing") scores higher than a bare mention.
    """
    low = content.lower()
    found: dict[str, RequiredArtifact] = {}
    for spec in _ARTIFACTS:
        for trig in spec.triggers:
            idx = low.find(trig)
            if idx == -1:
                continue
            start = max(0, idx - _OBLIGATION_WINDOW)
            end = min(len(content), idx + len(trig) + _OBLIGATION_WINDOW)
            window = content[start:end]
            obligated = bool(_OBLIGATION_CUE.search(window))
            conf = 0.6
            if obligated:
                conf += 0.3
            conf = round(min(1.0, conf), 3)
            existing = found.get(spec.key)
            if existing is None or conf > existing.confidence:
                found[spec.key] = RequiredArtifact(
                    key=spec.key,
                    canonical=spec.canonical,
                    category=spec.category,
                    trigger_phrase=trig,
                    obligated=obligated,
                    snippet=window.strip()[:240],
                    confidence=conf,
                )
            break  # one trigger hit per artifact is enough
    return list(found.values())


# ---------------------------------------------------------------------------
# ND dispatcher
# ---------------------------------------------------------------------------

def _hash_pair(content: str, nd_id: str, source: str | None) -> str:
    h = hashlib.sha256()
    h.update(nd_id.encode("utf-8")); h.update(b"|")
    h.update((source or "inline").encode("utf-8")); h.update(b"|")
    h.update(content.encode("utf-8"))
    return "sha256:" + h.hexdigest()[:32]


def _edge(subject: str, predicate: str, obj: str, dimension: Dimension) -> dict[str, Any]:
    return {"subject": subject, "predicate": predicate, "object": obj,
            "dimension": dimension.value}


class RequiredArtifactExtractor(BaseNDDispatcher):
    """ND that surfaces the contracts, policies, registers, and assessments an
    instrument requires the organisation to produce.

    Produces ``kind=required-artifact`` pairs. Each carries the artifact
    category + canonical name and a STRUCTURAL edge ``artifact required-by
    document`` so the conflict/obligation graph can show "this document
    obliges you to hold these N artifacts".
    """

    nd_id = "nd-required-artifact"
    handles_types = ["normative", "document"]
    handles_facets: list[str] = []
    confidence_floor = 0.0

    def extract(self, content, classification, *, source_document=None):
        artifacts = extract_required_artifacts(content)
        base = _hash_pair(content, self.nd_id, source_document)
        scope = "regulation"
        for f in getattr(classification, "facets", []) or []:
            scope = f
            break
        out: list[dict[str, Any]] = []
        for idx, a in enumerate(artifacts):
            pid = f"{base}-art{idx}"
            out.append({
                "id": pid,
                "problem": {
                    "id": f"{pid}-p",
                    "kind": "required-artifact",
                    "scope": scope,
                    "type": "mental-model",
                    "summary": f"requires: {a.canonical}",
                    "facets": {
                        "artifact": a.key,
                        "category": a.category,
                        "obligated": a.obligated,
                    },
                    "context": {"kind_of_model": "required-artifact"},
                },
                "solution": {
                    "id": pid,
                    "problem_id": f"{pid}-p",
                    "artifact": a.key,
                    "artifact_name": a.canonical,
                    "category": a.category,
                    "obligated": a.obligated,
                    "trigger_phrase": a.trigger_phrase,
                    "body": (f"REQUIRED ARTIFACT ({a.category})\n"
                             f"{a.canonical}\n"
                             f"obligated: {a.obligated}\n"
                             f"trigger: \"{a.trigger_phrase}\""),
                    "body_format": "structured-artifact",
                    "authority_tier": 1,
                    "confidence": a.confidence,
                },
                "edges": [
                    _edge(a.key, "required-by", scope, Dimension.STRUCTURAL),
                    _edge(a.key, "is-a", a.category, Dimension.STRUCTURAL),
                ],
            })
        return out


def register_required_artifact_nd(router) -> None:
    """Register the required-artifact ND on a router."""
    router.register(RequiredArtifactExtractor())
