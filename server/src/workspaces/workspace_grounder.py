# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace Grounder — citation, grounding, and provenance for everything web-grounded.

Standard workspace tool beside Workspace Lock (the privacy boundary) and Workspace Oversight
(the judgment boundary): the Grounder is the **attribution boundary**. Its
invariant: *no citation, no claim* — any web-grounded statement that enters a
workspace must carry at least one registered work with creator attribution. The goal
is ethical and legal: honor the creators whose works ground the answer.

Three research modes are orchestrated by the workspace-grounder skill (agents do the
reading; this module is the deterministic ledger they report into):

  * **researcher** — single-pass grounding of one question;
  * **twin** — two independent passes over the same question, cross-checked;
    agreement promotes a claim to ``verified``, disagreement marks it
    ``disputed`` and surfaces it as a residual for the human;
  * **swarm** — fan-out that follows the citations *inside* sources, tracing
    the provenance of ideas back through works to entities (creators,
    publishers, instruments). ``frontier()`` tells the swarm which works still
    have untraced citations; ``trace()`` walks a work back to its roots.

Citation styles are user-choosable (APA, MLA, Chicago, Harvard, IEEE,
Vancouver); formatting is deterministic string-building, never invention —
missing fields stay missing rather than being guessed.

Storage per workspace: ``<folder>/grounding/{works,claims,provenance}.jsonl``.
Idempotent (content-keyed ids), audited via the signed mutation log (including
refusals), pure stdlib.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .mutation_log import MutationLog, LogEvent
from .urn import mint_canonical

try:                                            # Unix only, like mutation_log
    import fcntl
except ImportError:                             # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

CITATION_STYLES = ("apa", "mla", "chicago", "harvard", "ieee", "vancouver")

WORK_TYPES = ("web", "article", "book", "chapter", "statute", "case",
              "standard", "report", "dataset", "preprint", "thesis",
              "audio", "video", "software", "policy", "other")

PROVENANCE_RELATIONS = ("cites", "quotes", "derives_from", "republishes",
                        "translates", "summarizes", "responds_to", "grounds")

CLAIM_STATUSES = ("asserted", "verified", "disputed", "retracted")

RESEARCH_MODES = ("researcher", "twin", "swarm")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _year(date: str) -> str:
    """First 4-digit run in a date string, else 'n.d.'."""
    run = ""
    for ch in date or "":
        if ch.isdigit():
            run += ch
            if len(run) == 4:
                return run
        else:
            run = ""
    return "n.d."


def _work_id(url: str, doi: str, title: str, creators: list) -> str:
    h = hashlib.sha256()
    key = (doi or url or (title + "|" + "|".join(
        c.get("name", "") for c in creators))).strip().lower()
    h.update(key.encode("utf-8"))
    return "work:" + h.hexdigest()[:24]


def _claim_id(text: str) -> str:
    h = hashlib.sha256()
    h.update(text.strip().encode("utf-8"))
    return "claim:" + h.hexdigest()[:24]


def _row_version(rid: str, row: dict) -> str:
    """The monotonic version keying a row's versum identity-upsert: its
    ``last_seen`` (bumped on every mutation), falling back to ``first_seen`` then
    the row id — so an edit supersedes and an unchanged row re-flush is a no-op."""
    return str(row.get("last_seen") or row.get("first_seen") or rid)


def _work_mint_ids(identifiers: Optional[dict], doi: str) -> dict:
    """Addressing identifiers for a work's canonical URN: its external ids minus
    fixity (``sha256``), with the DOI folded in. Neutral — the identifiers' own
    order decides which namespace is canonical."""
    ids = {k: v for k, v in (identifiers or {}).items() if k != "sha256" and v}
    if doi:
        ids.setdefault("doi", doi)
    return ids


@dataclass
class Work:
    """One cited work — the unit of attribution."""

    id: str
    type: str                       # see WORK_TYPES
    title: str
    creators: list                  # [{name, role?}] — role: author|editor|artist|org|...
    container: str = ""             # journal / site / album / official journal
    publisher: str = ""
    date: str = ""                  # as stated by the source; never invented
    url: str = ""
    accessed: str = ""              # when the web source was retrieved
    doi: str = ""
    identifiers: dict = field(default_factory=dict)   # isbn, celex, ecli, isrc, ...
    canonical_urn: str = ""         # the shared identity spine (minted from ids)
    tags: list = field(default_factory=list)   # jurisdiction, topics, ... (facets)
    confidence: str = ""            # the source's own confidence marker, if any
    language: str = ""
    retrieved_by: str = "researcher"                  # which mode found it
    entity_refs: list = field(default_factory=list)   # codes in the entity corpus
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GroundedClaim:
    """One claim bound to the works that ground it."""

    id: str
    text: str
    work_ids: list
    locator: str = ""               # page / section / timestamp / quote pinpoint
    quote: str = ""                 # verbatim supporting passage, if captured
    confidence: float = 0.0
    status: str = "asserted"        # see CLAIM_STATUSES
    method: str = "researcher"      # see RESEARCH_MODES
    agent: str = ""                 # which researcher/twin/swarm member reported
    verified_by: list = field(default_factory=list)   # twin agents that confirmed
    evidence_at_promotion: Optional[bool] = None      # quote/locator present when twin-verified
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── citation formatting — deterministic, never inventive ─────────────────────

