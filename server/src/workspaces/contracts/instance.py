# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The contract-instance model — contracts as first-class entities on the
legal world map, with typed parties, dates, terms, and a version chain.

The schema audit (2026-06-04) found contracts existing only as an opaque
``contract_id`` string in ``contract_reviews.py``: no parties, no effective
date, no term, no governing law, no versioning. This module supplies the
missing nouns so the obligation runtime and the machine-readable-contract
pipeline have something typed to bind to:

  * ``PartyRef``         — a party as a reference to a world-map entity
                           (LEGAL_PERSON / NATURAL_PERSON / PUBLIC_BODY) with a
                           role slug and an optional LEI (ISO 17442, checksum-
                           verified);
  * ``ContractInstance`` — identity (id + version + document hash), parties,
                           ``effective_date: Date``, ``term: Term``, governing
                           law as an entity code, declared event dates for
                           relative-deadline resolution, and a ``supersedes``
                           link (``contract_id@version``) so amendments form an
                           explicit chain instead of orphaned hashes;
  * ``ContractRegistry`` — JSONL persistence under ``<folder>/contracts/``,
                           idempotent on (contract_id, version), audited via
                           the folder's signed mutation log, and projected onto
                           the world map (contract entity + ``party_to_contract``
                           + ``subject_to`` edges) so ``reach()`` and the 5D KG
                           see contracts the way they see laws.

Cold-start discipline: every field except identity is optional. An unextracted
field is honestly ``None`` ("not extracted"), never guessed — but a field that
IS present is typed and validated at write (the temporal layer rejects
malformed dates; PartyRef rejects malformed LEIs). Pure stdlib.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ..legal_connection import Connection
from ..legal_world import EntityKind
from ..mutation_log import LogEvent, MutationLog
from workspaces.adapters.solver.temporal import Date, Money, RelativeDeadline, TemporalError, Term

__all__ = ["PartyRef", "ContractInstance", "ContractRegistry", "ContractError"]


class ContractError(ValueError):
    """Raised when a contract record is malformed. Reject, don't coerce."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """A contract party as a *reference* into the world map — not a bare string.
    ``entity_code`` should resolve to a LEGAL_PERSON / NATURAL_PERSON /
    PUBLIC_BODY entity; the registry creates a stub entity on register() if the
    code is unknown, so parties are queryable from day one."""

    entity_code: str
    role: str                            # processor | controller | licensor | …
    name: str = ""                       # display name (entity name wins if set)
    lei: Optional[str] = None            # ISO 17442 Legal Entity Identifier
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
    identity = document_hash. Every substantive field optional (cold-start:
    "not extracted" beats invented), every present field typed."""

    contract_id: str
    version: int = 1
    title: str = ""
    contract_type: str = ""              # dpa | nda | msa | licence | … (slug or "")
    parties: tuple[PartyRef, ...] = ()
    effective_date: Optional[Date] = None
    term: Optional[Term] = None
    governing_law: Optional[str] = None  # entity code of the legal order (e.g. "DE")
    jurisdiction_anchors: tuple[str, ...] = ()
    events: dict[str, Date] = field(default_factory=dict)  # signing, delivery, …
    total_value: Optional[Money] = None
    supersedes: Optional[str] = None     # "contract_id@version"
    document_hash: str = ""              # sha256:<hex> of the source bytes
    language: str = ""                   # ISO 639-1
    source_document: str = ""            # path/URL of the ingested document
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

    # ── derived ───────────────────────────────────────────────────────────────
    @property
    def ref(self) -> str:
        return f"{self.contract_id}@{self.version}"

    def event_dates(self) -> dict[str, Date]:
        """Events for RelativeDeadline.resolve(): declared events + the
        canonical ``effective_date`` / ``term_end`` aliases when known."""
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
        """The honest "not extracted" list the intake UI renders."""
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

    # ── serde ─────────────────────────────────────────────────────────────────
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


# ── ContractRegistry ──────────────────────────────────────────────────────────

def contracts_dir(folder: str | Path) -> Path:
    return Path(folder) / "contracts"


def _inst_path(folder: str | Path) -> Path:
    return contracts_dir(folder) / "instances.jsonl"


