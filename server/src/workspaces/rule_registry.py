# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Rule registry — every span-norm placed onto the legal map, per workspace and per user.

The unit is the **span**: one contiguous passage of source text (a contract
clause, a statutory sentence, a Randnummer) carries exactly **one norm**. This
registry takes each span-norm and *places* it onto the legal-entity map — anchors
it to the instruments it cites, the jurisdiction those instruments apply in, and
the regulators that enforce them — then persists it in two scopes:

  * **per workspace** — ``<folder>/legal-corpus/rule-items.jsonl`` (next to the entity
    corpus), so the folder's own rules travel with the folder;
  * **per user** — ``~/.workspace/log/rule-registry.jsonl``, tagged with the originating
    workspace, so a user can ask "every payment-term clause I hold across all my
    projects" or "every span anchored to the GDPR".

Anchoring reuses what already exists: ``rule_extractor.extract_rules`` gives one
``RuleFacet`` per span (per-span = per-norm), ``corpus_ingest`` recognises the
instruments a span cites, and the world map resolves instrument → jurisdiction →
enforcing regulator. So placing a rule is the same recognition the corpus and
cross-reference graph already run, indexed the other way: from the rule to the
entities that govern it.

Idempotent (keyed by source + span text), provenance-stamped, and audited via the
signed mutation log. Pure stdlib.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .rule_extractor import extract_rules, RuleFacet
from .corpus.ingest import candidates_from_text
from .legal_connection import Connection
from .legal_world import EntityKind, WorldMap
from .mutation_log import MutationLog, LogEvent, LOG_ROOT_DEFAULT
from .urn import mint_canonical


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rule_id(source_document: str, span_text: str) -> str:
    h = hashlib.sha256()
    h.update((source_document or "inline").encode("utf-8"))
    h.update(b"|")
    h.update(span_text.strip().encode("utf-8"))
    return "rule:" + h.hexdigest()[:24]


def _span_urn(rid: str) -> str:
    """The span's address on the identity spine, minted from its content-keyed
    id — deterministic per (document, pinpoint, text) and stable across
    reanchoring, which mutates the record in place."""
    return mint_canonical("rule-" + rid.partition(":")[2])


@dataclass
class Anchor:
    entity: str               # entity code in the legal map
    kind: str                 # instrument | jurisdiction | regulator
    relation: str             # cites | governed_by | enforced_by
    basis: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SpanNorm:
    id: str
    span: dict                 # {document, start, end, text}
    norm: dict                 # {modal, subject, action, condition, exception, language}
    anchors: list              # [Anchor-dict]
    kind: str = "rule"         # rule | clause
    workspace: str = ""
    user: str = ""
    source: str = "ingest"
    canonical_urn: str = ""    # the shared identity spine (minted from the id)
    obligation_urns: list = field(default_factory=list)   # compiles_to targets
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _norm_from_facet(f: RuleFacet) -> dict:
    return {"modal": f.modal, "subject": f.subject, "action": f.action,
            "condition": f.condition, "exception": f.exception,
            "language": f.language,
            # juridical-primitive layer (rule DNA; "" = honest abstention)
            "incident": getattr(f, "incident", ""),
            "counterparty": getattr(f, "counterparty", ""),
            "condition_kind": getattr(f, "condition_kind", "")}


# ── anchoring: place a span onto the legal entities that govern it ────────────

def _resolve_world(folder: Optional[str]) -> WorldMap:
    """The legal map to anchor against: the folder's persisted corpus if it has
    one, else the digital-law seed."""
    if folder:
        try:
            from .legal_corpus import EntityRegistry
            reg = EntityRegistry(folder)
            if reg.entities:
                return reg.to_world_map()
        except Exception:                                      # noqa: BLE001
            pass
    from .legal_world import seed_world
    return seed_world()


def anchors_for(span_text: str, world: WorldMap) -> list[Anchor]:
    """The legal entities a span-norm is placed at: every instrument it cites,
    each instrument's jurisdiction, and the regulators that enforce it."""
    out: list[Anchor] = []
    seen: set[tuple] = set()

    def _add(entity: str, kind: str, relation: str, basis: str = "") -> None:
        key = (entity, relation)
        if key not in seen:
            seen.add(key)
            out.append(Anchor(entity, kind, relation, basis))

    for cand in candidates_from_text(span_text):
        code = cand["code"]
        # the cited instrument — basis is the section pinpoint when present
        # (e.g. "§ 286 Abs. 1"), else the instrument name.
        _add(code, "instrument", "cites", cand.get("pinpoint") or cand.get("name", ""))
        # jurisdiction straight from the candidate (works for national statutes
        # the seed world has never heard of, e.g. BGB → DE).
        if cand.get("jurisdiction"):
            _add(cand["jurisdiction"], "jurisdiction", "governed_by", "owning order")
        ent = world.get(code)
        if ent is None:
            continue
        # jurisdiction the instrument applies in (from the map, when present)
        for ed in world.edges:
            if ed.subject == code and ed.connection is Connection.APPLIES_IN:
                _add(ed.object, "jurisdiction", "governed_by", ed.basis)
        # regulators that enforce the instrument
        for ed in world.edges:
            if ed.object == code and ed.connection is Connection.ENFORCES:
                _add(ed.subject, "regulator", "enforced_by", ed.basis)
    return out


