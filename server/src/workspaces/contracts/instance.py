# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Contract-instance folder runtime — the registry over the consumed model.

The contract **model** — ``PartyRef`` (typed party with checksum-verified LEI)
and ``ContractInstance`` (identity, typed parties/dates/term/governing-law, the
supersession chain) — is RETIRED into ``loomground-legal`` and consumed through
the ``adapters/legal`` seam (the workspaces boundary rule confines every upstream
import there). The model is pure domain: dataclasses + validation, no I/O.

What STAYS in RVND is the **folder runtime**: ``ContractRegistry`` — JSONL
persistence under ``<folder>/contracts/``, idempotent on (contract_id, version),
audited via the folder's signed mutation log, and projected onto the legal world
map (contract entity + ``party_to_contract`` + ``subject_to`` edges) so
``reach()`` and the 5D KG see contracts the way they see laws. None of that is
domain model; all of it is RVND runtime.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..legal_connection import Connection
from ..mutation_log import LogEvent, MutationLog
# The contract model is the plane's, consumed via the seam.
from ..adapters.legal import (
    PartyRef, ContractInstance, ContractError, EntityKind, _lei_checksum_ok,
)

__all__ = ["PartyRef", "ContractInstance", "ContractRegistry", "ContractError",
           "_lei_checksum_ok"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