class ContractRegistry:
    """Persisted, audited registry of contract instances for one folder.
    Same discipline as ``legal_corpus.EntityRegistry``: JSONL storage
    (inspectable, diffable), idempotent register keyed by (contract_id,
    version), best-effort signed audit events, no network."""

    def __init__(self, folder: str | Path, *, log_root: Optional[str | Path] = None):
        self.folder = Path(folder)
        self.log_root = Path(log_root) if log_root else None
        self.instances: dict[str, dict] = {}     # "cid@ver" -> record
        self.load()

    # ── persistence ───────────────────────────────────────────────────────────
    def load(self) -> None:
        p = _inst_path(self.folder)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.instances[f"{r['contract_id']}@{r.get('version', 1)}"] = r

    def _flush(self) -> None:
        d = contracts_dir(self.folder)
        d.mkdir(parents=True, exist_ok=True)
        _inst_path(self.folder).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in self.instances.values())
            + ("\n" if self.instances else ""),
            encoding="utf-8")

    def _log(self, op: str, ref: str, extra: dict) -> Optional[str]:
        try:
            log = MutationLog(self.folder, log_root=self.log_root)
            return log.append(LogEvent(
                event="ingest", folder_path=str(self.folder), pair_id=ref,
                channel="document", actor=extra.get("actor", "ingest"),
                extra={"kind": "contract-instance", "op": op, **extra}))
        except Exception:
            return None     # audit is best-effort; never block a register on it

    # ── register / supersede ──────────────────────────────────────────────────
    def register(self, inst: ContractInstance, *, actor: str = "ingest",
                 entity_registry: Optional[Any] = None) -> dict:
        """Add or update one contract version. Idempotent on (contract_id,
        version): same document_hash → unchanged; different hash for the same
        version → REFUSED (amendments are new versions, not silent rewrites).
        If an ``entity_registry`` (legal_corpus.EntityRegistry) is passed, the
        contract and its parties are projected onto the world map."""
        key = inst.ref
        existing = self.instances.get(key)
        if existing is not None:
            if existing.get("document_hash") and inst.document_hash and \
                    existing["document_hash"] != inst.document_hash:
                raise ContractError(
                    f"{key} already registered with a different document_hash — "
                    f"register the amendment as version {inst.version + 1} "
                    f"with supersedes={key!r}")
            rec = dict(existing)
            rec.update({k: v for k, v in inst.to_dict().items() if v not in (None, "", [], {})})
            rec["last_seen"] = _now()
            self.instances[key] = rec
            self._flush()
            self._log("contract.update", key, {"actor": actor})
            return dict(rec, status="updated")
        if inst.supersedes is not None and inst.supersedes not in self.instances:
            raise ContractError(
                f"supersedes target {inst.supersedes!r} is not registered")
        now = _now()
        rec = dict(inst.to_dict(), first_seen=now, last_seen=now)
        self.instances[key] = rec
        self._flush()
        self._log("contract.create", key, {
            "actor": actor, "document_hash": inst.document_hash,
            "supersedes": inst.supersedes,
            "missing_fields": inst.missing_fields()})
        if entity_registry is not None:
            self._project(inst, entity_registry)
        return dict(rec, status="created")

    def supersede(self, old_ref: str, new_inst: ContractInstance, *,
                  actor: str = "ingest",
                  entity_registry: Optional[Any] = None) -> dict:
        """Register an amendment: new version explicitly chained to the old."""
        if old_ref not in self.instances:
            raise ContractError(f"cannot supersede unknown {old_ref!r}")
        if new_inst.supersedes != old_ref:
            raise ContractError(
                f"new instance must declare supersedes={old_ref!r}, "
                f"got {new_inst.supersedes!r}")
        return self.register(new_inst, actor=actor, entity_registry=entity_registry)

    # ── world-map projection ──────────────────────────────────────────────────
    def _project(self, inst: ContractInstance, reg: Any) -> None:
        """Contract + parties become entities; party_to_contract + subject_to
        edges land in the legal corpus, so reach() and the 5D KG see them."""
        reg.ingest_entity(
            code=inst.contract_id, name=inst.title or inst.contract_id,
            kind=EntityKind.CONTRACT.value, jurisdiction=inst.governing_law,
            source="ingest",
            facets={"contract_type": inst.contract_type, "version": inst.version,
                    "document_hash": inst.document_hash})
        for p in inst.parties:
            reg.ingest_entity(
                code=p.entity_code, name=p.name or p.entity_code,
                kind=p.entity_kind, source="ingest",
                facets={"lei": p.lei} if p.lei else {})
            reg.ingest_edge(
                subject=p.entity_code,
                connection=Connection.PARTY_TO_CONTRACT.value,
                obj=inst.contract_id,
                basis=f"{inst.ref} role={p.role}", source="ingest")
        if inst.governing_law:
            reg.ingest_edge(
                subject=inst.contract_id,
                connection=Connection.SUBJECT_TO.value,
                obj=inst.governing_law,
                basis=f"governing-law clause ({inst.ref})", source="ingest")

    # ── queries ───────────────────────────────────────────────────────────────
    def get(self, contract_id: str, version: Optional[int] = None) -> Optional[ContractInstance]:
        if version is not None:
            r = self.instances.get(f"{contract_id}@{version}")
            return ContractInstance.from_dict(r) if r else None
        latest = self.latest_version(contract_id)
        return self.get(contract_id, latest) if latest else None

    def latest_version(self, contract_id: str) -> Optional[int]:
        vs = [r["version"] for r in self.instances.values()
              if r["contract_id"] == contract_id]
        return max(vs) if vs else None

    def versions(self, contract_id: str) -> list[ContractInstance]:
        return sorted((ContractInstance.from_dict(r) for r in self.instances.values()
                       if r["contract_id"] == contract_id),
                      key=lambda i: i.version)

    def chain(self, contract_id: str) -> list[str]:
        """The supersession chain, oldest → newest, as refs."""
        return [i.ref for i in self.versions(contract_id)]

    def by_party(self, entity_code: str) -> list[ContractInstance]:
        out = []
        for r in self.instances.values():
            if any(p.get("entity_code") == entity_code for p in r.get("parties", [])):
                out.append(ContractInstance.from_dict(r))
        return sorted(out, key=lambda i: (i.contract_id, i.version))

    def all_latest(self) -> list[ContractInstance]:
        ids = {r["contract_id"] for r in self.instances.values()}
        return [self.get(cid) for cid in sorted(ids)]    # type: ignore[misc]
