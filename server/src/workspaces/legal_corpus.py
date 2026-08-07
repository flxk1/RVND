# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The legal-entity corpus — a persisted, growable registry of entities and the
edges between them, with the ingest path that lets a user (or an automated scan)
add a newly-found organisation or law.

The world map (`legal_world.py`) ships a seed; real use needs the corpus to
*grow*: a user reads about a new regulator, or the folder ingestor scrapes a page
and finds a law it doesn't yet hold. Both flow through the same door:
``ingest_entity`` / ``ingest_edge``. Each ingest is:

  * **idempotent** — keyed by ``(kind, code)``; re-ingesting updates ``last_seen``
    and fills missing fields instead of duplicating;
  * **provenance-stamped** — ``source`` (seed | user | ingest), ``first_seen`` /
    ``last_seen``, and the URL the entity was found at;
  * **audited** — an optional signed mutation-log event records who added what,
    when (same Ed25519 hash-chain as the rest of folder memory).

Storage is plain JSONL under the folder (`<folder>/legal-corpus/entities.jsonl`,
`edges.jsonl`) so the corpus is inspectable, diffable, and portable. The headline
output is ``urls()`` — the retrievable corpus of pointers to organisations and
laws — which is exactly what a retrieval/grounding step consumes.

Pure stdlib + the existing mutation log; no network. (Validating that a URL
actually resolves is the job of the reachability check in ``source_validator`` /
``law_sources`` — this module records the pointer; it does not fetch it.)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .legal_connection import Connection, is_connection
from .adapters.legal import Entity, EntityKind, WorldEdge, WorldMap
from .mutation_log import MutationLog, LogEvent
from .urn import mint_canonical


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def corpus_dir(folder: str | Path) -> Path:
    return Path(folder) / "legal-corpus"


def _ent_path(folder: str | Path) -> Path:
    return corpus_dir(folder) / "entities.jsonl"


def _edge_path(folder: str | Path) -> Path:
    return corpus_dir(folder) / "edges.jsonl"


def _ekey(kind: str, code: str) -> str:
    return f"{kind}:{code}"


def _entity_ids(rec: dict) -> dict:
    """A record's external identifiers as a namespace->value map, folding any
    legacy top-level id fields into the neutral ``ids`` store. No namespace is
    privileged; ``source`` is minted only when this is empty."""
    ids = dict(rec.get("ids") or {})
    for legacy in ("celex", "arxiv", "doi"):
        if rec.get(legacy):
            ids.setdefault(legacy, rec[legacy])
    return ids


def _candidate_ids(c: dict) -> dict:
    """External identifiers a scan attached to a candidate: an explicit ``ids``
    map, plus any recognised id field folded in. Extend by naming a namespace
    here or by the extractor emitting ``ids`` directly — nothing is hardcoded
    into the minting rule."""
    ids = dict(c.get("ids") or {})
    for ns in ("celex", "arxiv", "doi", "ecli"):
        if c.get(ns):
            ids.setdefault(ns, c[ns])
    return ids