def _host_instrument_anchors(code: str, world: WorldMap) -> dict:
    """Resolve a host instrument's jurisdiction(s) and enforcing regulators once,
    so a per-provision anchor set can be built cheaply."""
    jurisdictions: list[str] = []
    regulators: list[str] = []
    ent = world.get(code)
    if ent is not None and ent.jurisdiction:
        jurisdictions.append(ent.jurisdiction)
    for ed in world.edges:
        if ed.subject == code and ed.connection is Connection.APPLIES_IN and ed.object not in jurisdictions:
            jurisdictions.append(ed.object)
        if ed.object == code and ed.connection is Connection.ENFORCES and ed.subject not in regulators:
            regulators.append(ed.subject)
    return {"code": code, "jurisdictions": jurisdictions, "regulators": regulators}


def _host_anchor_dicts(host: dict, pinpoint: str) -> list[dict]:
    """Anchor dicts for a provision of the host instrument: cites the instrument
    (basis = the article pinpoint), governed_by its jurisdiction, enforced_by its
    regulators."""
    out = [Anchor(host["code"], "instrument", "cites", pinpoint).to_dict()]
    for j in host["jurisdictions"]:
        out.append(Anchor(j, "jurisdiction", "governed_by", "owning order").to_dict())
    for r in host["regulators"]:
        out.append(Anchor(r, "regulator", "enforced_by", "mandate").to_dict())
    return out


