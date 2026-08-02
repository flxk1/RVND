# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""navigate_folder — the substrate "where do I stand" operation (CANON §12.1).

Standard Workspace folder operation, available to every folder, never re-implemented
per vertical. Composes the existing engine:

    reference authorities (bank + user-dropped)  --build_house-->  requirement rooms
    the folder's own documents                   --map_coverage->  FURNISHED / EMPTY / ORPHAN

and returns a position report: your requirements, which documents furnish them, the
gaps (EMPTY rooms), and — where authorities carry them — the jurisdiction + trust tier
+ currency of each requirement's source.

Substrate provides this engine; the *vertical* provides the reference bank (its atoms
+ artifact catalogue). User-dropped authorities enter at the lowest trust tier and are
labelled, never treated as settled (CANON §12.1, three-tier authority trust).

LLM-free, deterministic. No statute knowledge here — it consumes atoms the NDs produced.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .requirements_house import build_house
from .evidence_coverage import EvidenceDoc, map_coverage


# ---- trust tiers (CANON §12.1) -------------------------------------------------
TIER_BANK_VERIFIED = "bank-verified"       # curated + expert-signed (verified: true)
TIER_BANK_UNVERIFIED = "bank-unverified"   # in bank, web-verified, awaiting sign-off
TIER_USER_DROPPED = "user-dropped"         # pasted/fetched this session; never reviewed

_TIER_RANK = {TIER_BANK_VERIFIED: 3, TIER_BANK_UNVERIFIED: 2, TIER_USER_DROPPED: 1}

# label shown on any finding/requirement built on a non-verified authority
_TIER_LABEL = {
    TIER_BANK_VERIFIED: "",
    TIER_BANK_UNVERIFIED: "source in the reference bank, not yet expert-confirmed",
    TIER_USER_DROPPED: "based on the source you supplied — not expert-confirmed",
}


@dataclass
class Authority:
    """A statute/case/rule atom-set offered to navigate-folder, with its trust tier."""
    authority_id: str
    jurisdiction: str = ""
    tier: str = TIER_BANK_VERIFIED
    currency: str = "current"                 # current | under-appeal | pending | superseded
    verified: bool = False
    obligations: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    cross_refs: list[dict[str, Any]] = field(default_factory=list)
    routes_to_agent: str = ""

    def label(self) -> str:
        return _TIER_LABEL.get(self.tier, "")


@dataclass
class NavigateReport:
    domain: str
    coverage_ratio: float
    requirements: list[dict[str, Any]]        # rooms, each with its authority context
    gaps: list[dict[str, Any]]                # EMPTY rooms — the things you should have but don't
    orphans: list[dict[str, Any]]             # documents that fit no requirement
    authorities_used: list[dict[str, Any]]    # id, jurisdiction, tier, currency, label
    unverified_present: bool                  # true if any non-verified authority informed the report

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def navigate_folder(
    *,
    domain: str,
    authorities: list[Authority],
    documents: list[dict[str, Any]],          # [{"doc_id","title","text"}]
    catalogue_artifacts: Optional[list[dict[str, Any]]] = None,
    title: str = "",
) -> NavigateReport:
    """Run the position operation for one folder.

    domain               — the vertical's domain key (e.g. "music-law")
    authorities          — bank + user-dropped Authority objects (already atomised by NDs)
    documents            — the folder's own files (contracts/invoices/statements)
    catalogue_artifacts  — the vertical's per-domain artifact/room catalogue (optional)
    """
    obligations: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = list(catalogue_artifacts or [])
    cross_refs: list[dict[str, Any]] = []
    used: list[dict[str, Any]] = []
    unverified = False

    # highest-trust authority first, so verified sources win room sourcing
    for a in sorted(authorities, key=lambda x: _TIER_RANK.get(x.tier, 0), reverse=True):
        for ob in a.obligations:
            ob = dict(ob)
            ob.setdefault("authority_id", a.authority_id)
            ob.setdefault("jurisdiction", a.jurisdiction)
            ob["_tier"] = a.tier
            ob["_currency"] = a.currency
            ob["_label"] = a.label()
            obligations.append(ob)
        artifacts.extend(a.artifacts)
        cross_refs.extend(a.cross_refs)
        used.append({"authority_id": a.authority_id, "jurisdiction": a.jurisdiction,
                     "tier": a.tier, "currency": a.currency, "verified": a.verified,
                     "routes_to_agent": a.routes_to_agent, "label": a.label()})
        if not a.verified:
            unverified = True

    house = build_house(obligations=obligations, artifacts=artifacts,
                        cross_refs=cross_refs, domain=domain, title=title)

    docs = [EvidenceDoc(doc_id=d.get("doc_id", f"doc-{i}"),
                        title=d.get("title", ""), text=d.get("text", ""))
            for i, d in enumerate(documents)]
    cov = map_coverage(house, docs)

    return NavigateReport(
        domain=domain,
        coverage_ratio=cov.coverage_ratio,
        requirements=[r.to_dict() for r in house.rooms],
        gaps=cov.empty,
        orphans=cov.orphans,
        authorities_used=used,
        unverified_present=unverified,
    )