class EntityRegistry:
    """In-memory view of the persisted corpus, with idempotent ingest."""

    def __init__(self, folder: str | Path, *, log_root: Optional[str | Path] = None):
        from .folder_context import resolve_folder_context

        self.folder = Path(resolve_folder_context(folder))
        self.log_root = Path(log_root) if log_root else None
        self.entities: dict[str, dict] = {}    # ekey -> record
        self.edges: dict[str, dict] = {}       # edge-key -> record
        self.load()

    # ── persistence ───────────────────────────────────────────────────────────
    def load(self) -> None:
        ep = _ent_path(self.folder)
        if ep.exists():
            for line in ep.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.entities[_ekey(r["kind"], r["code"])] = r
        gp = _edge_path(self.folder)
        if gp.exists():
            for line in gp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.edges[self._edge_key(r["subject"], r["connection"], r["object"])] = r
        self._migrate_ids()

    def _migrate_ids(self) -> None:
        """Backfill or refresh each entity's canonical URN so a corpus written
        before the identity spine — or under an earlier minting rule — self-heals
        on open. Deterministic: a corpus already current rewrites nothing."""
        dirty = False
        for r in self.entities.values():
            ids = _entity_ids(r)
            if ids and r.get("ids") != ids:
                r["ids"] = ids
                dirty = True
            want = mint_canonical(r["code"], ids=ids)
            if r.get("canonical_urn") != want:
                r["canonical_urn"] = want
                dirty = True
        if dirty:
            self._flush()

    def _flush(self) -> None:
        if getattr(self, "_defer_flush", False):
            return                      # bulk seeding flushes once at the end
        d = corpus_dir(self.folder)
        d.mkdir(parents=True, exist_ok=True)
        _ent_path(self.folder).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in self.entities.values()) + "\n",
            encoding="utf-8")
        _edge_path(self.folder).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in self.edges.values()) + ("\n" if self.edges else ""),
            encoding="utf-8")

    def _log(self, event: str, ref: str, extra: dict) -> Optional[str]:
        if getattr(self, "_defer_flush", False) and event != "corpus.bulk":
            return None                 # bulk seeding audits once, with counts
        try:
            log = MutationLog(self.folder, log_root=self.log_root)
            return log.append(LogEvent(
                event="ingest", folder_path=str(self.folder), pair_id=ref,
                channel="document", actor=extra.get("source", "ingest"),
                extra={"kind": "legal-corpus", "op": event, **extra}))
        except Exception:
            return None     # audit is best-effort; never block an ingest on it

    @staticmethod
    def _edge_key(subject: str, connection: str, obj: str) -> str:
        return f"{subject}|{connection}|{obj}"

    # ── ingest path ───────────────────────────────────────────────────────────
    def ingest_entity(self, *, code: str, name: str, kind: str,
                      url: Optional[str] = None, jurisdiction: Optional[str] = None,
                      domains: Iterable[str] = (), region: str = "",
                      source: str = "ingest", facets: Optional[dict] = None,
                      ids: Optional[dict] = None) -> dict:
        """Add or update one entity. Idempotent on (kind, code): a repeat ingest
        refreshes ``last_seen`` and fills blanks rather than duplicating.
        ``ids`` maps identifier namespace -> value (e.g. ``{"celex": ...}``,
        ``{"ecli": ...}``, a national register); a stronger identifier arriving on
        a later ingest is recorded and upgrades the entity's ``canonical_urn``. No
        namespace is privileged. Returns the record with a ``status`` of
        created|updated|unchanged."""
        EntityKind(kind)                       # validate kind (raises on typo)
        key = _ekey(kind, code)
        now = _now()
        ids = {k: v for k, v in (ids or {}).items() if v}
        existing = self.entities.get(key)
        if existing is None:
            rec = {"code": code, "name": name, "kind": kind, "url": url,
                   "jurisdiction": jurisdiction, "domains": sorted(set(domains)),
                   "region": region, "source": source,
                   "facets": dict(facets or {}),
                   "first_seen": now, "last_seen": now}
            if ids:
                rec["ids"] = dict(ids)
            rec["canonical_urn"] = mint_canonical(code, ids=ids)
            self.entities[key] = rec
            self._flush()
            rec = dict(rec, status="created")
            self._log("entity.create", key, {"source": source, "url": url, "name": name})
            return rec
        # update: fill missing fields, refresh last_seen, widen domains
        changed = False
        for fld, val in (("url", url), ("jurisdiction", jurisdiction),
                         ("name", name), ("region", region)):
            if val and not existing.get(fld):
                existing[fld] = val
                changed = True
        merged = sorted(set(existing.get("domains", [])) | set(domains))
        if merged != existing.get("domains"):
            existing["domains"] = merged
            changed = True
        for ns, val in ids.items():
            if ns not in existing.get("ids", {}):
                existing.setdefault("ids", {})[ns] = val
                changed = True
        new_canonical = mint_canonical(existing["code"], ids=_entity_ids(existing))
        if new_canonical != existing.get("canonical_urn"):
            existing["canonical_urn"] = new_canonical
            changed = True
        existing["last_seen"] = now
        self._flush()
        status = "updated" if changed else "unchanged"
        if changed:
            self._log("entity.update", key, {"source": source, "url": existing.get("url")})
        return dict(existing, status=status)

    def ingest_edge(self, *, subject: str, connection: str, obj: str,
                    basis: str = "", url: str = "", source: str = "ingest") -> dict:
        """Add or update one typed edge. Idempotent on (subject, connection, obj)."""
        if not is_connection(connection):
            raise ValueError(f"unknown connection {connection!r}")
        key = self._edge_key(subject, connection, obj)
        now = _now()
        existing = self.edges.get(key)
        if existing is None:
            rec = {"subject": subject, "connection": connection, "object": obj,
                   "basis": basis, "url": url, "source": source,
                   "first_seen": now, "last_seen": now}
            self.edges[key] = rec
            self._flush()
            self._log("edge.create", key, {"source": source})
            return dict(rec, status="created")
        if basis and not existing.get("basis"):
            existing["basis"] = basis
        existing["last_seen"] = now
        self._flush()
        return dict(existing, status="updated")

    def ingest_from_extraction(self, candidates: Iterable[dict], *,
                               source: str = "ingest") -> dict:
        """Bulk ingest of entities a scan/extraction surfaced. Each candidate:
        ``{"code","name","kind","url",[domains],[jurisdiction]}``. Returns a
        per-candidate status summary — the hook the folder ingestor calls when it
        finds an organisation or law not yet in the corpus."""
        results = []
        for c in candidates:
            try:
                r = self.ingest_entity(
                    code=c["code"], name=c.get("name", c["code"]),
                    kind=c.get("kind", "instrument"), url=c.get("url"),
                    jurisdiction=c.get("jurisdiction"),
                    domains=c.get("domains", ()), source=source,
                    ids=_candidate_ids(c))
                results.append({"code": c["code"], "status": r["status"]})
            except (KeyError, ValueError) as exc:
                results.append({"code": c.get("code", "?"), "status": "rejected",
                                "error": str(exc)})
        return {"ingested": results,
                "created": sum(r["status"] == "created" for r in results),
                "updated": sum(r["status"] == "updated" for r in results),
                "rejected": sum(r["status"] == "rejected" for r in results)}

    # ── retrieval-facing queries ──────────────────────────────────────────────
    def search(self, *, kind: Optional[str] = None, domain: Optional[str] = None,
               jurisdiction: Optional[str] = None) -> list[dict]:
        out = []
        for r in self.entities.values():
            if kind and r["kind"] != kind:
                continue
            if domain and domain not in r.get("domains", []):
                continue
            if jurisdiction and r.get("jurisdiction") != jurisdiction:
                continue
            out.append(r)
        return out

    def urls(self) -> list[dict]:
        """The retrievable corpus: code, kind, name, URL, domains for every entity
        that has a URL. This is what a retrieval/grounding step ingests."""
        return [{"code": r["code"], "kind": r["kind"], "name": r["name"],
                 "url": r["url"], "domains": r.get("domains", [])}
                for r in self.entities.values() if r.get("url")]

    def to_world_map(self) -> WorldMap:
        """Materialise the persisted corpus as a WorldMap (for reach/projection)."""
        w = WorldMap()
        for r in self.entities.values():
            w.add(Entity(code=r["code"], name=r["name"], kind=EntityKind(r["kind"]),
                         url=r.get("url"), jurisdiction=r.get("jurisdiction"),
                         domains=tuple(r.get("domains", [])), region=r.get("region", ""),
                         source=r.get("source", "ingest"), facets=r.get("facets", {})))
        for r in self.edges.values():
            w.connect(r["subject"], Connection(r["connection"]), r["object"],
                      basis=r.get("basis", ""), url=r.get("url", ""),
                      source=r.get("source", "ingest"))
        return w