class RuleRegistry:
    """Per-workspace + per-user store of span-norms placed on the legal map."""

    def __init__(self, folder: str | Path, *, user: str = "",
                 user_root: Optional[str | Path] = None,
                 log_root: Optional[str | Path] = None):
        from .folder_context import resolve_folder_context

        self.folder = Path(resolve_folder_context(folder))
        self.user = user
        self.user_root = Path(user_root) if user_root else LOG_ROOT_DEFAULT
        self.log_root = Path(log_root) if log_root else None
        self.items: dict[str, dict] = {}     # id -> record (workspace scope)
        self.load()

    # ── persistence ───────────────────────────────────────────────────────────
    def _workspace_path(self) -> Path:
        return self.folder / "legal-corpus" / "rule-items.jsonl"

    def _user_path(self) -> Path:
        return self.user_root / "rule-registry.jsonl"

    def load(self) -> None:
        p = self._workspace_path()
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.items[r["id"]] = r
        # Records written before the identity spine self-heal on open, the
        # same way the entity corpus and the grounding works do.
        dirty = False
        for rid, r in self.items.items():
            if not r.get("canonical_urn"):
                r["canonical_urn"] = _span_urn(rid)
                r.setdefault("obligation_urns", [])
                dirty = True
        if dirty:
            self._flush_workspace()

    def _flush_workspace(self) -> None:
        p = self._workspace_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                               for r in self.items.values()) + "\n", encoding="utf-8")

    def _mirror_user(self, rec: dict) -> None:
        """Append/refresh the rule in the per-user store, tagged with its workspace.
        Keyed by (user, workspace, id) so the same rule from two workspaces coexists."""
        try:
            up = self._user_path()
            up.parent.mkdir(parents=True, exist_ok=True)
            rows: dict[tuple, dict] = {}
            if up.exists():
                for line in up.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        r = json.loads(line)
                        rows[(r.get("user", ""), r.get("workspace", ""), r["id"])] = r
            rows[(rec.get("user", ""), rec.get("workspace", ""), rec["id"])] = rec
            up.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                    for r in rows.values()) + "\n", encoding="utf-8")
        except Exception:                                      # noqa: BLE001
            pass     # user mirror is best-effort

    def _log(self, rec: dict) -> Optional[str]:
        try:
            log = MutationLog(self.folder, log_root=self.log_root)
            return log.append(LogEvent(
                event="ingest", folder_path=str(self.folder), pair_id=rec["id"],
                channel="document", actor=rec.get("source", "ingest"),
                extra={"kind": "rule-item", "anchors": [a["entity"] for a in rec["anchors"]],
                       "modal": rec["norm"].get("modal")}))
        except Exception:                                      # noqa: BLE001
            return None

    # ── placement ─────────────────────────────────────────────────────────────
    def place_span(self, span_text: str, *, source_document: str = "",
                   start: Optional[int] = None, end: Optional[int] = None,
                   kind: str = "rule", world: Optional[WorldMap] = None,
                   facet: Optional[RuleFacet] = None, source: str = "ingest",
                   anchors: Optional[list] = None, pinpoint: str = "",
                   document_hash: str = "",
                   document_version: Optional[int] = None) -> dict:
        """Place one span (= one norm) onto the legal map and persist it.

        ``anchors`` (precomputed) override text-derived anchoring — used when the
        host instrument is known (ingesting a law's own articles). ``pinpoint``
        (e.g. ``Art. 17(3)``) is recorded on the span. ``document_hash`` /
        ``document_version`` pin the span to one version of its source document
        so an amendment can re-anchor (or orphan) it explicitly — see
        :meth:`reanchor_document`."""
        world = world or _resolve_world(str(self.folder))
        if facet is None:
            facets = extract_rules(span_text)
            facet = facets[0] if facets else RuleFacet(raw_sentence=span_text)
        # Rule-DNA completeness (audit round 3): every span-norm carries the
        # juridical-primitive layer regardless of which path created it —
        # already-enriched facets (contract intake, role-aware) are skipped.
        from .hohfeld import attach_incidents
        attach_incidents([facet])
        rid = _rule_id(source_document + "|" + pinpoint, span_text)
        now = _now()
        existing = self.items.get(rid)
        if anchors is None:
            anchors = [a.to_dict() for a in anchors_for(span_text, world)]
        if existing is None:
            rec = SpanNorm(
                id=rid,
                span={"document": source_document, "start": start, "end": end,
                      "pinpoint": pinpoint, "text": span_text.strip(),
                      "document_hash": document_hash,
                      "document_version": document_version},
                norm=_norm_from_facet(facet), anchors=anchors, kind=kind,
                workspace=str(self.folder), user=self.user, source=source,
                canonical_urn=_span_urn(rid),
                first_seen=now, last_seen=now).to_dict()
            self.items[rid] = rec
            self._flush_workspace()
            self._mirror_user(rec)
            self._log(rec)
            return dict(rec, status="created")
        existing["last_seen"] = now
        if anchors and not existing.get("anchors"):
            existing["anchors"] = anchors
        if not existing.get("canonical_urn"):
            existing["canonical_urn"] = _span_urn(rid)
        self._flush_workspace()
        self._mirror_user(existing)
        return dict(existing, status="updated")

    def reanchor_document(self, source_document: str, new_text: str, *,
                          new_hash: str, new_version: int,
                          actor: str = "ingest") -> dict:
        """Migrate this document's spans to a new version of its text.

        For every span of ``source_document``: if the span text still occurs in
        ``new_text``, the record is re-pinned (new offsets, new hash/version);
        if it does not, the record is marked ``orphaned`` — it is NEVER silently
        dropped, and an orphan is an ESCALATE for the decision surface (the
        clause may have been amended, moved, or deleted; which one is a human
        call). Returns ``{migrated, orphaned, untouched}`` with ids."""
        migrated: list[str] = []
        orphaned: list[str] = []
        now = _now()
        for rid, rec in self.items.items():
            span = rec.get("span") or {}
            if span.get("document") != source_document:
                continue
            if span.get("document_version") == new_version:
                continue
            text = span.get("text") or ""
            idx = new_text.find(text) if text else -1
            if idx >= 0:
                span.update({"start": idx, "end": idx + len(text),
                             "document_hash": new_hash,
                             "document_version": new_version})
                rec.pop("orphaned", None)
                rec["last_seen"] = now
                migrated.append(rid)
            else:
                rec["orphaned"] = {"at_version": new_version, "hash": new_hash,
                                   "marked": now}
                rec["last_seen"] = now
                orphaned.append(rid)
            self._log(rec)
        if migrated or orphaned:
            self._flush_workspace()
        untouched = [rid for rid, rec in self.items.items()
                     if (rec.get("span") or {}).get("document") == source_document
                     and rid not in migrated and rid not in orphaned]
        return {"document": source_document, "new_version": new_version,
                "migrated": migrated, "orphaned": orphaned,
                "untouched": untouched,
                "escalate": bool(orphaned)}

    def orphans(self, source_document: str = "") -> list[dict]:
        """Spans stranded by a document update — the decision-surface feed."""
        out = []
        for rec in self.items.values():
            if not rec.get("orphaned"):
                continue
            if source_document and (rec.get("span") or {}).get("document") != source_document:
                continue
            out.append(rec)
        return out

    def place_legal_text(self, content: str, instrument_code: str, *,
                         source_document: str = "", world: Optional[WorldMap] = None,
                         source: str = "ingest") -> dict:
        """Ingest a law's own text: cut it into provisions, extract the norm(s) in
        each, and place every one as an individual span-norm anchored to the host
        instrument (``cites`` with the article pinpoint as basis) plus its
        jurisdiction and enforcing regulators. This is how the norms *inside* a law
        enter the ND-rule map individually, not just clauses that cite it."""
        from .legal_norm_splitter import segment_provisions
        world = world or _resolve_world(str(self.folder))
        host = _host_instrument_anchors(instrument_code, world)
        placed: list[dict] = []
        for prov in segment_provisions(content):
            # one law, many norms: do NOT fingerprint-dedupe across provisions —
            # every article's operative norm must enter the map individually.
            for f in extract_rules(prov.text, gated_by_fingerprint=False):
                span = (f.raw_sentence or "").strip()
                if not span:
                    continue
                # host anchor (with this provision's pinpoint) + any in-text cross-refs
                anchors = _host_anchor_dicts(host, prov.pinpoint)
                anchors += [a.to_dict() for a in anchors_for(span, world)
                            if a.entity != instrument_code]
                r = self.place_span(span, source_document=source_document,
                                    kind="norm", world=world, facet=f, source=source,
                                    anchors=anchors, pinpoint=prov.pinpoint)
                placed.append({"id": r["id"], "status": r["status"],
                               "pinpoint": prov.pinpoint, "modal": r["norm"].get("modal")})
        return {"instrument": instrument_code, "placed": placed, "count": len(placed),
                "created": sum(p["status"] == "created" for p in placed),
                "provisions": len({p["pinpoint"] for p in placed})}

    def place_document(self, content: str, *, source_document: str = "",
                       kind: str = "rule", world: Optional[WorldMap] = None,
                       source: str = "ingest") -> dict:
        """Extract one norm per span from a document and place each. Returns a
        summary with the placed rule ids."""
        world = world or _resolve_world(str(self.folder))
        placed = []
        for f in extract_rules(content):
            span = f.raw_sentence or ""
            if not span.strip():
                continue
            idx = content.find(span)
            r = self.place_span(span, source_document=source_document,
                                start=idx if idx >= 0 else None,
                                end=(idx + len(span)) if idx >= 0 else None,
                                kind=kind, world=world, facet=f, source=source)
            placed.append({"id": r["id"], "status": r["status"],
                           "anchors": [a["entity"] for a in r["anchors"]]})
        return {"placed": placed, "count": len(placed),
                "created": sum(p["status"] == "created" for p in placed)}

    # ── queries ───────────────────────────────────────────────────────────────
    def rules_at(self, entity_code: str) -> list[dict]:
        """Reverse index: every span-norm placed at a given legal entity."""
        return [r for r in self.items.values()
                if any(a["entity"] == entity_code for a in r["anchors"])]

    def search(self, *, modal: Optional[str] = None,
               relation: Optional[str] = None) -> list[dict]:
        out = []
        for r in self.items.values():
            if modal and r["norm"].get("modal") != modal:
                continue
            if relation and not any(a["relation"] == relation for a in r["anchors"]):
                continue
            out.append(r)
        return out

    def workspace_items(self) -> list[dict]:
        return list(self.items.values())

    def user_items(self, *, user: Optional[str] = None) -> list[dict]:
        """Every span-norm in the per-user store (across workspaces), optionally
        filtered to one user."""
        up = self._user_path()
        if not up.exists():
            return []
        rows = [json.loads(l) for l in up.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [r for r in rows if user is None or r.get("user", "") == user]


def place_into_registry(folder: str, content: str, *, user: str = "",
                        source_document: str = "", log_root: Optional[str] = None,
                        source: str = "ingest") -> dict:
    """Best-effort hook for the ingest pipeline: place every span-norm in a
    document onto the legal map, per workspace + per user. Never raises into caller.

    If the document IS a legal instrument (a host instrument is recognised), route
    to article-aware extraction so each provision's norm enters the map
    individually; otherwise treat it as a third-party document (clauses that cite
    laws)."""
    try:
        reg = RuleRegistry(folder, user=user, log_root=log_root)
        from .corpus.ingest import _CODE_ALIASES
        from .crossref_extractor import infer_host_instrument
        host = infer_host_instrument(content)
        if host:
            code = _CODE_ALIASES.get(host, host)
            return reg.place_legal_text(content, code,
                                        source_document=source_document, source=source)
        return reg.place_document(content, source_document=source_document, source=source)
    except Exception as exc:                                   # noqa: BLE001
        return {"placed": [], "error": f"{type(exc).__name__}: {exc}"}
