# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Required-artifact extraction — what the instrument makes you *produce*.

A norm that says "the controller shall maintain a record of processing
activities" does not just impose an action; it implies a **deliverable the
organisation must produce and hold**: a RoPA. This surface turns those buried
duties into a checklist of artifacts — contracts, policies, registers,
assessments — so a reader sees "to comply with this document you must have: a
DPA, a privacy policy, a DPIA, …".

The curated compliance-artifact catalogue (trigger phrases → canonical artifact +
category) and the obligation-cued scan are **not** implemented here any more —
they are the ingest plane's, in ``loomground_ingest.artifacts``, CONSUMED through
the ``adapters.ingest.artifacts`` seam. What stays here is the ND-dispatcher
wrapping: projecting a required artifact into a mental-model pair + STRUCTURAL
edges — RVND's own orchestration. Extend the catalogue in ``loomground-ingest``,
never here.

Output: one ``kind=required-artifact`` pair per distinct artifact found, carrying
the category, the canonical artifact name, the triggering phrase, and a STRUCTURAL
edge ``(artifact) required-by (host)``. It *flags the requirement*; it does not
draft the artifact (ship the mechanism, not the deliverable).
"""

from __future__ import annotations

import hashlib
from typing import Any

from rvnd.adapters.solver.dimensions import Dimension
from .nd_routing import BaseNDDispatcher
from .adapters.ingest.artifacts import (  # consumed from the plane, not re-grown
    RequiredArtifact,          # noqa: F401 — re-exported for the package facade + consumers
    extract_required_artifacts,
)


# ---------------------------------------------------------------------------
# ND dispatcher — RVND-owned orchestration (unchanged)
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