def _persist_world(reg: EntityRegistry, w, *, source: str) -> None:
    """Bulk-persist a world map: per-record flushing is O(n²) on file writes
    for a ~900-record corpus, so flushing is deferred to one write at the end.
    Audit events still fire per record (the log is append-only anyway)."""
    reg._defer_flush = True
    try:
        for e in w.entities.values():
            reg.ingest_entity(code=e.code, name=e.name, kind=e.kind.value, url=e.url,
                              jurisdiction=e.jurisdiction, domains=e.domains,
                              region=e.region, source=source, facets=e.facets)
        for ed in w.edges:
            reg.ingest_edge(subject=ed.subject, connection=ed.connection.value,
                            obj=ed.object, basis=ed.basis, url=ed.url, source=source)
        reg._log("corpus.bulk", source, {
            "source": source, "entities": len(reg.entities),
            "edges": len(reg.edges)})
    finally:
        reg._defer_flush = False
        reg._flush()


def _stamp_catalogue_ids(reg: EntityRegistry) -> None:
    """Attach the external identifiers the instrument catalogue knows to seeded
    entities, so a work is namespace-addressed — its canonical URN keyed on the
    identifier the catalogue holds — from the start, not only after a document
    scan cites it. A work the catalogue knows no identifier for keeps its
    ``source`` key. Additive; never blocks folder setup."""
    try:
        from .corpus.ingest import ids_for_code
    except Exception:                                           # noqa: BLE001
        return
    reg._defer_flush = True
    try:
        for r in list(reg.entities.values()):
            ext = ids_for_code(r["code"])
            if ext:
                reg.ingest_entity(code=r["code"], name=r["name"], kind=r["kind"], ids=ext)
    finally:
        reg._defer_flush = False
        reg._flush()


def seed_registry(folder: str | Path, *, log_root: Optional[str | Path] = None,
                  enriched: bool = True) -> EntityRegistry:
    """Persist the digital-law seed world into a folder's corpus (idempotent), so
    a fresh folder starts with the acquis + regulators + standards bodies and can
    grow from there.

    ``enriched`` (default) also
    persists the full reference corpus (world_corpus_loader) run through
    ``world_relations.enrich`` — memberships, party_to/bound_by, regulator
    ``enforces``, adequacy ``equivalent_to``, instrument lineage and
    ``presumes_conformity`` edges, each carrying its ``basis``. Entities stop
    floating: a span anchored to an instrument resolves onward to the regulator
    that enforces it and the orders it binds. Failure of the enrichment pass
    falls back to the bare seed rather than blocking folder setup."""
    from .adapters.legal import seed_world
    reg = EntityRegistry(folder, log_root=log_root)
    _persist_world(reg, seed_world(), source="seed")
    if enriched:
        try:
            from .world_relations import build_enriched_world
            w, _stats = build_enriched_world()
            _persist_world(reg, w, source="seed")
        except Exception:                                       # noqa: BLE001
            pass    # relational pass is additive; never block folder setup on it
    _stamp_catalogue_ids(reg)
    return reg
