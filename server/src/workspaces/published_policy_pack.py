# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fail-closed import boundary for externally published policy packs.

Classifiers and adapters declare the action kinds they can emit. RVND accepts
only declarations drawn from the host's known action-kind registry, requires
the four review attestations, and binds the pack fingerprint to the child's
approved governance lane. Import validates authority; it does not create it.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .adapters.policy_languages import installed_policy_language_packages
from .governance_lane import get_lane

REQUIRED_REVIEWS = (
    "child_safety",
    "developmental",
    "privacy",
    "jurisdictional",
)


class PolicyPackDenied(ValueError):
    """The published pack cannot enter the active governance boundary."""


@dataclass(frozen=True)
class ImportedPolicyPack:
    pack_id: str
    publisher: str
    policy_fingerprint: str
    action_kinds: tuple[str, ...]
    reviews: dict[str, str]
    child_agent: str
    governance_lane_id: str
    language_contracts: dict[str, dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "publisher": self.publisher,
            "policy_fingerprint": self.policy_fingerprint,
            "action_kinds": list(self.action_kinds),
            "reviews": dict(self.reviews),
            "child_agent": self.child_agent,
            "governance_lane_id": self.governance_lane_id,
            "language_contracts": {
                name: dict(contract)
                for name, contract in self.language_contracts.items()
            },
        }


def import_published_policy_pack(
    payload: Mapping[str, Any],
    *,
    folder: str,
    child_agent: str,
    declared_action_kinds: Iterable[str],
    known_action_kinds: Iterable[str],
    active_policy_fingerprint: str,
    review_attestations: Mapping[str, str],
    log_root: Optional[str] = None,
) -> ImportedPolicyPack:
    """Validate one pack against adapter declarations and the active child lane.

    ``declared_action_kinds`` comes from the classifier or adapter contract;
    ``known_action_kinds`` is RVND's host registry. Both are required so an
    adapter cannot smuggle an unregistered kind through a policy document.
    """
    pack_id = _required_text(payload, "pack_id")
    publisher = _required_text(payload, "publisher")
    pack_fingerprint = _required_text(payload, "policy_fingerprint")
    if not active_policy_fingerprint.strip():
        raise PolicyPackDenied("active policy fingerprint is required")
    if pack_fingerprint != active_policy_fingerprint:
        raise PolicyPackDenied("published pack does not match the active policy fingerprint")

    declared = _normalise_kinds(declared_action_kinds, "adapter declaration")
    known = _normalise_kinds(known_action_kinds, "RVND action-kind registry")
    requested = _normalise_kinds(payload.get("action_kinds"), "published pack")
    unknown = sorted(set(requested) - set(known))
    undeclared = sorted(set(requested) - set(declared))
    if unknown:
        raise PolicyPackDenied(f"unknown action kinds: {', '.join(unknown)}")
    if undeclared:
        raise PolicyPackDenied(f"undeclared action kinds: {', '.join(undeclared)}")

    if not isinstance(review_attestations, Mapping):
        raise PolicyPackDenied(
            "RVND review attestations must contain all mandatory reviews")
    reviews: dict[str, str] = {}
    for dimension in REQUIRED_REVIEWS:
        value = review_attestations.get(dimension)
        if not isinstance(value, str) or not value.strip():
            raise PolicyPackDenied(
                f"mandatory RVND {dimension} review is missing")
        reviews[dimension] = value.strip()

    active_lane = get_lane(folder, child_agent, log_root=log_root)
    if active_lane is None:
        raise PolicyPackDenied("child has no approved RVND governance lane")
    if active_lane.agent != child_agent:
        raise PolicyPackDenied("governance lane belongs to a different child agent")
    if active_lane.policy_fingerprint != active_policy_fingerprint:
        raise PolicyPackDenied("active policy fingerprint is not bound to the child governance lane")
    outside_lane = sorted(set(requested) - set(active_lane.action_classes))
    if outside_lane:
        raise PolicyPackDenied(
            f"action kinds outside the child governance lane: {', '.join(outside_lane)}")

    language_contracts = _active_language_contracts()
    return ImportedPolicyPack(
        pack_id=pack_id,
        publisher=publisher,
        policy_fingerprint=pack_fingerprint,
        action_kinds=requested,
        reviews=reviews,
        child_agent=child_agent,
        governance_lane_id=active_lane.lane_id,
        language_contracts=language_contracts,
    )