def _split_name(name: str) -> tuple[str, str]:
    """(last, first). 'Last, First' kept; else last token is the last name.
    Single-token and org names return (name, '')."""
    name = name.strip()
    if "," in name:
        last, _, first = name.partition(",")
        return last.strip(), first.strip()
    parts = name.split()
    if len(parts) < 2:
        return name, ""
    return parts[-1], " ".join(parts[:-1])


def _initials(first: str) -> str:
    return " ".join(p[0] + "." for p in first.split() if p)


def _names(work: dict, style: str) -> str:
    creators = work.get("creators") or []
    if not creators:
        return ""
    out: list[str] = []
    for i, c in enumerate(creators):
        if c.get("role") in ("org", "organisation", "organization",
                             "publisher", "body", "institution"):
            out.append(c.get("name", "").strip())   # never split an org name
            continue
        last, first = _split_name(c.get("name", ""))
        if style == "apa":
            out.append(f"{last}, {_initials(first)}".rstrip(", ") if first else last)
        elif style in ("mla", "chicago"):
            out.append(f"{last}, {first}" if (i == 0 and first) else
                       (f"{first} {last}".strip()))
        elif style == "harvard":
            out.append(f"{last}, {_initials(first)}".rstrip(", ") if first else last)
        elif style == "ieee":
            out.append(f"{_initials(first)} {last}".strip())
        else:  # vancouver
            out.append(f"{last} {''.join(p[0] for p in first.split())}".strip())
    if len(out) == 1:
        return out[0]
    if style == "apa":
        return ", ".join(out[:-1]) + ", & " + out[-1]
    if style in ("mla", "chicago", "harvard"):
        return ", ".join(out[:-1]) + ", and " + out[-1]
    return ", ".join(out)


def format_citation(work: dict, style: str = "apa") -> str:
    """One formatted citation for a work record. Missing fields are omitted,
    never guessed — an incomplete citation is honest; an invented one is not."""
    if style not in CITATION_STYLES:
        raise ValueError(f"unknown style {style!r}; choose one of {CITATION_STYLES}")
    names = _names(work, style)
    title = (work.get("title") or "").strip()
    container = (work.get("container") or "").strip()
    publisher = (work.get("publisher") or "").strip()
    date = (work.get("date") or "").strip()
    url = (work.get("url") or "").strip()
    doi = (work.get("doi") or "").strip()
    accessed = (work.get("accessed") or "").strip()
    year = _year(date)
    link = ("https://doi.org/" + doi) if doi and not doi.startswith("http") else (doi or url)

    if style == "apa":
        bits = [f"{names} ({date or year})." if names else f"({date or year}).",
                f"{title}." if title else "",
                f"*{container}*." if container else "",
                f"{publisher}." if publisher else "",
                link]
    elif style == "mla":
        bits = [f"{names}." if names else "",
                f"“{title}.”" if title else "",
                f"*{container}*," if container else "",
                f"{publisher}," if publisher else "",
                f"{date or year},",
                f"{link}." if link else ""]
    elif style == "chicago":
        bits = [f"{names}." if names else "",
                f"“{title}.”" if title else "",
                f"*{container}*." if container else "",
                f"{publisher}, {date or year}." if publisher else f"{date or year}.",
                f"{link}." if link else ""]
    elif style == "harvard":
        bits = [f"{names} ({year})" if names else f"({year})",
                f"*{title}*." if title else "",
                f"{container}." if container else "",
                f"{publisher}." if publisher else "",
                f"Available at: {link}" if link else "",
                f"(Accessed: {accessed})." if accessed else ""]
    elif style == "ieee":
        bits = [f"{names}," if names else "",
                f"“{title},”" if title else "",
                f"*{container}*," if container else "",
                f"{publisher}," if publisher else "",
                f"{date or year}.",
                f"[Online]. Available: {link}" if link else ""]
    else:  # vancouver
        bits = [f"{names}." if names else "",
                f"{title}." if title else "",
                f"{container}." if container else "",
                f"{publisher};" if publisher else "",
                f"{date or year}.",
                f"Available from: {link}" if link else ""]
    return " ".join(b for b in bits if b).strip()


# ── the ledger ────────────────────────────────────────────────────────────────

