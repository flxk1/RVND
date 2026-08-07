# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""WorkspaceMemory — folder-scoped memory interface (Phase 2 / A2).

Sits on top of the per-folder ``MutationLog`` (A1) and enforces the
asymmetric hierarchical rule (children flow up; siblings out of scope):

- Memory flows UP: a folder's view includes its own content plus the union
  of every descendant folder's content.
- Memory does NOT flow DOWN: a folder cannot read its parent's content or
  any sibling folder's content.

The asymmetry is structural — implemented at this layer, not enforced by
policy. A junior engineer in ``/companies/acme/Engineering/platform/``
cannot accidentally read ``/companies/acme/HR/compensation/`` because the
discovery code never even looks at HR's log directory when the context is
Engineering's.

This module is content-shape-agnostic. Callers pass dict-shaped pairs
(typically produced by serialising an ND's ``ProblemSolutionPair`` via
``dataclasses.asdict``). The pair dict must contain at least:

.. code-block:: python

    {
      "id": "sha256:...",
      "problem": {"id": ..., "scope": ..., "summary": ..., "facets": ...},
      "solution": {"id": ..., "body": ..., "confidence": ..., ...},
    }

Other fields are passed through unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .folder_context import resolve_folder_context
from .mutation_log import (
    LOG_ROOT_DEFAULT,
    LogEvent,
    MutationLog,
    folder_hash,
)

_LOG = logging.getLogger(__name__)

# Channels that carry shared KNOWLEDGE (the plane owned by versum), as opposed to
# RVND-local capture-evidence (llm_answer / websearch) and audit/system events.
# Knowledge-channel pairs are mirrored into the folder's versum sink so the read
# path can be served from versum (the memory→versum split). Capture-evidence and
# system/audit stay in the local MutationLog.
_KNOWLEDGE_CHANNELS = frozenset({"document", "fact", "reasoning"})


# ===========================================================================
# Folder discovery
# ===========================================================================


def discover_folders(log_root: str | Path | None = None) -> dict[str, str]:
    """Return ``{folder_path -> folder_hash}`` for every folder with a log.

    Walks the log root directory, peeks the first event of each log file to
    recover the folder_path, and returns the mapping. Logs with empty or
    malformed first lines are skipped.
    """
    root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
    if not root.exists():
        return {}

    out: dict[str, str] = {}
    for subdir in sorted(root.iterdir()):
        if not subdir.is_dir():
            continue
        log_file = subdir / "events.jsonl"
        if not log_file.exists():
            continue
        try:
            with log_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    fp = obj.get("folder_path")
                    if fp:
                        out[str(fp)] = subdir.name
                        break  # first valid event tells us the folder
        except OSError:
            continue
    return out


def discover_descendants(
    folder_context: str | Path,
    *,
    log_root: str | Path | None = None,
) -> list[str]:
    """Return the list of folder paths that are ``folder_context`` or descendants.

    The asymmetric hierarchical rule, expressed as a path-prefix filter:
    given ``/companies/acme/HR/``, returns every known folder whose path
    equals it or starts with it + ``/``. Sibling folders
    (``/companies/acme/Engineering/``) are excluded by construction.
    """
    ctx = str(Path(folder_context).expanduser().resolve())
    ctx_prefix = ctx if ctx.endswith("/") else ctx + "/"
    all_folders = discover_folders(log_root=log_root)
    out: list[str] = []
    for fp in all_folders:
        if fp == ctx or fp.startswith(ctx_prefix):
            out.append(fp)
    return sorted(out)


def discover_ancestors(
    folder_context: str | Path,
    *,
    log_root: str | Path | None = None,
) -> list[str]:
    """Return the list of folder paths that are STRICT ancestors of ``folder_context``.

    The complement to :func:`discover_descendants`: walks up the path hierarchy
    and returns every known folder (one that has a log) whose path is a strict
    prefix of ``folder_context``. The folder_context itself is excluded.

    Used by B5 (distributed memory) to find ancestor folders whose
    ``publish()``-marked pairs flow DOWN to this folder.
    """
    ctx = str(Path(folder_context).expanduser().resolve())
    all_folders = discover_folders(log_root=log_root)
    out: list[str] = []
    for fp in all_folders:
        if fp == ctx:
            continue
        # fp is an ancestor of ctx iff ctx starts with fp + "/".
        if ctx.startswith(fp + "/"):
            out.append(fp)
    # Sort by depth (shallowest first — closer to root).
    out.sort(key=lambda p: p.count("/"))
    return out


# ===========================================================================
# Pair extraction from log events
# ===========================================================================