def import_published_policy_pack_into_versum(
    payload: Mapping[str, Any],
    *,
    folder: str,
    child_agent: str,
    declared_action_kinds: Iterable[str],
    known_action_kinds: Iterable[str],
    active_policy_fingerprint: str,
    review_attestations: Mapping[str, str],
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Validate through RVND, then persist through Ingest → Versum.

    The caller supplies an already acquired mapping. RVND owns the fail-closed
    governance decision; Loomground Ingest owns the dimensioned-subgraph write
    adapter; Versum owns the only persistent store door.
    """
    imported = import_published_policy_pack(
        payload,
        folder=folder,
        child_agent=child_agent,
        declared_action_kinds=declared_action_kinds,
        known_action_kinds=known_action_kinds,
        active_policy_fingerprint=active_policy_fingerprint,
        review_attestations=review_attestations,
        log_root=log_root,
    )

    from loomground_ingest import Subgraph, versum_writer
    from .adapters.versum import DimensionedSubgraphSink

    workspace = Path(folder).expanduser().resolve(strict=True)
    canonical = json.dumps(
        imported.to_dict(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest_hex = hashlib.sha256(canonical).hexdigest()
    digest = "sha256:" + digest_hex
    source_id = f"policy-pack:{imported.pack_id}"
    evidence_id = "evidence:" + hashlib.sha256(
        (source_id + ":" + digest).encode("utf-8")
    ).hexdigest()

    nodes: list[dict[str, Any]] = [
        {
            "id": source_id,
            "kind": "published-policy-pack",
            "publisher": imported.publisher,
            "policy_fingerprint": imported.policy_fingerprint,
        },
        {
            "id": f"agent:{imported.child_agent}",
            "kind": "child-agent",
        },
        {
            "id": f"governance-lane:{imported.governance_lane_id}",
            "kind": "rvnd-governance-lane",
        },
    ]
    nodes.extend({
        "id": f"action-kind:{kind}",
        "kind": "declared-action-kind",
        "name": kind,
    } for kind in imported.action_kinds)
    nodes.extend({
        "id": f"review:{imported.pack_id}:{dimension}",
        "kind": "rvnd-review-attestation",
        "dimension": dimension,
        "attestation": attestation,
    } for dimension, attestation in sorted(imported.reviews.items()))
    nodes.extend({
        "id": f"language-contract:{name}:{contract['version']}",
        "kind": "policy-language-contract",
        "name": name,
        **contract,
    } for name, contract in sorted(imported.language_contracts.items()))

    edges: list[dict[str, Any]] = [
        {
            "source": source_id,
            "target": f"agent:{imported.child_agent}",
            "type": "governs",
            "dimension": "intentional",
        },
        {
            "source": source_id,
            "target": f"governance-lane:{imported.governance_lane_id}",
            "type": "bound-to",
            "dimension": "structural",
        },
    ]
    edges.extend({
        "source": source_id,
        "target": f"action-kind:{kind}",
        "type": "declares",
        "dimension": "intentional",
    } for kind in imported.action_kinds)
    edges.extend({
        "source": source_id,
        "target": f"review:{imported.pack_id}:{dimension}",
        "type": "reviewed-by-rvnd",
        "dimension": "relational",
    } for dimension in sorted(imported.reviews))
    edges.extend({
        "source": source_id,
        "target": f"language-contract:{name}:{contract['version']}",
        "type": "validated-with",
        "dimension": "structural",
    } for name, contract in sorted(imported.language_contracts.items()))

    writer = versum_writer(
        DimensionedSubgraphSink(
            workspace / ".versum", authorized_store_root=workspace
        ),
        idempotency_key="rvnd-policy-pack:" + digest_hex,
        source={"source_id": source_id, "content_digest": digest},
        evidence=[{
            "evidence_id": evidence_id,
            "source_id": source_id,
            "locator": source_id,
            "content_digest": digest,
        }],
        nd={
            "facet": "nD",
            "system_id": "system:rvnd-policy-pack",
            "dimension_count": 5,
            "axes": [
                "structural", "causal", "intentional", "temporal",
                "relational",
            ],
        },
    )
    receipt = writer.write(Subgraph(
        dimension="nD",
        nodes=nodes,
        edges=edges,
        provenance={
            "source_id": source_id,
            "content_digest": digest,
            "policy_fingerprint": imported.policy_fingerprint,
        },
    ))
    if not receipt.get("written"):
        raise PolicyPackDenied(
            f"Ingest → Versum refused published policy pack: "
            f"{receipt.get('reason', 'unknown')}"
        )
    return {**imported.to_dict(), "write": receipt}


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PolicyPackDenied(f"{field} is required")
    return value.strip()


def _normalise_kinds(values: Any, source: str) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise PolicyPackDenied(f"{source} must declare action kinds")
    kinds: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise PolicyPackDenied(f"{source} contains an invalid action kind")
        kinds.append(value.strip())
    if not kinds:
        raise PolicyPackDenied(f"{source} must declare at least one action kind")
    if len(kinds) != len(set(kinds)):
        raise PolicyPackDenied(f"{source} contains duplicate action kinds")
    return tuple(kinds)


def _active_language_contracts() -> dict[str, dict[str, str]]:
    """Bind accepted packs to RVND's installed policy-language contracts.

    Published packs are already compiled, so RVND must not reinterpret their
    action kinds as free-form Deontic prose. It does, however, consume and
    persist the exact Governance and Deontic language identities used at the
    validation boundary. Missing or incomplete language contracts deny import.
    """
    contracts: dict[str, dict[str, str]] = {}
    for name, package, role in installed_policy_language_packages():
        try:
            version = package.language_version()
            status = package.language_status()
        except Exception as exc:
            raise PolicyPackDenied(
                f"{name} policy language contract is unavailable"
            ) from exc
        if not isinstance(version, str) or not version.strip():
            raise PolicyPackDenied(
                f"{name} policy language version is unavailable"
            )
        if not isinstance(status, str) or not status.strip():
            raise PolicyPackDenied(
                f"{name} policy language status is unavailable"
            )
        contracts[name] = {
            "package": (
                "loomground-governance"
                if name == "governance"
                else "loomground-deontic"
            ),
            "version": version.strip(),
            "status": status.strip(),
            "role": role,
        }
    return contracts
