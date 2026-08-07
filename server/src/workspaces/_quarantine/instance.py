# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""QUARANTINED — the MIGRATED portion of the original contracts/instance.

This is the contract MODEL — ``PartyRef`` and ``ContractInstance`` (with
``ContractError``) — as it stood in RVND before the world-stack cut, now consumed
from ``loomground-legal`` via ``workspaces.adapters.legal``. Kept verbatim ONLY
so the retirement can be verified against the original before deletion;
dead-on-arrival, never imported by live code (fenced by
``tests/test_consumed_modules.py``). The folder runtime — ``ContractRegistry``
(JSONL persistence, signed mutation-log audit, and the world-map ``_project``
adapter) — STAYED in the live module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from workspaces.adapters.solver.temporal import Date, Money, RelativeDeadline, Term
from ..legal_world import EntityKind


class ContractError(ValueError):
    """Raised when a contract record is malformed. Reject, don't coerce."""


# ── PartyRef ──────────────────────────────────────────────────────────────────

_PERSON_KINDS = frozenset({EntityKind.LEGAL_PERSON.value,
                           EntityKind.NATURAL_PERSON.value,
                           EntityKind.PUBLIC_BODY.value})
_ROLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")


def _lei_checksum_ok(lei: str) -> bool:
    """ISO 17442 / ISO 7064 mod-97-10: letters → 10..35, whole string mod 97 == 1."""
    digits = "".join(str(int(c, 36)) for c in lei)
    return int(digits) % 97 == 1


@dataclass(frozen=True)
class PartyRef:
    """A contract party as a *reference* into the world map — not a bare string."""

    entity_code: str
    role: str
    name: str = ""
    lei: Optional[str] = None
    entity_kind: str = EntityKind.LEGAL_PERSON.value

    def __post_init__(self) -> None:
        if not self.entity_code or not isinstance(self.entity_code, str):
            raise ContractError("party needs a non-empty entity_code")
        if not self.role or not _ROLE_RE.match(self.role):
            raise ContractError(f"party role must be a lowercase slug, got {self.role!r}")
        if self.entity_kind not in _PERSON_KINDS:
            raise ContractError(
                f"party entity_kind must be one of {sorted(_PERSON_KINDS)}, got {self.entity_kind!r}")
        if self.lei is not None:
            if not _LEI_RE.match(self.lei):
                raise ContractError(f"not an ISO 17442 LEI (20 chars): {self.lei!r}")
            if not _lei_checksum_ok(self.lei):
                raise ContractError(f"LEI checksum failed: {self.lei!r}")

    def to_dict(self) -> dict:
        return {"entity_code": self.entity_code, "role": self.role,
                "name": self.name, "lei": self.lei, "entity_kind": self.entity_kind}

    @classmethod
    def from_dict(cls, d: dict) -> "PartyRef":
        return cls(entity_code=d["entity_code"], role=d["role"],
                   name=d.get("name", ""), lei=d.get("lei"),
                   entity_kind=d.get("entity_kind", EntityKind.LEGAL_PERSON.value))


# ── ContractInstance ──────────────────────────────────────────────────────────

_CONTRACT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SUPERSEDES_RE = re.compile(r"^(?P<cid>[a-z0-9][a-z0-9._-]*)@(?P<ver>\d+)$")