class GroundingLedger:
    """Per-workspace store of works, grounded claims, and provenance edges."""

    def __init__(self, folder: str | Path, *,
                 log_root: Optional[str | Path] = None):
        self.folder = Path(folder)
        self.log_root = Path(log_root) if log_root else None
        self.works: dict[str, dict] = {}
        self.claims: dict[str, dict] = {}
        self.provenance: dict[str, dict] = {}   # edge-key -> record
        # dirty-skip: node-key -> last-written version, so a _flush re-appends
        # only the row(s) that actually changed (last_seen bumped) instead of the
        # whole dict — keeps writes O(changed), not O(n) per mutation.
        self._written: dict[str, str] = {}
        self._txn_seen: int = -1          # sink fingerprint at last load (reload gate)
        self.load()

    # ── persistence ───────────────────────────────────────────────────────────
    def _dir(self) -> Path:
        return self.folder / "grounding"

    @contextlib.contextmanager
    def batch(self):
        """Hold the write lock across many mutations and flush once on exit.

        A bulk import calling ``register_work`` per row otherwise reloads state
        and rewrites the JSONL files on every call — O(n^2) file writes over a
        large registry. Inside a batch the per-call lock/reload and flushes are
        suppressed; the lock is taken once, state loaded once, and the three
        stores flushed once at the end. Audit events still fire per record
        (the mutation log is append-only)."""
        with self._write_lock():
            self._in_batch = True
            try:
                yield self
            finally:
                self._in_batch = False
                self._flush("works.jsonl", self.works)
                self._flush("claims.jsonl", self.claims)
                self._flush("provenance.jsonl", self.provenance)

    @contextlib.contextmanager
    def _write_lock(self):
        """Per-folder write lock held across load-mutate-flush.

        Two grounding processes on the same folder serialise instead of losing
        writes. State is reloaded under the lock to pick up another writer's
        flushes — so a deferred flush must suppress this reload too, or the
        reload would drop the unflushed rows (see ``batch``). No-op where fcntl
        is unavailable.
        """
        if getattr(self, "_in_batch", False):   # lock already held by batch()
            yield
            return
        if fcntl is None:                       # pragma: no cover - Windows
            yield
            return
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / ".lock", "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                # Reload only when the sink actually changed under us (another
                # writer). Re-reading + revalidating every versum transaction on
                # every mutation is O(n) per call → O(n²) over a batch; a cheap
                # fingerprint keeps a single writer's own sequential writes O(1)
                # here (its flush updates the fingerprint) while a concurrent
                # writer's new transaction still triggers a reload.
                if self._sink_fingerprint() != self._txn_seen:
                    self.load()                 # see another writer's state
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _sink_fingerprint(self) -> int:
        """An O(1) change-detector for the versum sink: the mtime (ns) of its
        append-only transaction directory, which advances whenever any writer adds
        or removes a transaction. Counting files instead would be O(n) per
        write-lock → O(n²) over a bulk import; a single stat keeps the reload gate
        cheap while still catching a concurrent writer's change."""
        # the sink's transaction dir name is versum's (``ingestion.subgraph``); the
        # grounder already writes through the adapters.versum seam, so keying the
        # change-token off that dir is within the same consumed boundary.
        txn_dir = self._versum_store() / "_dimensioned_subgraph_transactions"
        try:
            return os.stat(txn_dir).st_mtime_ns
        except OSError:
            return 0

    # works/claims/provenance no longer live in a local JSONL store — they are
    # CONSUMED from the folder's versum sink (the canonical knowledge plane) as
    # identity-upsert records. The store is dedicated to the grounder
    # (``grounding/.versum``); each row rides losslessly as a versum record body
    # wrapped ``{"id": "<kind>:<row-id>", "_kind": <kind>, "_row": <row>}`` and
    # is keyed for supersede-in-place by the row's monotonic ``last_seen``
    # (versum ``identity=True``). The three in-memory dicts remain the working
    # projection every method reads/mutates; only the persistence layer moved.
    _STORE_KIND = {"works.jsonl": "work", "claims.jsonl": "claim",
                   "provenance.jsonl": "provenance"}
    _KIND_KEYFIELD = {"work": "id", "claim": "id", "provenance": "key"}

    def _versum_store(self) -> Path:
        return self._dir() / ".versum"

    def _load_jsonl(self, name: str, key: str) -> dict[str, dict]:
        p = self._dir() / name
        out: dict[str, dict] = {}
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    out[r[key]] = r
        return out

    def load(self) -> None:
        """Rebuild the three projections from the folder's versum sink (identity
        records, latest-wins), then self-heal any pre-versum JSONL store."""
        self.works, self.claims, self.provenance = {}, {}, {}
        self._written = {}
        buckets = {"work": self.works, "claim": self.claims,
                   "provenance": self.provenance}
        try:
            from .adapters.versum import iter_records
            for rec in iter_records(self._versum_store()):
                props = rec.get("properties") if isinstance(rec, dict) else None
                body = props.get("record") if isinstance(props, dict) else None
                if not isinstance(body, dict):
                    continue
                kind, row = body.get("_kind"), body.get("_row")
                if kind in buckets and isinstance(row, dict):
                    k = row.get(self._KIND_KEYFIELD[kind])
                    if k:
                        buckets[kind][str(k)] = row
                        self._written[f"{kind}:{k}"] = _row_version(k, row)
        except Exception:                                       # noqa: BLE001
            pass
        self._migrate_legacy_jsonl(buckets)
        self._txn_seen = self._sink_fingerprint()

    def _migrate_legacy_jsonl(self, buckets: dict[str, dict]) -> None:
        """One-time self-heal: import a pre-versum ``grounding/*.jsonl`` store into
        the versum sink for any row not already present. Idempotent — once
        migrated, subsequent loads read everything from versum and this adds
        nothing."""
        legacy = {"work": ("works.jsonl", "id"), "claim": ("claims.jsonl", "id"),
                  "provenance": ("provenance.jsonl", "key")}
        for kind, (fname, key) in legacy.items():
            rows = self._load_jsonl(fname, key)
            missing = {k: r for k, r in rows.items() if k not in buckets[kind]}
            if not missing:
                continue
            buckets[kind].update(missing)
            self._write_rows(kind, missing)

    def _write_rows(self, kind: str, rows: dict[str, dict]) -> None:
        """Write-through: persist the CHANGED rows of this kind to the versum sink
        as identity-upsert records (supersede-in-place, keyed by monotonic
        ``last_seen``) in ONE batched transaction — one durable write for the whole
        flush, not one per row. Unchanged rows (``last_seen`` not advanced since the
        last write) are skipped."""
        keyfield = self._KIND_KEYFIELD[kind]
        batch: list[dict] = []
        marks: list[tuple[str, str]] = []
        for row in rows.values():
            rid = str(row.get(keyfield) or "")
            if not rid:
                continue
            version = _row_version(rid, row)
            id_key = f"{kind}:{rid}"
            if self._written.get(id_key) == version:
                continue                                # unchanged since last write
            batch.append({"record": {"id": id_key, "_kind": kind, "_row": row},
                          "version": version})
            marks.append((id_key, version))
        if not batch:
            return
        try:
            from .adapters.versum import append_records
        except Exception:                                       # noqa: BLE001
            return
        store = self._versum_store()
        store.mkdir(parents=True, exist_ok=True)
        try:
            append_records(store, records=batch, dimension="relational",
                           actor="grounder")
            for id_key, version in marks:
                self._written[id_key] = version
            # my own writes are now reflected — don't let the next _write_lock
            # treat them as another writer's change and trigger a full reload.
            self._txn_seen = self._sink_fingerprint()
        except Exception:                                       # noqa: BLE001
            pass

    def _flush(self, name: str, rows: dict[str, dict]) -> None:
        if getattr(self, "_in_batch", False):   # batch() flushes once on exit
            return
        self._write_rows(self._STORE_KIND[name], rows)

    def _log(self, event: str, pair_id: str, extra: dict,
             actor: str = "grounder") -> Optional[str]:
        try:
            # One MutationLog per ledger: the log's tail cache makes repeated
            # appends cheap only on a reused instance (bulk imports).
            log = getattr(self, "_mutation_log", None)
            if log is None:
                log = self._mutation_log = MutationLog(self.folder,
                                                       log_root=self.log_root)
            return log.append(LogEvent(
                event=event, folder_path=str(self.folder), pair_id=pair_id,
                channel="document", actor=actor, extra=extra))
        except Exception:                                       # noqa: BLE001
            return None

    # ── public mutating surface — serialised by the folder write lock ─────────
    def register_work(self, **kw) -> dict:
        with self._write_lock():
            return self._register_work(**kw)

    def ground_claim(self, text: str, work_ids: list, **kw) -> dict:
        with self._write_lock():
            return self._ground_claim(text, work_ids, **kw)

    def set_claim_status(self, claim_id: str, status: str, **kw) -> dict:
        with self._write_lock():
            return self._set_claim_status(claim_id, status, **kw)

    def add_provenance(self, from_work: str, relation: str, to_work: str,
                       **kw) -> dict:
        with self._write_lock():
            return self._add_provenance(from_work, relation, to_work, **kw)

    def link_creators_to_corpus(self) -> dict:
        with self._write_lock():
            return self._link_creators_to_corpus()

    def forget_subject(self, name: str) -> dict:
        with self._write_lock():
            return self._forget_subject(name)

    # ── works ─────────────────────────────────────────────────────────────────
    def _match_identity(self, *, url: str, doi: str) -> Optional[dict]:
        """An existing record that shares this work's DOI or URL, if any."""
        u = (url or "").strip()
        d = (doi or "").strip().lower()
        for rec in self.works.values():
            if d and rec.get("doi", "").strip().lower() == d:
                return rec
            if u and rec.get("url", "").strip() == u:
                return rec
        return None

    def _register_work(self, *, title: str, type: str = "web",
                       creators: Optional[list] = None, container: str = "",
                       publisher: str = "", date: str = "", url: str = "",
                       accessed: str = "", doi: str = "",
                       identifiers: Optional[dict] = None, language: str = "",
                       retrieved_by: str = "researcher",
                       entity_refs: Optional[list] = None,
                       tags: Optional[list] = None, confidence: str = "",
                       content: Optional[str] = None) -> dict:
        """Add or refresh one work. Idempotent on (doi | url | title+creators).
        Creators may be given as strings or {name, role} dicts. ``content``
        (the retrieved page/document text, not stored) is hashed into
        ``identifiers.sha256`` for web-source fixity."""
        if content:
            identifiers = dict(identifiers or {})
            identifiers.setdefault("sha256", hashlib.sha256(
                content.encode("utf-8")).hexdigest())
        if type not in WORK_TYPES:
            raise ValueError(f"unknown work type {type!r}")
        if retrieved_by not in RESEARCH_MODES:
            raise ValueError(f"unknown mode {retrieved_by!r}")
        norm_creators = [{"name": c} if isinstance(c, str) else dict(c)
                         for c in (creators or [])]
        wid = _work_id(url, doi, title, norm_creators)
        now = _now()
        existing = self.works.get(wid)
        if existing is None:
            # The same work arriving once by URL and once by DOI must not fork
            # the provenance graph; merge into the first-seen record.
            match = self._match_identity(url=url, doi=doi)
            if match is not None:
                existing, wid = match, match["id"]
        if existing is None:
            try:
                canonical_urn = mint_canonical(
                    "", ids=_work_mint_ids(identifiers, doi), title=title)
            except ValueError:
                canonical_urn = ""      # no identifier and no title to key on
            rec = Work(id=wid, type=type, title=title.strip(),
                       creators=norm_creators, container=container,
                       publisher=publisher, date=date, url=url,
                       accessed=accessed or (now if url else ""), doi=doi,
                       identifiers=identifiers or {}, canonical_urn=canonical_urn,
                       tags=sorted(set(tags or [])), confidence=confidence,
                       language=language, retrieved_by=retrieved_by,
                       entity_refs=entity_refs or [],
                       first_seen=now, last_seen=now).to_dict()
            self.works[wid] = rec
            self._flush("works.jsonl", self.works)
            self._log("ingest", wid, {
                "kind": "grounding-work", "title": rec["title"],
                "creators": [c["name"] for c in norm_creators],
                # Joined string so erase_sweep's haystack (str values only)
                # finds creator names in grounder events.
                "creators_text": ", ".join(c["name"] for c in norm_creators),
                "url": url, "doi": doi})
            return dict(rec, status="created")
        existing["last_seen"] = now
        for fld, val in (("container", container), ("publisher", publisher),
                         ("date", date), ("doi", doi), ("url", url),
                         ("language", language)):
            if val and not existing.get(fld):
                existing[fld] = val
        if norm_creators and not existing.get("creators"):
            existing["creators"] = norm_creators
        if entity_refs:
            existing["entity_refs"] = sorted(
                set(existing.get("entity_refs", [])) | set(entity_refs))
        if tags:
            existing["tags"] = sorted(set(existing.get("tags", [])) | set(tags))
        if confidence and not existing.get("confidence"):
            existing["confidence"] = confidence
        if identifiers:
            merged = dict(existing.get("identifiers") or {})
            for k, v in identifiers.items():
                if v:
                    merged.setdefault(k, v)
            existing["identifiers"] = merged
        try:
            new_urn = mint_canonical("", ids=_work_mint_ids(
                existing.get("identifiers"), existing.get("doi", "")),
                title=existing.get("title", ""))
        except ValueError:
            new_urn = existing.get("canonical_urn", "")
        if new_urn and new_urn != existing.get("canonical_urn"):
            existing["canonical_urn"] = new_urn
        self._flush("works.jsonl", self.works)
        return dict(existing, status="updated")

    # ── claims — the invariant lives here ─────────────────────────────────────
    def _ground_claim(self, text: str, work_ids: list, *, locator: str = "",
                      quote: str = "", confidence: float = 0.0,
                      method: str = "researcher", agent: str = "") -> dict:
        """Bind one claim to the works that ground it.

        **No citation, no claim**: refuses (and audits the refusal) when no
        known work is supplied. The refusal is a first-class outcome the
        orchestrating skill must surface, not an exception to swallow."""
        if method not in RESEARCH_MODES:
            raise ValueError(f"unknown mode {method!r}")
        known = [w for w in (work_ids or []) if w in self.works]
        if not known:
            cid = _claim_id(text)
            self._log("reject", cid, {
                "kind": "grounding-refusal",
                "reason": "no citation, no claim",
                "supplied_work_ids": list(work_ids or []),
                "method": method, "agent": agent})
            return {"status": "refused", "id": cid,
                    "reason": "no citation, no claim — register the work first",
                    "supplied_work_ids": list(work_ids or [])}
        cid = _claim_id(text)
        now = _now()
        existing = self.claims.get(cid)
        if existing is None:
            rec = GroundedClaim(id=cid, text=text.strip(), work_ids=known,
                                locator=locator, quote=quote,
                                confidence=confidence, method=method,
                                agent=agent, first_seen=now,
                                last_seen=now).to_dict()
            self.claims[cid] = rec
            self._flush("claims.jsonl", self.claims)
            self._log("ingest", cid, {
                "kind": "grounding-claim", "work_ids": known,
                "method": method, "confidence": confidence, "agent": agent})
            return dict(rec, status="created")
        # repeat grounding: merge works; an independent second agent
        # (twin mode) confirming the same claim promotes it to verified.
        existing["last_seen"] = now
        existing["work_ids"] = sorted(set(existing["work_ids"]) | set(known))
        if confidence > existing.get("confidence", 0.0):
            existing["confidence"] = confidence
        if quote and not existing.get("quote"):
            existing["quote"] = quote
        if locator and not existing.get("locator"):
            existing["locator"] = locator
        if (method == "twin" and agent and agent != existing.get("agent")
                and existing.get("status") == "asserted"):
            existing["status"] = "verified"
            existing.setdefault("verified_by", []).append(agent)
            # Agreement is not evidence. Stamp whether any pass supplied a
            # quote/locator, so an evidence-less `verified` stays auditable
            # and surfaces in coverage().
            existing["evidence_at_promotion"] = bool(
                existing.get("quote") or existing.get("locator"))
            self._log("admit", cid, {"kind": "grounding-twin-confirm",
                                     "agent": agent,
                                     "evidence_at_promotion":
                                         existing["evidence_at_promotion"]})
        self._flush("claims.jsonl", self.claims)
        return dict(existing, status="updated")

    def _set_claim_status(self, claim_id: str, status: str, *,
                          by: str = "", note: str = "") -> dict:
        """Twin cross-check / human review outcome. ``disputed`` is a residual:
        the orchestrator must surface it to the human, never resolve it."""
        if status not in CLAIM_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        rec = self.claims.get(claim_id)
        if rec is None:
            return {"status": "unknown-claim", "id": claim_id}
        rec["status"] = status
        rec["last_seen"] = _now()
        if by:
            rec.setdefault("verified_by", []).append(by)
        self._flush("claims.jsonl", self.claims)
        self._log("admit" if status == "verified" else "hold", claim_id,
                  {"kind": "grounding-status", "status": status,
                   "by": by, "note": note})
        return dict(rec)

    # ── provenance — tracing ideas back through works to entities ─────────────
    def _add_provenance(self, from_work: str, relation: str, to_work: str, *,
                        evidence: str = "", basis: str = "") -> dict:
        """One typed edge: ``from_work`` --relation--> ``to_work`` (the swarm
        records each citation it follows here). Idempotent on the triple."""
        if relation not in PROVENANCE_RELATIONS:
            raise ValueError(f"unknown relation {relation!r}")
        for w in (from_work, to_work):
            if w not in self.works:
                return {"status": "unknown-work", "work": w,
                        "reason": "register the work before linking it"}
        key = f"{from_work}|{relation}|{to_work}"
        now = _now()
        existing = self.provenance.get(key)
        if existing is None:
            rec = {"key": key, "from": from_work, "relation": relation,
                   "to": to_work, "evidence": evidence, "basis": basis,
                   "first_seen": now, "last_seen": now}
            self.provenance[key] = rec
            self._flush("provenance.jsonl", self.provenance)
            self._log("ingest", key, {"kind": "grounding-provenance",
                                      "relation": relation})
            return dict(rec, status="created")
        existing["last_seen"] = now
        if evidence and not existing.get("evidence"):
            existing["evidence"] = evidence
        self._flush("provenance.jsonl", self.provenance)
        return dict(existing, status="updated")

    def trace(self, work_id: str, *, max_depth: int = 8) -> dict:
        """Walk a work's provenance upstream (what it cites, what those cite…)
        to the root works — the origin of the idea — and the entities behind
        them (creators, publishers, linked corpus entities). Cycle-safe."""
        if work_id not in self.works:
            return {"status": "unknown-work", "work": work_id}
        out_edges: dict[str, list[dict]] = {}
        for e in self.provenance.values():
            out_edges.setdefault(e["from"], []).append(e)
        visited: set[str] = set()
        chains: list[list[dict]] = []

        def walk(node: str, path: list[dict], depth: int) -> None:
            visited.add(node)
            nexts = out_edges.get(node, [])
            if not nexts or depth >= max_depth:
                if path:
                    chains.append(path)
                return
            for e in nexts:
                if e["to"] in {p["to"] for p in path} or e["to"] == work_id:
                    chains.append(path + [dict(e, cycle=True)])
                    continue
                walk(e["to"], path + [e], depth + 1)

        walk(work_id, [], 0)
        roots = sorted({c[-1]["to"] for c in chains if not c[-1].get("cycle")}) \
            if chains else []
        entities: list[dict] = []
        for rid in roots or [work_id]:
            w = self.works.get(rid, {})
            entities.append({
                "work": rid, "title": w.get("title", ""),
                "creators": [c.get("name") for c in w.get("creators", [])],
                "publisher": w.get("publisher", ""),
                "entity_refs": w.get("entity_refs", [])})
        return {"status": "ok", "work": work_id, "depth": max_depth,
                "chains": chains, "roots": roots, "root_entities": entities,
                "works_visited": sorted(visited)}

    def frontier(self) -> dict:
        """Works whose citations have not been traced yet — the swarm's next
        targets. A work is on the frontier when it has no outgoing edges."""
        traced = {e["from"] for e in self.provenance.values()}
        rows = [{"id": w["id"], "title": w["title"], "url": w.get("url", ""),
                 "retrieved_by": w.get("retrieved_by", "")}
                for w in self.works.values() if w["id"] not in traced]
        return {"frontier": rows, "count": len(rows),
                "total_works": len(self.works)}

    # ── reporting ─────────────────────────────────────────────────────────────
    def bibliography(self, *, style: str = "apa",
                     work_ids: Optional[list] = None) -> dict:
        """Formatted citations in the chosen style, alphabetical (numbered for
        IEEE/Vancouver), for the whole ledger or a subset."""
        rows = ([self.works[w] for w in work_ids if w in self.works]
                if work_ids else list(self.works.values()))
        rows.sort(key=lambda w: (_names(w, "apa") or w.get("title", "")).lower())
        entries = []
        for i, w in enumerate(rows, 1):
            c = format_citation(w, style)
            entries.append({"work_id": w["id"],
                            "citation": (f"[{i}] {c}" if style in
                                         ("ieee", "vancouver") else c)})
        return {"style": style, "count": len(entries), "entries": entries}

    def coverage(self) -> dict:
        """The honor-creators report: how complete is attribution, which claims
        are unverified or disputed, which works lack creators, dates, or links."""
        by_status: dict[str, int] = {}
        for c in self.claims.values():
            by_status[c["status"]] = by_status.get(c["status"], 0) + 1
        missing_creators = [w["id"] for w in self.works.values()
                            if not w.get("creators")]
        missing_link = [w["id"] for w in self.works.values()
                        if not (w.get("url") or w.get("doi"))]
        missing_date = [w["id"] for w in self.works.values() if not w.get("date")]
        untraced = self.frontier()["count"]
        # Citation presence is not claim support: surface the gap.
        no_evidence = [c["id"] for c in self.claims.values()
                       if not (c.get("quote") or c.get("locator"))]
        verified_no_evidence = [c["id"] for c in self.claims.values()
                                if c["status"] == "verified"
                                and not (c.get("quote") or c.get("locator"))]
        # Quote discipline: minimal passage, not page dumps.
        overlong_quotes = [c["id"] for c in self.claims.values()
                           if len(c.get("quote") or "") > 300]
        # Fixity: a live URL is not evidence of past content.
        missing_fixity = [w["id"] for w in self.works.values()
                          if w.get("url")
                          and not (w.get("identifiers", {}).get("sha256")
                                   or w.get("identifiers", {}).get("archive"))]
        n = max(len(self.works), 1)
        attribution = round(1.0 - (len(missing_creators) * 0.5
                                   + len(missing_link) * 0.3
                                   + len(missing_date) * 0.2) / n, 3)
        return {"works": len(self.works), "claims": len(self.claims),
                "claims_by_status": by_status,
                "works_missing_creators": missing_creators,
                "works_missing_link": missing_link,
                "works_missing_date": missing_date,
                "untraced_works": untraced,
                "attribution_completeness": max(attribution, 0.0),
                "claims_without_evidence": no_evidence,
                "verified_without_evidence": verified_no_evidence,
                "support_failures": [
                    c["id"] for c in self.claims.values()
                    if c.get("support_check", {}).get("verdict")
                    in ("does_not_support", "insufficient")],
                "overlong_quotes": overlong_quotes,
                "web_works_missing_fixity": missing_fixity,
                "disputed_residuals": [c["id"] for c in self.claims.values()
                                       if c["status"] == "disputed"]}

    # ── bridge: creators become entities on the workspace's map ────────────────────
    def _link_creators_to_corpus(self) -> dict:
        """Best-effort: ingest every creator into the folder's entity corpus
        (natural persons by default, organisations when role says so), so the
        provenance of ideas connects to the same map the rest of the workspace uses.
        Never raises into the caller."""
        try:
            from .legal_corpus import EntityRegistry
            reg = EntityRegistry(self.folder, log_root=self.log_root)
            linked = []
            for w in self.works.values():
                for c in w.get("creators", []):
                    name = c.get("name", "").strip()
                    if not name:
                        continue
                    kind = ("legal_person"
                            if c.get("role") in ("org", "organisation",
                                                 "organization", "publisher")
                            else "natural_person")
                    code = "creator:" + hashlib.sha256(
                        name.lower().encode("utf-8")).hexdigest()[:16]
                    reg.ingest_entity(code=code, name=name, kind=kind,
                                      url=w.get("url") or None,
                                      source="grounder")
                    if code not in w.get("entity_refs", []):
                        w.setdefault("entity_refs", []).append(code)
                        w["last_seen"] = _now()   # content changed → advance version
                    linked.append({"work": w["id"], "entity": code,
                                   "name": name, "kind": kind})
            self._flush("works.jsonl", self.works)
            return {"status": "ok", "linked": linked, "count": len(linked)}
        except Exception as exc:                                # noqa: BLE001
            return {"status": "error",
                    "error": f"{type(exc).__name__}: {exc}", "linked": []}


    # ── erasure — the attribution boundary honors the right to be forgotten ───
    def _forget_subject(self, name: str) -> dict:
        """Remove a creator (data subject) from the ledger and the entity
        corpus.

        Removes the name from every work's creator list (the work record
        stays, marked ``creator_erased`` so citations render honestly without
        the name), deletes the grounder-created ``creator:`` entity, and
        audits a ``purge`` event. Claim *text* mentioning the subject is NOT
        edited — those claims are returned for human review (options, never
        answers). Erasure of the signed log itself stays with the two-key
        ``erase_*`` workflow."""
        needle = name.strip().lower()
        if not needle:
            return {"status": "error", "error": "empty subject"}
        code = "creator:" + hashlib.sha256(
            needle.encode("utf-8")).hexdigest()[:16]
        works_touched: list[str] = []
        for w in self.works.values():
            kept = [c for c in w.get("creators", [])
                    if c.get("name", "").strip().lower() != needle]
            if len(kept) != len(w.get("creators", [])):
                w["creators"] = kept
                w["creator_erased"] = True
                w["entity_refs"] = [r for r in w.get("entity_refs", [])
                                    if r != code]
                w["last_seen"] = _now()   # content changed → advance the sink version
                works_touched.append(w["id"])
        if works_touched:
            self._flush("works.jsonl", self.works)
        entity_removed = False
        try:
            from .legal_corpus import EntityRegistry
            reg = EntityRegistry(self.folder, log_root=self.log_root)
            for key in [k for k, r in reg.entities.items()
                        if r.get("code") == code]:
                reg.entities.pop(key)
                entity_removed = True
            if entity_removed:
                reg._flush()
        except Exception:                                   # noqa: BLE001
            pass
        claims_mentioning = [c["id"] for c in self.claims.values()
                             if needle in (c.get("text", "") + " "
                                           + c.get("quote", "")).lower()]
        self._log("purge", code, {
            "kind": "grounding-subject-forgotten",
            "works_touched": works_touched,
            "entity_removed": entity_removed,
            "claims_for_review": claims_mentioning})
        return {"status": "ok", "works_touched": works_touched,
                "entity_removed": entity_removed,
                "claims_for_human_review": claims_mentioning}

    # ── creator-role classification (local-LLM integration #3) ────────────────
    def classify_creator_roles(self, *, model: str = "") -> dict:
        """Fill the missing person/organisation role on creators via the
        local-LLM route. The role drives citation formatting (org names are
        never split into Last, First). Model output is *proposed* and
        recorded (`role_source: "local-llm"`), never overwrites a role a
        human or source already set. No endpoint → returns ``unavailable``,
        ledger untouched."""
        try:
            from .local_llm import classify
        except Exception as exc:                            # noqa: BLE001
            return {"status": "unavailable",
                    "error": f"{type(exc).__name__}: {exc}"}
        classified = []
        with self._write_lock():
            # collect under the lock: _write_lock reloads state, so any
            # references gathered before it would be orphans
            pending = [(w, c) for w in self.works.values()
                       for c in w.get("creators", []) if not c.get("role")]
            if not pending:
                return {"status": "ok", "classified": [], "count": 0}
            for w, c in pending:
                res = classify(
                    "Is this creator name a person or an organisation? "
                    "NAME: " + c.get("name", ""),
                    ["person", "organisation"], model=model or None)
                if not res.get("ok"):
                    return {"status": "unavailable",
                            "error": res.get("error", ""),
                            "classified": classified}
                cat = res.get("category", "")
                if cat not in ("person", "organisation"):
                    continue                                # never guess
                c["role"] = "org" if cat == "organisation" else "author"
                c["role_source"] = "local-llm"
                classified.append({"work": w["id"], "name": c.get("name"),
                                   "role": c["role"]})
            if classified:
                self._flush("works.jsonl", self.works)
        if classified:
            self._log("classify", "creators", {
                "kind": "grounding-creator-roles",
                "count": len(classified),
                "model": model or "default"})
        return {"status": "ok", "classified": classified,
                "count": len(classified)}

    # ── claim-support gate ───────────────────────────────────────────────────
    def check_claim_support(self, claim_id: str, *, model: str = "") -> dict:
        """Semantic check: does the claim's quote support the claim text?
        Routes to the local-LLM endpoint (``WORKSPACE_LOCAL_LLM_URL``); verdicts
        are ``supports | does_not_support | insufficient``. A failing verdict
        NEVER auto-retracts — it is recorded on the claim and surfaces in
        ``coverage()`` for oversight. ``insufficient`` always escalates.
        Gated for production by the ≥32-pair gold-set (see
        EVAL_2026-06-04_grounder-local-llms.md)."""
        rec = self.claims.get(claim_id)
        if rec is None:
            return {"status": "unknown-claim", "id": claim_id}
        quote = rec.get("quote") or ""
        if not quote:
            return {"status": "no-evidence", "id": claim_id,
                    "reason": "claim has no quote to check against; "
                              "this is already surfaced in coverage()"}
        try:
            from .local_llm import classify
            res = classify(
                "CLAIM: " + rec["text"] + "\nQUOTE: " + quote
                + "\nDoes the quote support the claim?",
                ["supports", "does_not_support", "insufficient"],
                model=model or None)
        except Exception as exc:                            # noqa: BLE001
            return {"status": "unavailable",
                    "error": f"{type(exc).__name__}: {exc}"}
        if not res.get("ok"):
            return {"status": "unavailable", "error": res.get("error", "")}
        verdict = res.get("category", "insufficient")
        with self._write_lock():
            rec = self.claims.get(claim_id)
            rec["support_check"] = {"verdict": verdict,
                                    "model": res.get("model_used", ""),
                                    "ts": _now()}
            self._flush("claims.jsonl", self.claims)
        self._log("classify", claim_id, {
            "kind": "grounding-support-check", "verdict": verdict,
            "model": res.get("model_used", "")})
        return {"status": "ok", "id": claim_id, "verdict": verdict,
                "escalate": verdict != "supports"}