def _pair_from_event(evt: LogEvent) -> dict[str, Any] | None:
    """Extract the embedded pair dict from a remember-event.

    On ``remember()``, WorkspaceMemory stores the full pair body in ``LogEvent.extra``
    under the key ``"pair"``. State-transition events (admit/delete/etc.)
    reference only the ``pair_id`` and don't embed the body. This helper
    returns None for state-transition events.
    """
    pair_data = evt.extra.get("pair") if isinstance(evt.extra, dict) else None
    if not isinstance(pair_data, dict):
        return None
    return pair_data


# ===========================================================================
# Similarity (phase 1: keyword overlap)
# ===========================================================================


_TOKEN_RE = re.compile(r"\b\w+\b", re.UNICODE)


def _tokenise(text: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(text)}


def _keyword_similarity(query: dict[str, Any], pair: dict[str, Any]) -> float:
    """Jaccard similarity over the union of (summary, facet values).

    Phase 1 implementation. Phase 2 will replace with embedding-based similarity
    (Phi-3.5 / BGE-small) when the local-model runtime is wired in.
    """
    query_summary = str(query.get("summary", ""))
    query_facets_raw = query.get("facets", {})
    pair_problem = pair.get("problem", {}) if isinstance(pair, dict) else {}
    pair_summary = str(pair_problem.get("summary", ""))
    pair_facets_raw = pair_problem.get("facets", {})

    query_tokens = _tokenise(query_summary)
    if isinstance(query_facets_raw, dict):
        for v in query_facets_raw.values():
            if isinstance(v, str):
                query_tokens |= _tokenise(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        query_tokens |= _tokenise(item)

    pair_tokens = _tokenise(pair_summary)
    if isinstance(pair_facets_raw, dict):
        for v in pair_facets_raw.values():
            if isinstance(v, str):
                pair_tokens |= _tokenise(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        pair_tokens |= _tokenise(item)

    if not query_tokens or not pair_tokens:
        return 0.0
    inter = len(query_tokens & pair_tokens)
    union = len(query_tokens | pair_tokens)
    return inter / union if union else 0.0


# ===========================================================================
# WorkspaceMemory
# ===========================================================================


@dataclass
class WebResult:
    """One result from a web search — the unit ``web_capture`` ingests."""

    url: str
    title: str = ""
    snippet: str = ""
    full_text: str = ""


class WorkspaceMemory:
    """Folder-scoped memory view.

    Construct one per ``folder_context``. Reads aggregate from that folder
    plus every descendant; writes go to that folder's own log only. The
    asymmetric rule is enforced by ``discover_descendants`` — siblings and
    ancestors are never opened.

    Phase 2 (this file): in-memory aggregation + keyword similarity. Phase 3
    will add an embedding similarity backend.
    """

    def __init__(
        self,
        folder_context: str | Path | None = None,
        *,
        log_root: str | Path | None = None,
        actor: str = "user",
        allow_unscoped: bool = False,
    ):
        """Construct a folder-scoped memory view.

        If ``folder_context`` is None, the constructor falls back to the value
        from :func:`workspaces.folder_context.current_folder` (the
        contextvar set by :class:`~workspaces.folder_context.folder_context`
        / :func:`~workspaces.folder_context.set_folder`, or the
        ``WORKSPACE_FOLDER_CONTEXT`` environment variable). If none can be
        determined and ``allow_unscoped`` is False, raises
        :class:`~workspaces.folder_context.NoFolderContextError`.
        """
        # Scope the A6 allowlist to this view's log root (matches _own_log below),
        # so enforcement reads the registry the operation actually writes to.
        self.folder_context = resolve_folder_context(
            folder_context, allow_unscoped=allow_unscoped, log_root=log_root
        )
        self._log_root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
        self._actor = actor
        # The own-folder log — where writes go.
        self._own_log = MutationLog(self.folder_context, log_root=self._log_root)
        # Cache of MutationLog instances by folder_path for read aggregation.
        self._log_cache: dict[str, MutationLog] = {self.folder_context: self._own_log}

    # ----------------------------------------------------------------------
    # Internal — log access (asymmetric rule lives here)
    # ----------------------------------------------------------------------

    def _logs_in_scope(self) -> list[MutationLog]:
        """Return MutationLog instances for folder_context + every descendant.

        Discovery is path-prefix; the asymmetric rule (sub-folders flow up;
        ancestors do NOT) is enforced by construction — siblings and
        ancestors never appear in the result.
        """
        descendants = discover_descendants(
            self.folder_context, log_root=self._log_root
        )
        # Always include folder_context itself even if it has no log yet.
        if self.folder_context not in descendants:
            descendants.append(self.folder_context)
        for fp in descendants:
            if fp not in self._log_cache:
                self._log_cache[fp] = MutationLog(fp, log_root=self._log_root)
        return [self._log_cache[fp] for fp in descendants]

    def _ancestor_logs(self) -> list[MutationLog]:
        """Return MutationLog instances for every STRICT ancestor of folder_context.

        Used by the B5 distributed-memory read path. Ancestors carry pairs
        that have been explicitly :meth:`publish`-ed downward; private pairs
        in ancestor logs are NOT readable from here.
        """
        ancestors = discover_ancestors(
            self.folder_context, log_root=self._log_root
        )
        for fp in ancestors:
            if fp not in self._log_cache:
                self._log_cache[fp] = MutationLog(fp, log_root=self._log_root)
        return [self._log_cache[fp] for fp in ancestors]

    def _find_owning_log(self, pair_id: str) -> MutationLog | None:
        """Locate the log that originally remembered this pair_id.

        Walks logs in scope from most-specific to most-general; returns the
        first one that has a ``remember`` event for this pair_id.
        """
        for log in self._logs_in_scope():
            for evt in log.replay():
                # the ingest/live event identifies the owning log by pair_id — post
                # body-drop a knowledge ingest event no longer carries the body in
                # ``extra['pair']``, so match on the event kind, not the body.
                if evt.pair_id == pair_id and evt.event in ("ingest", "live"):
                    return log
        return None

    # ----------------------------------------------------------------------
    # Write API
    # ----------------------------------------------------------------------

    def remember(
        self,
        pair: dict[str, Any],
        *,
        channel: str = "document",
        lifecycle_state: str = "live",
        source_hash: str = "",
    ) -> str:
        """Write a pair to this folder's log. Returns ``pair_id``.

        The pair dict must contain at minimum ``id``, ``problem``, and
        ``solution`` keys. The full pair is embedded in the LogEvent's
        ``extra`` field so it can be reconstructed from the log alone.

        Idempotent only at the read-aggregation layer: re-calling
        ``remember()`` with the same pair appends another event, but
        ``by_id()`` returns the latest stored body.
        """
        pair_id = str(pair.get("id") or "")
        if not pair_id:
            raise ValueError("pair must have an 'id' key")
        problem_id = str(pair.get("problem", {}).get("id", "")) if isinstance(pair.get("problem"), dict) else ""

        # memory→versum body-drop: a knowledge-channel pair's BODY lives in the
        # folder's versum sink (the canonical, authoritative knowledge plane), NOT
        # in the log event. Write versum FIRST so a sink failure fails the remember
        # rather than leaving a body-less log event with no body anywhere; then log
        # a body-less event (it still owns pair_id + lifecycle + scope). A
        # non-knowledge channel (capture / system) keeps its body in the log — it
        # has no versum copy, so it is not redundant.
        if channel in _KNOWLEDGE_CHANNELS:
            self._write_knowledge_to_versum(pair)
            extra: dict[str, Any] = {"distribution_scope": "private"}
        else:
            extra = {"pair": pair, "distribution_scope": "private"}

        evt = LogEvent(
            event="ingest",
            folder_path=self.folder_context,  # overwritten by log on append
            pair_id=pair_id,
            lifecycle_state=lifecycle_state,
            channel=channel,
            problem_id=problem_id,
            source_hash=source_hash,
            actor=self._actor,
            extra=extra,
        )
        self._own_log.append(evt)
        return pair_id

    def _write_knowledge_to_versum(self, pair: dict[str, Any]) -> None:
        """Write a knowledge-channel pair's body into this folder's versum sink as
        an identity-upsert record (a re-``remember`` of the same id supersedes in
        place, latest-wins on read), via the adapters.versum seam. Authoritative,
        NOT best-effort: after the body-drop the body lives ONLY here, so a sink
        failure must raise (and fail the remember) rather than silently lose it.
        """
        # A sealed workspace is read-only: refuse the write BEFORE touching disk,
        # the same refusal the mutation log enforces — else the versum-first write
        # would both bypass the seal and leak plaintext knowledge into .versum.
        from . import seal
        from .mutation_log import SealedWriteError
        if seal.is_sealed(self.folder_context, log_root=self._log_root):
            raise SealedWriteError(
                "workspace is sealed — unseal before writing knowledge")
        from .adapters.versum import append_record
        store = Path(self.folder_context) / ".versum"
        store.mkdir(parents=True, exist_ok=True)
        # a strictly-monotonic per-process version so a later remember of the same
        # pair id wins on read (str of ns epoch: stable digit-count → lexicographic
        # order == chronological).
        append_record(store, record=pair, dimension="relational",
                      actor=self._actor, identity=True, version=str(time.time_ns()))

    def _versum_knowledge_pairs(self) -> dict[str, dict[str, Any]]:
        """Knowledge pairs from this folder + descendants' versum sinks, keyed by
        the pair's own id.

        The read side of the memory→versum split: knowledge bodies live in versum
        (``remember`` mirrors them via ``append_record``, storing the full pair in
        the node's ``properties.record``), and versum already hides erased
        knowledge. Hierarchy mirrors :meth:`_logs_in_scope` — folder_context plus
        every descendant, never siblings or ancestors. Best-effort: a versum read
        failure is logged and skipped so a read never breaks.
        """
        out: dict[str, dict[str, Any]] = {}
        try:
            from .adapters.versum import iter_records
        except Exception:  # versum surface absent → nothing to fold in
            return out
        folders = discover_descendants(self.folder_context, log_root=self._log_root)
        if self.folder_context not in folders:
            folders.append(self.folder_context)
        for fp in folders:
            store = Path(fp) / ".versum"
            try:
                for rec in iter_records(store):
                    props = rec.get("properties") if isinstance(rec, dict) else None
                    body = props.get("record") if isinstance(props, dict) else None
                    if isinstance(body, dict) and body.get("id"):
                        out.setdefault(str(body["id"]), body)
            except Exception as exc:
                _LOG.warning("versum knowledge read skipped for %s: %s", fp, exc)
        return out

    def _versum_bodies_for(self, folder_path: str) -> dict[str, dict[str, Any]]:
        """Knowledge pair bodies in ONE folder's versum sink, keyed by pair id
        (on-disk read). Used where the log no longer carries the body post
        body-drop (e.g. cascade delete-by-document). Best-effort."""
        out: dict[str, dict[str, Any]] = {}
        try:
            from .adapters.versum import read_disk_versum_records
            for rec in read_disk_versum_records(folder_path):
                body = rec.get("properties", {}).get("record") if isinstance(rec, dict) else None
                if isinstance(body, dict) and body.get("id"):
                    out.setdefault(str(body["id"]), body)
        except Exception:  # best-effort — the log still owns lifecycle
            pass
        return out

    def _erase_knowledge_from_versum(self, pair_ids: set[str], *,
                                     physical: bool, reason: str = "") -> None:
        """Keep the versum sink consistent with a log delete/purge of knowledge.

        A logical delete is already honored by reads via the log's lifecycle state,
        but a PURGE removes the log event outright — so without erasing the versum
        mirror too, the read union would resurface a purged pair. Enumerates folder
        + descendants' sinks and erases every node whose stored pair id is in
        ``pair_ids`` (physical purge when ``physical``, else a tombstone). Guarded:
        a versum failure is logged, never fatal to the log operation.
        """
        if not pair_ids:
            return
        try:
            from .adapters.versum import iter_records, erase_record
        except Exception:
            return
        folders = discover_descendants(self.folder_context, log_root=self._log_root)
        if self.folder_context not in folders:
            folders.append(self.folder_context)
        for fp in folders:
            store = Path(fp) / ".versum"
            if not store.is_dir():
                continue
            try:
                for rec in iter_records(store):
                    props = rec.get("properties") if isinstance(rec, dict) else None
                    body = props.get("record") if isinstance(props, dict) else None
                    nid = rec.get("node_id") if isinstance(rec, dict) else None
                    if nid and isinstance(body, dict) and str(body.get("id")) in pair_ids:
                        erase_record(store, nid, physical=physical,
                                     actor=self._actor, reason=reason)
            except Exception as exc:
                _LOG.warning("versum erasure sync skipped for %s: %s", fp, exc)

    def publish(
        self,
        pair: dict[str, Any],
        *,
        scope: str = "descendants",
        channel: str = "document",
        source_hash: str = "",
    ) -> str:
        """Write a pair to this folder's log + mark it DISTRIBUTED to descendants.

        Where :meth:`remember` writes a PRIVATE pair (subject to the asymmetric
        rule — flows UP only), :meth:`publish` writes a DISTRIBUTED pair that
        flows DOWN to every descendant folder (B5).

        The pair body lives only in THIS folder's log. Descendants read it via
        the ancestor-distributed read path; nothing is duplicated.

        Args:
            pair: the pair dict (same shape as remember()).
            scope: ``"descendants"`` (default) — propagates to every descendant.
                Other values reserved for future use.
            channel: the same channel marker as remember() (document / etc.).
            source_hash: optional source-hash for cascade-delete by source.

        Returns the pair_id.

        To revoke a published pair, call :meth:`unpublish` or :meth:`delete`.
        """
        if scope != "descendants":
            raise ValueError(
                f"publish scope must be 'descendants' (got {scope!r}). "
                f"Other scopes reserved for future use."
            )
        pair_id = str(pair.get("id") or "")
        if not pair_id:
            raise ValueError("pair must have an 'id' key")
        problem_id = (
            str(pair.get("problem", {}).get("id", ""))
            if isinstance(pair.get("problem"), dict) else ""
        )

        evt = LogEvent(
            event="ingest",
            folder_path=self.folder_context,
            pair_id=pair_id,
            lifecycle_state="live",
            channel=channel,
            problem_id=problem_id,
            source_hash=source_hash,
            actor=self._actor,
            extra={"pair": pair, "distribution_scope": scope},
        )
        self._own_log.append(evt)
        return pair_id

    def unpublish(self, pair_id: str) -> bool:
        """Revoke a previously-published pair from descendants.

        Equivalent to :meth:`delete` — writes a logical-delete event. The pair
        is hidden from descendants on next read; the audit chain survives.
        Returns True if a published pair was found, else False.
        """
        # Walk only the own log + descendants to find the published pair.
        # Don't look at ancestor logs — we can't unpublish what we didn't publish.
        for log in self._logs_in_scope():
            for evt in log.replay():
                if evt.pair_id != pair_id:
                    continue
                pair_data = _pair_from_event(evt)
                if pair_data is None:
                    continue
                if evt.extra.get("distribution_scope") == "descendants":
                    log.append(LogEvent(
                        event="delete",
                        folder_path=log.folder_path,
                        pair_id=pair_id,
                        lifecycle_state="deleted",
                        channel="system",
                        actor=self._actor,
                        extra={"reason": "unpublish",
                               "distribution_scope": "private"},
                    ))
                    return True
        return False

    def web_capture(
        self,
        query: str,
        results: list[WebResult] | list[dict[str, Any]],
        *,
        engine: str = "web",
    ) -> list[str]:
        """Ingest websearch results into this folder. Returns pair_ids.

        Each result becomes one pair: the query is the problem-summary, the
        result snippet is the solution-body, the URL is the cited source.
        """
        # Redact secrets/PII before persistence (same invariant as
        # llm_capture / *_capture._project_pair). Identity ids/hashes are over
        # the RAW query/url (one-way, non-leaking); only stored CONTENT is redacted.
        from .lock import redact_for_capture
        captured: list[str] = []
        retrieved_at = time.time()
        for r in results:
            if isinstance(r, WebResult):
                url, title, snippet, full = r.url, r.title, r.snippet, r.full_text
            else:
                url = str(r.get("url", ""))
                title = str(r.get("title", ""))
                snippet = str(r.get("snippet", ""))
                full = str(r.get("full_text", ""))

            pid = _stable_id(self.folder_context, "web", query, url)
            sid = _stable_id(pid, snippet or title or url)
            q_red = redact_for_capture(query)
            url_red = redact_for_capture(url)
            body_red = redact_for_capture(snippet or full)

            pair: dict[str, Any] = {
                "id": sid,
                "problem": {
                    "id": pid,
                    "scope": "web",
                    "type": "websearch",
                    "summary": q_red,
                    "facets": {"engine": engine, "url": url_red,
                               "title": redact_for_capture(title),
                               "retrieved_at": retrieved_at},
                },
                "solution": {
                    "id": sid,
                    "problem_id": pid,
                    "body": body_red,
                    "body_format": "prose",
                    "authority_tier": 5,    # web/unverified
                    "confidence": 0.5,
                    "cited_sources": [url_red] if url else [],
                    "extractor_chain": ["web_capture"],
                    "extractor_version": "0.1.0",
                    "created_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(retrieved_at)
                    ),
                },
            }
            captured.append(
                self.remember(pair, channel="websearch", source_hash=_short_hash(url))
            )
        return captured

    def llm_capture(
        self,
        prompt_context: str,
        response: str,
        *,
        model: str,
        cited_sources: list[str] | None = None,
    ) -> str:
        """Ingest an LLM exchange into this folder. Returns pair_id.

        The prompt context becomes the problem-summary; the model's response
        is the solution-body; the model id + prompt-context-hash form the
        provenance trail.
        """
        # Redact secrets/PII before persistence (same invariant as
        # llm_capture._project_pair). Identity hashes/ids are over the RAW
        # prompt (one-way, non-leaking); only stored CONTENT is redacted.
        from .lock import redact_for_capture
        prompt_hash = _short_hash(prompt_context)
        pid = _stable_id(self.folder_context, "llm", model, prompt_hash)
        sid = _stable_id(pid, response)
        p_red = redact_for_capture(prompt_context)
        resp_red = redact_for_capture(response)
        pair: dict[str, Any] = {
            "id": sid,
            "problem": {
                "id": pid,
                "scope": "llm",
                "type": "llm_query",
                "summary": p_red[:200],
                "facets": {
                    "model": model,
                    "prompt_context_hash": prompt_hash,
                    "prompt_context_length": len(p_red),
                },
            },
            "solution": {
                "id": sid,
                "problem_id": pid,
                "body": resp_red,
                "body_format": "prose",
                "authority_tier": 5,
                "confidence": 0.6,
                "cited_sources": [redact_for_capture(s) for s in (cited_sources or [])],
                "extractor_chain": [f"llm_capture:{model}"],
                "extractor_version": "0.1.0",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
        return self.remember(pair, channel="llm_answer", source_hash=prompt_hash)

    def delete(self, pair_id: str) -> bool:
        """Write a logical-delete event. Returns True if pair was found, else False.

        Logical delete: the pair is hidden from reads but the audit chain
        (and the original ``ingest`` event) survives. Use ``purge_pair()``
        for physical erasure.
        """
        log = self._find_owning_log(pair_id)
        if log is None:
            return False
        log.append(LogEvent(
            event="delete",
            folder_path=log.folder_path,
            pair_id=pair_id,
            lifecycle_state="deleted",
            channel="system",
            actor=self._actor,
            extra={"reason": "user_delete"},
        ))
        return True

    def delete_document(self, document_path: str) -> int:
        """Cascade-delete every pair derived from this document. Returns count.

        Identifies pairs by ``Problem.source_document``. Issues one delete
        event per pair, all sharing a ``cascade_root_id`` for audit-trail
        reconstruction.
        """
        target = str(document_path)
        cascade_id = str(uuid.uuid4())
        deleted = 0
        for log in self._logs_in_scope():
            seen: set[str] = set()
            # post body-drop a knowledge pair's body (with its source_document)
            # lives in the folder's versum sink, not the log event — resolve it so
            # the cascade still matches by source document.
            versum_bodies = self._versum_bodies_for(log.folder_path)
            for evt in log.replay():
                pair = _pair_from_event(evt) or versum_bodies.get(evt.pair_id)
                if pair is None:
                    continue
                problem = pair.get("problem") if isinstance(pair, dict) else None
                src = None
                if isinstance(problem, dict):
                    src = problem.get("source_document")
                if src != target:
                    continue
                if evt.pair_id in seen:
                    continue
                seen.add(evt.pair_id)
                log.append(LogEvent(
                    event="delete",
                    folder_path=log.folder_path,
                    pair_id=evt.pair_id,
                    lifecycle_state="deleted",
                    channel="system",
                    actor=self._actor,
                    extra={"reason": "cascade_document_delete",
                           "document_path": target,
                           "cascade_root_id": cascade_id},
                ))
                deleted += 1
        return deleted

    def purge_pair(
        self,
        pair_id: str,
        *,
        legal_basis: str = "",
        requester_ref: str = "",
        reason: str = "",
    ) -> int:
        """PHYSICAL erasure of every event referencing this pair_id. IRREVERSIBLE.

        For GDPR Art. 17 erasure-from-everything. The default user-facing
        ``delete`` is logical. Returns count of events purged across the
        owning log only (other logs in scope are not rewritten — they
        reference the pair_id but don't store its body).

        0.6.8 (B1): ``legal_basis``, ``requester_ref`` and ``reason`` are
        forwarded to ``MutationLog.purge`` and recorded in the tombstone.
        See ``MutationLog.purge`` for validation rules.
        """
        log = self._find_owning_log(pair_id)
        purged = 0 if log is None else log.purge(
            pair_id,
            legal_basis=legal_basis,
            requester_ref=requester_ref,
            reason=reason,
        )
        # Keep the versum sink consistent: physically erase the knowledge mirror,
        # else the read union would resurface a purged pair (its log event is gone).
        self._erase_knowledge_from_versum(
            {pair_id}, physical=True, reason=reason or "purge_pair")
        return purged

    def purge_document(
        self,
        document_path: str,
        *,
        legal_basis: str = "",
        requester_ref: str = "",
        reason: str = "",
    ) -> int:
        """PHYSICAL erasure of every pair derived from a document. IRREVERSIBLE.

        Walks every log in scope, finds pair_ids whose stored Problem has
        ``source_document == document_path``, and calls ``log.purge(pair_id)``
        on each. Returns the total count of events purged across all logs.

        Unlike :meth:`delete_document` (logical), this rewrites the log
        files. Audit trail of the original ingest is gone. Use only for
        true Art.17 erasure requests, not routine forgetting.

        0.6.8 (B1): ``legal_basis``, ``requester_ref`` and ``reason`` are
        recorded in each tombstone. Same legal grounds apply across the
        whole document.
        """
        target = str(document_path)
        total_purged = 0
        purged_ids: set[str] = set()
        for log in self._logs_in_scope():
            pair_ids_to_purge: set[str] = set()
            # post body-drop a knowledge pair's body (with source_document) lives in
            # the folder's versum sink, not the log event — resolve it so purge
            # still matches by source document.
            versum_bodies = self._versum_bodies_for(log.folder_path)
            for evt in log.replay():
                pair = _pair_from_event(evt) or versum_bodies.get(evt.pair_id)
                if pair is None:
                    continue
                problem = pair.get("problem") if isinstance(pair, dict) else None
                if isinstance(problem, dict) and problem.get("source_document") == target:
                    pair_ids_to_purge.add(evt.pair_id)
            for pid in pair_ids_to_purge:
                total_purged += log.purge(
                    pid,
                    legal_basis=legal_basis,
                    requester_ref=requester_ref,
                    reason=reason,
                )
            purged_ids |= pair_ids_to_purge
        # Keep the versum sink consistent: physically erase the knowledge mirrors of
        # every purged pair, else the read union would resurface them.
        self._erase_knowledge_from_versum(
            purged_ids, physical=True, reason=reason or "purge_document")
        return total_purged

    # ----------------------------------------------------------------------
    # Read API
    # ----------------------------------------------------------------------

    def _ancestor_distributed_aggregation(
        self,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, float]]:
        """Aggregate ancestor-distributed pairs visible to this folder.

        For each ancestor log, walk its events and track per-pair:

        - The latest body (if a pair was ever recorded for that pair_id).
        - The latest ``distribution_scope`` value.
        - The latest ``lifecycle_state``.

        A pair is visible to descendants iff its LATEST distribution_scope
        is ``"descendants"`` AND its latest lifecycle_state is not in
        ``{deleted, purged, rejected}``. This honours both:

        - The publisher's choice (publish → distribute → re-mark-as-private
          flips visibility off).
        - The publisher's revocation (publish → delete drops the pair).

        Returns (bodies, latest_state, latest_ts) restricted to visible pairs.
        """
        ancestor_bodies: dict[str, dict[str, Any]] = {}
        ancestor_scope: dict[str, str] = {}
        ancestor_state: dict[str, str] = {}
        ancestor_ts: dict[str, float] = {}

        for log in self._ancestor_logs():
            for evt in log.replay():
                pid = evt.pair_id
                pair = _pair_from_event(evt)
                if pair is not None:
                    ancestor_bodies[pid] = pair
                scope_marker = evt.extra.get("distribution_scope") if isinstance(evt.extra, dict) else None
                if scope_marker:
                    ancestor_scope[pid] = scope_marker
                if evt.lifecycle_state:
                    ancestor_state[pid] = evt.lifecycle_state
                ancestor_ts[pid] = evt.ts

        # Filter to pairs visible to descendants.
        visible_bodies: dict[str, dict[str, Any]] = {}
        visible_state: dict[str, str] = {}
        visible_ts: dict[str, float] = {}
        for pid, body in ancestor_bodies.items():
            if ancestor_scope.get(pid) != "descendants":
                continue
            if ancestor_state.get(pid) in ("deleted", "purged", "rejected"):
                continue
            visible_bodies[pid] = body
            visible_state[pid] = ancestor_state.get(pid, "")
            visible_ts[pid] = ancestor_ts.get(pid, 0.0)
        return visible_bodies, visible_state, visible_ts

    def by_id(self, pair_id: str) -> dict[str, Any] | None:
        """Direct lookup by pair_id. Returns None if not found or deleted.

        Walks logs in scope (own + descendants); reconstructs the pair body
        from its remember event; honours most-recent-state-wins (deleted pairs
        return None). Also includes ancestor-DISTRIBUTED pairs per B5.
        """
        latest_state: str | None = None
        latest_body: dict[str, Any] | None = None
        for log in self._logs_in_scope():
            for evt in log.replay():
                if evt.pair_id != pair_id:
                    continue
                pair = _pair_from_event(evt)
                if pair is not None:
                    latest_body = pair
                if evt.lifecycle_state:
                    latest_state = evt.lifecycle_state
        # memory→versum body-drop: a knowledge pair's body lives in versum, not the
        # log event — so fold in the versum body BEFORE applying lifecycle, so the
        # log's delete/purge/reject state governs a versum-sourced body too (else a
        # deleted knowledge pair whose body is only in versum would leak).
        if latest_body is None:
            latest_body = self._versum_knowledge_pairs().get(pair_id)
        if latest_body is not None:
            if latest_state in ("deleted", "purged", "rejected"):
                return None
            return latest_body

        # Not in own scope — check ancestor-distributed pairs (B5).
        anc_bodies, _, _ = self._ancestor_distributed_aggregation()
        return anc_bodies.get(pair_id)

    def search(
        self,
        query: dict[str, Any] | str,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """Find similar past pairs across folder_context + descendants.

        ``query`` may be a full Problem dict (with summary + facets) or a
        plain string (treated as the summary). Phase-1 implementation uses
        Jaccard similarity over tokens of (summary + facet values). Phase-2
        will swap in embedding similarity.

        Honours the asymmetric rule: only pairs from folder_context or its
        descendants are considered. Sibling and ancestor folders are out
        of scope by construction.

        Excludes pairs whose latest state is ``deleted``/``purged``/``rejected``.
        Ties broken by recency (most-recent first).
        """
        if isinstance(query, str):
            query_dict: dict[str, Any] = {"summary": query, "facets": {}}
        else:
            query_dict = dict(query)

        # Aggregate every remember-event across in-scope logs, then apply
        # most-recent-state-wins to filter out deleted/rejected pairs.
        bodies: dict[str, dict[str, Any]] = {}
        latest_state: dict[str, str] = {}
        latest_ts: dict[str, float] = {}
        for log in self._logs_in_scope():
            for evt in log.replay():
                pair = _pair_from_event(evt)
                if pair is not None:
                    bodies[evt.pair_id] = pair
                if evt.lifecycle_state:
                    latest_state[evt.pair_id] = evt.lifecycle_state
                latest_ts[evt.pair_id] = evt.ts

        # memory→versum read: fold in knowledge bodies from the versum sink. In
        # the dual-write phase these mirror the log (dedup → no change); once
        # knowledge is sink-only they are the sole source. The log still owns
        # lifecycle STATE, so a pair the log marks deleted is filtered below even
        # if versum still holds a body (versum-side erasure lands in a later
        # stage); versum-only pairs have no local state and read as live.
        for pid, body in self._versum_knowledge_pairs().items():
            if pid not in bodies:
                bodies[pid] = body
                latest_ts.setdefault(pid, 0.0)

        live_ids = [
            pid for pid in bodies
            if latest_state.get(pid) not in ("deleted", "purged", "rejected")
        ]

        # B5: fold in ancestor-distributed pairs (own log entry wins on conflict).
        anc_bodies, anc_state, anc_ts = self._ancestor_distributed_aggregation()
        for pid, body in anc_bodies.items():
            if pid not in bodies:
                bodies[pid] = body
                latest_state[pid] = anc_state.get(pid, "")
                latest_ts[pid] = anc_ts.get(pid, 0.0)
                if pid not in live_ids:
                    live_ids.append(pid)

        scored: list[tuple[float, float, dict[str, Any]]] = []
        for pid in live_ids:
            pair = bodies[pid]
            sim = _keyword_similarity(query_dict, pair)
            if sim > 0:
                scored.append((sim, latest_ts.get(pid, 0.0), pair))

        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [pair for _, _, pair in scored[:k]]

    def all_pairs(self) -> list[dict[str, Any]]:
        """Return every live pair in scope. Diagnostic / admin use.

        Includes own + descendants (per the asymmetric rule) AND
        ancestor-distributed pairs (per B5).
        """
        bodies: dict[str, dict[str, Any]] = {}
        latest_state: dict[str, str] = {}
        for log in self._logs_in_scope():
            for evt in log.replay():
                pair = _pair_from_event(evt)
                if pair is not None:
                    bodies[evt.pair_id] = pair
                if evt.lifecycle_state:
                    latest_state[evt.pair_id] = evt.lifecycle_state

        result = [
            bodies[pid] for pid in bodies
            if latest_state.get(pid) not in ("deleted", "purged", "rejected")
        ]

        existing_ids = {p.get("id") for p in result}

        # memory→versum read: include knowledge bodies the versum sink holds that
        # the log doesn't already surface, honoring log deletion state (a pair the
        # log marks deleted stays hidden until versum-side erasure lands; versum
        # already hides its own erased knowledge).
        for pid, body in self._versum_knowledge_pairs().items():
            if pid in existing_ids:
                continue
            if latest_state.get(pid) in ("deleted", "purged", "rejected"):
                continue
            result.append(body)
            existing_ids.add(pid)

        # B5: include ancestor-distributed pairs.
        anc_bodies, _, _ = self._ancestor_distributed_aggregation()
        for pid, body in anc_bodies.items():
            if pid not in existing_ids:
                result.append(body)
        return result


# ===========================================================================
# Helpers
# ===========================================================================


def _stable_id(*parts: str) -> str:
    payload = "\x1f".join(p.strip().lower() for p in parts if p).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()[:32]


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