@dataclass(frozen=True)
class ContractInstance:
    """One version of one contract. Identity = (contract_id, version); content
    identity = document_hash."""

    contract_id: str
    version: int = 1
    title: str = ""
    contract_type: str = ""
    parties: tuple[PartyRef, ...] = ()
    effective_date: Optional[Date] = None
    term: Optional[Term] = None
    governing_law: Optional[str] = None
    jurisdiction_anchors: tuple[str, ...] = ()
    events: dict[str, Date] = field(default_factory=dict)
    total_value: Optional[Money] = None
    supersedes: Optional[str] = None
    document_hash: str = ""
    language: str = ""
    source_document: str = ""
    facets: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _CONTRACT_ID_RE.match(self.contract_id or ""):
            raise ContractError(f"contract_id must be a slug, got {self.contract_id!r}")
        if not isinstance(self.version, int) or self.version < 1:
            raise ContractError(f"version must be an int >= 1, got {self.version!r}")
        if self.supersedes is not None:
            m = _SUPERSEDES_RE.match(self.supersedes)
            if not m:
                raise ContractError(
                    f"supersedes must be 'contract_id@version', got {self.supersedes!r}")
            if m["cid"] == self.contract_id and int(m["ver"]) >= self.version:
                raise ContractError(
                    f"a version can only supersede an earlier version of itself "
                    f"({self.supersedes!r} vs version {self.version})")
        if self.effective_date is not None and not isinstance(self.effective_date, Date):
            raise ContractError("effective_date must be a temporal.Date")
        if self.term is not None and not isinstance(self.term, Term):
            raise ContractError("term must be a temporal.Term")
        if self.total_value is not None and not isinstance(self.total_value, Money):
            raise ContractError("total_value must be a temporal.Money")
        for k, v in self.events.items():
            if not isinstance(v, Date):
                raise ContractError(f"event {k!r} must map to a temporal.Date")
        roles_seen = {}
        for p in self.parties:
            if not isinstance(p, PartyRef):
                raise ContractError("parties must be PartyRef instances")
            roles_seen.setdefault(p.role, []).append(p.entity_code)

    @property
    def ref(self) -> str:
        return f"{self.contract_id}@{self.version}"

    def event_dates(self) -> dict[str, Date]:
        out = dict(self.events)
        if self.effective_date is not None:
            out.setdefault("effective_date", self.effective_date)
        if self.term is not None:
            end = self.term.end_date()
            if end is not None:
                out.setdefault("term_end", end)
        return out

    def resolve_deadline(self, deadline: RelativeDeadline) -> Optional[Date]:
        return deadline.resolve(self.event_dates())

    def party_by_role(self, role: str) -> tuple[PartyRef, ...]:
        return tuple(p for p in self.parties if p.role == role)

    def missing_fields(self) -> list[str]:
        out = []
        if not self.parties:
            out.append("parties")
        if self.effective_date is None:
            out.append("effective_date")
        if self.term is None:
            out.append("term")
        if self.governing_law is None:
            out.append("governing_law")
        if not self.contract_type:
            out.append("contract_type")
        if not self.language:
            out.append("language")
        return out

    def to_dict(self) -> dict:
        return {
            "contract_id": self.contract_id, "version": self.version,
            "title": self.title, "contract_type": self.contract_type,
            "parties": [p.to_dict() for p in self.parties],
            "effective_date": self.effective_date.iso if self.effective_date else None,
            "term": self.term.to_dict() if self.term else None,
            "governing_law": self.governing_law,
            "jurisdiction_anchors": list(self.jurisdiction_anchors),
            "events": {k: v.iso for k, v in self.events.items()},
            "total_value": self.total_value.to_dict() if self.total_value else None,
            "supersedes": self.supersedes,
            "document_hash": self.document_hash, "language": self.language,
            "source_document": self.source_document, "facets": self.facets,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContractInstance":
        return cls(
            contract_id=d["contract_id"], version=int(d.get("version", 1)),
            title=d.get("title", ""), contract_type=d.get("contract_type", ""),
            parties=tuple(PartyRef.from_dict(p) for p in d.get("parties", [])),
            effective_date=Date(d["effective_date"]) if d.get("effective_date") else None,
            term=Term.from_dict(d["term"]) if d.get("term") else None,
            governing_law=d.get("governing_law"),
            jurisdiction_anchors=tuple(d.get("jurisdiction_anchors", [])),
            events={k: Date(v) for k, v in d.get("events", {}).items()},
            total_value=Money.from_dict(d["total_value"]) if d.get("total_value") else None,
            supersedes=d.get("supersedes"),
            document_hash=d.get("document_hash", ""), language=d.get("language", ""),
            source_document=d.get("source_document", ""), facets=d.get("facets", {}),
        )