# ── one-shot convenience for the common path ──────────────────────────────────

def ground(folder: str, claim: str, works: list, *, style: str = "apa",
           method: str = "researcher", agent: str = "",
           confidence: float = 0.0, locator: str = "",
           log_root: Optional[str] = None) -> dict:
    """Register the supplied works, ground the claim against them, and return
    the formatted citations — the single call most agents need after a
    web-grounded answer. ``works`` is a list of work dicts (``title`` required;
    everything else as known)."""
    ledger = GroundingLedger(folder, log_root=log_root)
    work_ids: list[str] = []
    registered: list[dict] = []
    for w in works or []:
        rec = ledger.register_work(**{k: v for k, v in w.items()
                                      if (k in Work.__dataclass_fields__
                                          or k == "content")
                                      and k not in ("id", "first_seen",
                                                    "last_seen")})
        work_ids.append(rec["id"])
        registered.append({"id": rec["id"], "status": rec["status"]})
    res = ledger.ground_claim(claim, work_ids, method=method, agent=agent,
                              confidence=confidence, locator=locator)
    citations = [format_citation(ledger.works[w], style)
                 for w in res.get("work_ids", []) if w in ledger.works]
    # Route the output through the normal oversight modes: grounded = at least
    # one cited source. Ungrounded → flagged; the workspace's oversight level decides
    # whether the agent stops (HITL) or keeps running flagged (HOTL/HIC).
    from .governance import decide_output
    oversight = decide_output(
        folder, grounded=bool(res.get("work_ids")),
        action_class=f"ground:{res.get('id', '')}",
        actor=agent or "system", log_root=log_root,
        detail=(claim[:120] if isinstance(claim, str) else ""))
    return {"claim": res, "works": registered, "style": style,
            "citations": citations, "oversight": oversight}
