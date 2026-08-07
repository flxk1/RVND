# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Erasure orchestration — the first-class verb for GDPR Art. 17 sweeps.

The low-level primitive is ``mutation_log.purge(pair_id, ...)`` which
writes a single tombstone per pair. ``erasure`` is the WORKFLOW layer
that:

  1. Sweeps a folder for all events referencing a subject (text match
     across pair bodies, capture_llm prompts/responses, capture_web
     payloads, derived pairs) and for draft and saved card files carrying
     the subject.
  2. Optionally cascades to descendant folders (folder graph walk).
  3. Composes ONE composite tombstone summarising the whole sweep.
  4. Writes to forgotten_subjects ledger to refuse future re-ingestion of
     the same subject text.

This is a controller-facing workflow. Status: beta, experimental, no
legal advice, ongoing — controller's decision.

Three-state intake model (D4):

  - ``request``  → write an ``ERASURE_REQUESTED`` audit event; do NOT act.
                   Use when an intake form fires and human review must come
                   before the sweep.
  - ``sweep``    → dry-run preview, returns the SweepReport.
  - ``execute``  → run the sweep + perform purges + write composite
                   tombstone + add to forgotten_subjects.

Failure modes are loud:

  - Missing controller key → ``RuntimeError``.
  - Missing legal_basis / requester_ref / reason → ``ValueError``.
  - Subject is empty / whitespace-only → ``ValueError``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import card_store, draft_store, forgotten_subjects
from .memory import WorkspaceMemory, _pair_from_event, discover_descendants
from .redaction import replace_ci
from .mutation_log import (
    LOG_ROOT_DEFAULT,
    LogEvent,
    MutationLog,
    VALID_LEGAL_BASES,
)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class SweepHit:
    """One match for the subject inside one folder's log."""

    folder:     str
    pair_id:    str
    kind:       str       # "pair" | "capture_llm" | "capture_web" | "derived" | "draft" | "card"
    audit_id:   str = ""
    snippet:    str = ""  # short, redacted preview of where the match fired


@dataclass
class SweepReport:
    """What the sweep would touch — preview before execute."""

    subject:               str
    folder_context:        str
    cascade:               bool
    hits_by_kind:          dict[str, list[SweepHit]] = field(default_factory=dict)
    hits_by_folder:        dict[str, int] = field(default_factory=dict)
    estimated_tombstone:   dict[str, Any] = field(default_factory=dict)
    #: Folders whose drafts could not be inspected (sealed): their draft
    #: hit-count is unknown, not zero — the preview names its blind spot.
    drafts_sealed:         list[str] = field(default_factory=list)
    #: Same blind spot for saved card files.
    cards_sealed:          list[str] = field(default_factory=list)

    def total_hits(self) -> int:
        return sum(len(v) for v in self.hits_by_kind.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject":             self.subject,
            "folder_context":      self.folder_context,
            "cascade":             self.cascade,
            "hits_by_kind": {
                k: [asdict(h) for h in v]
                for k, v in self.hits_by_kind.items()
            },
            "hits_by_folder":      dict(self.hits_by_folder),
            "estimated_tombstone": dict(self.estimated_tombstone),
            "drafts_sealed":       list(self.drafts_sealed),
            "cards_sealed":        list(self.cards_sealed),
            "total_hits":          self.total_hits(),
        }


class ErasureGuardRegistrationError(RuntimeError):
    """The durable re-ingestion guard could not be established."""


@dataclass
class ExecutionReport:
    """What ``execute`` actually did."""

    request_id:               str
    subject:                  str
    folder_context:           str
    cascade:                  bool
    dry_run:                  bool
    sweep:                    SweepReport
    purged_event_count:       int = 0          # sum of individual purge results
    purged_pairs:             list[str] = field(default_factory=list)
    composite_tombstone_id:   str = ""
    forgotten_subject_hash:   str = ""
    cascade_manifest:         dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions_previews_scrubbed: int = 0   # CL4: Privacy Lock previews scrubbed for the subject
    draft_surfaces_redacted:  int = 0      # draft files rewritten clean of the subject
    draft_surfaces_deleted:   int = 0      # unparseable draft files removed
    card_files_redacted:      int = 0      # card files rewritten clean of the subject
    card_files_deleted:       int = 0      # identity-matching or unreadable card files removed
    replayed_noop:            bool = False # True when a replay of an already-erased subject wrote nothing new

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id":             self.request_id,
            "subject":                self.subject,
            "folder_context":         self.folder_context,
            "cascade":                self.cascade,
            "dry_run":                self.dry_run,
            "sweep":                  self.sweep.to_dict(),
            "purged_event_count":     self.purged_event_count,
            "purged_pairs":           list(self.purged_pairs),
            "composite_tombstone_id": self.composite_tombstone_id,
            "forgotten_subject_hash": self.forgotten_subject_hash,
            "cascade_manifest":       dict(self.cascade_manifest),
            "decisions_previews_scrubbed": self.decisions_previews_scrubbed,
            "draft_surfaces_redacted": self.draft_surfaces_redacted,
            "draft_surfaces_deleted":  self.draft_surfaces_deleted,
            "card_files_redacted":    self.card_files_redacted,
            "card_files_deleted":     self.card_files_deleted,
            "replayed_noop":          self.replayed_noop,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_subject(subject: str) -> str:
    if not subject or not subject.strip():
        raise ValueError("erasure subject must be non-empty")
    return subject.strip()


def _validate_legal_basis(legal_basis: str) -> None:
    if not legal_basis:
        raise ValueError(
            "erasure requires legal_basis (one of "
            f"{sorted(VALID_LEGAL_BASES)})"
        )
    if legal_basis not in VALID_LEGAL_BASES:
        raise ValueError(
            f"unknown legal_basis '{legal_basis}'. Valid: "
            f"{sorted(VALID_LEGAL_BASES)}"
        )


def _short(text: str, n: int = 120) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= n:
        return text
    return text[: n - 1] + "..."


def _redacted_snippet(text: str, subject: str, n: int = 80) -> str:
    """Return a tiny preview of where the subject was found, with the
    subject itself replaced by ``[REDACTED]`` so the snippet itself does
    not leak the subject we are erasing.
    """
    if not text:
        return ""
    redacted = text
    s_lower = subject.strip().lower()
    if s_lower:
        # Case-insensitive replace by walking lowercased copy.
        lower = text.lower()
        idx = lower.find(s_lower)
        if idx >= 0:
            redacted = text[:idx] + "[REDACTED]" + text[idx + len(subject):]
    return _short(redacted, n)


def _event_text_haystack(evt: LogEvent, pair: dict | None = None) -> str:
    """Stitch together every text field on an event into one string we
    can search across. Conservative — better to over-match here (a
    SweepReport is just a preview) than to miss a hit.

    ``pair`` is the resolved pair body: post body-drop a knowledge pair's body
    lives in the versum sink, not the log event, so the caller resolves it (log
    body or versum body) and passes it in; falls back to the log body otherwise.
    """
    parts: list[str] = []
    # Pair body (resolved from the log event, or the versum sink post body-drop).
    if pair is None:
        pair = _pair_from_event(evt)
    if isinstance(pair, dict):
        problem = pair.get("problem", {}) or {}
        if isinstance(problem, dict):
            parts.append(str(problem.get("summary", "")))
            facets = problem.get("facets", {}) or {}
            if isinstance(facets, dict):
                for v in facets.values():
                    if isinstance(v, str):
                        parts.append(v)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, str):
                                parts.append(item)
        solution = pair.get("solution", {}) or {}
        if isinstance(solution, dict):
            parts.append(str(solution.get("body", "")))
            for src in solution.get("cited_sources", []) or []:
                if isinstance(src, str):
                    parts.append(src)
    # Extra payload (system events / capture metadata).
    if isinstance(evt.extra, dict):
        for k, v in evt.extra.items():
            if isinstance(v, str):
                parts.append(v)
    return "\n".join(parts)


def _classify_kind(evt: LogEvent, pair: dict | None = None) -> str:
    """Best-effort kind label for the sweep report — what category of
    event the subject was found in. ``pair`` is the resolved body (log or versum
    post body-drop); falls back to the log body."""
    channel = (evt.channel or "").lower()
    if channel == "llm_answer":
        return "capture_llm"
    if channel == "websearch":
        return "capture_web"
    if pair is None:
        pair = _pair_from_event(evt)
    if pair is not None:
        problem = pair.get("problem", {}) if isinstance(pair, dict) else {}
        ptype = (problem.get("type") or "").lower() if isinstance(problem, dict) else ""
        if ptype == "llm_exchange":
            return "capture_llm"
        if ptype == "websearch":
            return "capture_web"
        # If the pair is derived from another (cascade lineage), label
        # it "derived"; else "pair".
        if isinstance(problem, dict) and problem.get("derived_from"):
            return "derived"
    return "pair"


# ---------------------------------------------------------------------------
# Folder graph walk (own + descendants if cascade)
# ---------------------------------------------------------------------------


def _logs_for_sweep(
    folder_context: str,
    cascade: bool,
    log_root: Path | None,
) -> list[MutationLog]:
    folder = str(Path(folder_context).expanduser().resolve())
    paths = [folder]
    if cascade:
        # discover_descendants returns folders that have logs; we add the
        # root itself if it isn't already present.
        descendants = discover_descendants(folder, log_root=log_root)
        for d in descendants:
            if d not in paths:
                paths.append(d)
    return [MutationLog(p, log_root=log_root) for p in paths]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sweep(
    folder_context: str,
    subject: str,
    *,
    cascade: bool = False,
    log_root: Path | None = None,
) -> SweepReport:
    """Find every event referencing ``subject`` in the folder (+ descendants),
    plus draft files carrying the subject (drafts are free text beside the
    chain; ``execute`` rewrites them via ``draft_store.redact``) and saved
    card files (JSON under ``<folder>/cards/``; ``execute`` rewrites or
    deletes them via ``card_store.redact``). A sealed folder's drafts and
    cards cannot be inspected; ``drafts_sealed`` / ``cards_sealed`` name
    those folders so the preview never reports an uninspected file as clean.

    Returns a :class:`SweepReport`; ``execute(..., dry_run=True)`` wraps
    this and adds the would-be tombstone shape.
    """
    subject_norm = _validate_subject(subject)
    needle = subject_norm.lower()
    root = Path(folder_context).expanduser().resolve()

    report = SweepReport(
        subject=subject_norm,
        folder_context=str(root),
        cascade=bool(cascade),
        hits_by_kind={"pair": [], "capture_llm": [], "capture_web": [],
                      "derived": [], "draft": [], "card": []},
        hits_by_folder={},
    )

    for log in _logs_for_sweep(str(root), cascade, log_root):
        folder_str = log.folder_path
        # post body-drop, a knowledge pair's body lives in the folder's versum
        # sink, not the log event — load it so the sweep still sees body text/PII.
        try:
            from .adapters.versum import read_disk_versum_records
            versum_bodies = {
                b["id"]: b for b in
                (r.get("properties", {}).get("record") for r in
                 read_disk_versum_records(folder_str))
                if isinstance(b, dict) and isinstance(b.get("id"), str)
            }
        except Exception:                                       # noqa: BLE001
            versum_bodies = {}
        for evt in log.replay():
            pair = _pair_from_event(evt) or versum_bodies.get(evt.pair_id)
            haystack = _event_text_haystack(evt, pair).lower()
            if needle and needle in haystack:
                kind = _classify_kind(evt, pair)
                hit = SweepHit(
                    folder=folder_str,
                    pair_id=evt.pair_id,
                    kind=kind,
                    audit_id=evt.audit_id,
                    snippet=_redacted_snippet(
                        _event_text_haystack(evt, pair), subject_norm
                    ),
                )
                report.hits_by_kind.setdefault(kind, []).append(hit)
                report.hits_by_folder[folder_str] = (
                    report.hits_by_folder.get(folder_str, 0) + 1
                )
        # Draft files: matched surfaces, plus unparseable files (those
        # cannot be certified clean, so execute deletes them). A sealed
        # folder's drafts cannot be inspected — name the blind spot rather
        # than report zero hits as if the drafts were clean.
        drafts = draft_store.scan(folder_str, subject_norm, log_root=log_root)
        if drafts.get("sealed"):
            report.drafts_sealed.append(folder_str)
        for surface, n in sorted(drafts["hits"].items()):
            report.hits_by_kind["draft"].append(SweepHit(
                folder=folder_str, pair_id=f"draft:{surface}", kind="draft",
                snippet=f"{surface}: {n} occurrence(s)"))
            report.hits_by_folder[folder_str] = (
                report.hits_by_folder.get(folder_str, 0) + 1)
        for surface in drafts["unreadable"]:
            report.hits_by_kind["draft"].append(SweepHit(
                folder=folder_str, pair_id=f"draft:{surface}", kind="draft",
                snippet=f"{surface}: unreadable draft file — deleted on execute"))
            report.hits_by_folder[folder_str] = (
                report.hits_by_folder.get(folder_str, 0) + 1)
        # Saved card files: the chain purge alone leaves <folder>/cards/*.json
        # behind, so the sweep reaches them too. Identity and unreadable ids
        # may carry the subject (the card id is the file name), so they pass
        # through replace_ci before display; a field-hit id cannot carry it
        # as a whole word — that would classify as identity.
        cards = card_store.scan(folder_str, subject_norm, log_root=log_root)
        if cards.get("sealed"):
            report.cards_sealed.append(folder_str)
        for cid, n in sorted(cards["hits"].items()):
            report.hits_by_kind["card"].append(SweepHit(
                folder=folder_str, pair_id=f"card-file:{cid}", kind="card",
                snippet=f"{n} occurrence(s) in card fields"))
            report.hits_by_folder[folder_str] = (
                report.hits_by_folder.get(folder_str, 0) + 1)
        for cid in cards["identity"]:
            safe, _ = replace_ci(cid, subject_norm)
            report.hits_by_kind["card"].append(SweepHit(
                folder=folder_str, pair_id=f"card-file:{safe}", kind="card",
                snippet="card id carries the subject — deleted on execute"))
            report.hits_by_folder[folder_str] = (
                report.hits_by_folder.get(folder_str, 0) + 1)
        for cid in cards["unreadable"]:
            safe, _ = replace_ci(cid, subject_norm)
            report.hits_by_kind["card"].append(SweepHit(
                folder=folder_str, pair_id=f"card-file:{safe}", kind="card",
                snippet="unreadable card file — deleted on execute"))
            report.hits_by_folder[folder_str] = (
                report.hits_by_folder.get(folder_str, 0) + 1)

    # Estimated tombstone shape — the dict that ``execute`` would write
    # as a composite event_type="erasure_composite". Draft and card hits
    # are files, not chain pairs, so they stay out of the pair counts.
    pair_ids = sorted({
        h.pair_id
        for kind, hits in report.hits_by_kind.items()
        if kind not in ("draft", "card")
        for h in hits
    })
    report.estimated_tombstone = {
        "kind":                    "erasure_composite",
        "subject_preview":         "[REDACTED]",   # subject itself never goes on chain
        "affected_pair_count":     len(pair_ids),
        "affected_folder_count":   len(report.hits_by_folder),
        "hits_by_kind_count": {
            k: len(v) for k, v in report.hits_by_kind.items()
        },
    }
    return report


def dry_run(
    folder_context: str,
    subject: str,
    *,
    cascade: bool = False,
    log_root: Path | None = None,
) -> SweepReport:
    """Alias of :func:`sweep` — preview only, no writes."""
    return sweep(folder_context, subject, cascade=cascade, log_root=log_root)


def request(
    folder_context: str,
    subject: str,
    *,
    requester_ref: str,
    reason: str,
    log_root: Path | None = None,
    actor: str = "user",
) -> dict[str, Any]:
    """Two-phase intake (D4): write an ERASURE_REQUESTED audit event.

    Does NOT sweep or purge. Use when an intake form / ticket fires and a
    human review must come BEFORE the sweep+purge. Returns the request id
    (for later ``erase-status`` lookups) and the audit event id.
    """
    subject_norm = _validate_subject(subject)
    if not requester_ref:
        raise ValueError("erasure request requires requester_ref")
    if not reason:
        raise ValueError("erasure request requires reason")

    request_id = "erase-req:" + uuid.uuid4().hex[:16]
    folder = str(Path(folder_context).expanduser().resolve())
    log = MutationLog(folder, log_root=log_root or LOG_ROOT_DEFAULT)
    audit_id = log.append(LogEvent(
        event="system",
        folder_path=folder,
        pair_id=f"erasure:request:{request_id}",
        channel="system",
        actor=actor,
        extra={
            "kind":          "ERASURE_REQUESTED",
            "request_id":    request_id,
            "requester_ref": requester_ref,
            # The reason is requester/operator free text and this event is
            # permanent — "Jane Doe asked us to forget her" would engrave
            # the very subject, so it is scrubbed like every other on-chain
            # text field.
            "reason":        replace_ci(reason, subject_norm)[0],
            # Subject preview is REDACTED on-chain — the audit trail
            # should not become a way to recover what we may shortly be
            # asked to forget.
            "subject_preview": "[REDACTED]",
            "subject_length":  len(subject_norm),
        },
    ))
    return {
        "request_id": request_id,
        "audit_id":   audit_id,
        "folder":     folder,
    }


def status(
    folder_context: str,
    request_id: str,
    *,
    log_root: Path | None = None,
) -> dict[str, Any]:
    """Return the cascade manifest for an erasure request.

    Walks the folder's log for the matching ERASURE_REQUESTED event and
    every erasure_composite / purge event that references the same
    ``request_id``. Returns the union as a manifest.
    """
    folder = str(Path(folder_context).expanduser().resolve())
    log = MutationLog(folder, log_root=log_root or LOG_ROOT_DEFAULT)

    manifest: dict[str, Any] = {
        "request_id":  request_id,
        "folder":      folder,
        "requested":   None,
        "executed":    None,
        "purges":      [],
        "forgotten":   None,
    }
    # First pass: walk the chain to collect ERASURE_REQUESTED, composite,
    # forgotten breadcrumb, and the tracker events that pair up with
    # individual purges. The tracker (`erasure_pair_purge_start`) carries
    # `erasure_request_id` + `pair_id`, so it threads the underlying
    # primitive's purge tombstones back to this request.
    tracked_pair_ids: set[str] = set()
    for evt in log.replay():
        extra = evt.extra if isinstance(evt.extra, dict) else {}
        kind = extra.get("kind", "")
        rid = extra.get("request_id", "")
        if kind == "ERASURE_REQUESTED" and rid == request_id:
            manifest["requested"] = {
                "audit_id": evt.audit_id,
                "ts":       evt.ts,
                "actor":    evt.actor,
            }
        elif kind == "erasure_composite" and rid == request_id:
            manifest["executed"] = {
                "audit_id":              evt.audit_id,
                "ts":                    evt.ts,
                "purged_pair_count":     extra.get("affected_pair_count", 0),
                "affected_folder_count": extra.get("affected_folder_count", 0),
                "hits_by_kind_count":    extra.get("hits_by_kind_count", {}),
            }
        elif kind == "erasure_pair_purge_start" and (
            extra.get("erasure_request_id") == request_id or rid == request_id
        ):
            # The tracker's own ``pair_id`` is a synthetic
            # ``erasure-track:<request_id>:<ref>``; the purged pair is
            # named by ``extra.purged_pair_ref``, the same opaque ref the
            # purge tombstone carries as its pair_id. Legacy chains wrote
            # the raw id under ``purged_pair_id`` — and their tombstones
            # carry the raw id too, so equality stitching works either way.
            purged_pid = (extra.get("purged_pair_ref")
                          or extra.get("purged_pair_id", ""))
            if purged_pid:
                tracked_pair_ids.add(purged_pid)
        elif kind == "forgotten_subject_added" and rid == request_id:
            manifest["forgotten"] = {
                "audit_id":       evt.audit_id,
                "ts":             evt.ts,
                "subject_hash":   extra.get("subject_hash", ""),
            }

    # Second pass: for every actual `purge` event whose pair_id we
    # tracked, include it. The purge events themselves don't carry our
    # request id (the underlying primitive doesn't know about it) — the
    # tracker breadcrumb is how we stitch.
    for evt in log.replay():
        if evt.event != "purge":
            continue
        if evt.pair_id not in tracked_pair_ids:
            continue
        extra = evt.extra if isinstance(evt.extra, dict) else {}
        manifest["purges"].append({
            "audit_id":           evt.audit_id,
            "ts":                 evt.ts,
            "pair_id":            evt.pair_id,
            "legal_basis":        extra.get("legal_basis", ""),
            "purged_event_count": extra.get("purged_event_count", 0),
        })
    return manifest


def _write_forgotten_breadcrumb(root_log, subject_hash, *,
                                request_id, actor) -> None:
    """Best-effort audit breadcrumb after durable guard registration."""
    try:
        root_log.append(LogEvent(
            event="system",
            folder_path=root_log.folder_path,
            pair_id=f"forgotten:{subject_hash[:16]}",
            channel="system",
            actor=f"erasure:{actor}",
            extra={
                "kind":         "forgotten_subject_added",
                "request_id":   request_id,
                "subject_hash": subject_hash,
            },
        ))
    except Exception:
        # The ledger is already durable; this breadcrumb is diagnostic only.
        pass


def execute(
    folder_context: str,
    subject: str,
    *,
    legal_basis: str,
    requester_ref: str,
    reason: str,
    controller_signer: Any = None,   # accepted but unused — mutation_log.purge auto-uses controller key
    cascade: bool = False,
    dry_run: bool = False,
    log_root: Path | None = None,
    actor: str = "user",
    request_id: str | None = None,
) -> ExecutionReport:
    """Sweep → purge → composite tombstone → forgotten_subjects.

    Args:
        folder_context: workspace path the erasure operates against.
        subject:        the subject text to erase (PII identifier, name, etc.).
        legal_basis:    GDPR Art. 17(1) ground; required.
        requester_ref:  opaque reference to the requesting subject; required.
        reason:         free-text reason; required. Scrubbed of the subject
            before it lands on any permanent record.
        controller_signer: accepted for API symmetry; the underlying
            ``mutation_log.purge`` already uses the controller key (raises
            ``RuntimeError`` if uninitialised). Pass ``None`` and let the
            log do it.
        cascade:        when True, also sweep + purge descendant folders.
        dry_run:        when True, runs the sweep but performs no writes.
        log_root:       override for tests.
        actor:          who's running this (recorded on tombstone).
        request_id:     reuse the id returned by :func:`request`. If None
            and not dry_run, a fresh one is minted.

    Returns:
        :class:`ExecutionReport`.

    Controller co-signature is optional. If a controller keypair exists the
    tombstone is co-signed (two-key erasure); otherwise the operator signature
    alone authorises it (single-key) and ``erasure_mode`` records which.

    Raises:
        ValueError: if legal_basis / requester_ref / reason / subject
            are missing or invalid.
    """
    subject_norm = _validate_subject(subject)
    _validate_legal_basis(legal_basis)
    if not requester_ref:
        raise ValueError("erasure execute requires requester_ref")
    if not reason:
        raise ValueError("erasure execute requires reason")

    # Controller co-signature is optional (L0-first). If a controller
    # keypair exists, the purge tombstone is co-signed (two-key erasure);
    # otherwise it proceeds with the operator signature alone (single-key)
    # and records ``erasure_mode`` on the tombstone. No forced ceremony at
    # L0 — see ``MutationLog.purge`` for the rationale.

    if request_id is None or not request_id:
        request_id = "erase-req:" + uuid.uuid4().hex[:16]

    # The reason is operator free text and lands on permanent records (the
    # per-pair tombstones and the composite). "erase Jane Doe per DSAR"
    # would engrave the very subject being erased — scrub it once here.
    reason_safe = replace_ci(reason, subject_norm)[0]

    # 1) Sweep first — this is the basis for the composite tombstone +
    # the list of pairs to purge.
    sweep_report = sweep(
        folder_context, subject_norm,
        cascade=cascade, log_root=log_root,
    )

    report = ExecutionReport(
        request_id=request_id,
        subject="[REDACTED]",   # don't echo back the subject text
        folder_context=sweep_report.folder_context,
        cascade=cascade,
        dry_run=dry_run,
        sweep=sweep_report,
    )

    if dry_run:
        return report

    # Establish the durable re-ingestion guard before the first destructive
    # operation. If this fails, no purge, file redaction, or tombstone has
    # started. Retain the pre-registration membership state for replay logic.
    try:
        subject_hash, guard_added = forgotten_subjects.ensure(
            sweep_report.folder_context,
            subject_norm,
            request_id=request_id,
        )
    except Exception as exc:
        raise ErasureGuardRegistrationError(
            "forgotten-subject guard could not be durably registered; "
            "erasure not started"
        ) from exc
    report.forgotten_subject_hash = subject_hash
    was_already_forgotten = not guard_added

    # 2) Per-folder purges. We need a separate WorkspaceMemory per affected
    # folder so we can call purge_pair on the right log. Draft and card
    # hits are files, not chain pairs — they are redacted below, never
    # purged.
    cascade_manifest: dict[str, dict[str, Any]] = {}
    affected_pair_ids: set[str] = set()
    for kind, hits in sweep_report.hits_by_kind.items():
        if kind in ("draft", "card"):
            continue
        for h in hits:
            affected_pair_ids.add(h.pair_id)

    # Group pair_ids by folder for efficient per-log purges.
    per_folder_pairs: dict[str, set[str]] = {}
    for kind, hits in sweep_report.hits_by_kind.items():
        if kind in ("draft", "card"):
            continue
        for h in hits:
            per_folder_pairs.setdefault(h.folder, set()).add(h.pair_id)

    total_purged = 0
    purged_pairs_list: list[str] = []
    affected_pair_refs: set[str] = set()
    for folder, pair_ids in per_folder_pairs.items():
        # Use the per-folder log directly; we want the purge tombstones
        # to land in the same log as the events being erased.
        log = MutationLog(folder, log_root=log_root or LOG_ROOT_DEFAULT)
        per_folder = {"purged_pair_count": 0, "purged_event_count": 0, "pairs": []}
        for pid in sorted(pair_ids):
            # We extend the underlying purge call with our request id so
            # the per-pair tombstones can be threaded back to the
            # composite at status() time. The cleanest path is to call
            # MutationLog.purge with reason that carries the request_id;
            # but reason is already user-provided. So we use a sentinel
            # prefix in reason for now (status() pattern matches on
            # erasure_request_id in extra — we'd need a richer purge
            # signature to thread that through). Workaround: emit a
            # tracker event before each purge so status() can stitch.
            #
            # The tracker outlives the purge it announces, so it names the
            # pair only through the same opaque folder-salted ref the purge
            # tombstone will carry — a raw pid (e.g. a legacy ``card:<name>``
            # id) would be re-engraved by its own erasure. Same-string on
            # both sides is what lets status() stitch by plain equality.
            ref = forgotten_subjects.purged_pair_ref(folder, pid)
            affected_pair_refs.add(ref)
            try:
                # IMPORTANT: the tracker uses a DIFFERENT pair_id from
                # the one being purged. ``mutation_log.purge`` removes
                # every event referencing the target pair_id; if the
                # tracker carried ``pair_id=pid`` it would be wiped by
                # the very call it is announcing. Using a derived id
                # (``erasure-track:<request_id>:<ref>``) keeps the
                # breadcrumb on-chain so ``erase_status`` can stitch.
                tracker_pid = f"erasure-track:{request_id}:{ref}"
                log.append(LogEvent(
                    event="system",
                    folder_path=folder,
                    pair_id=tracker_pid,
                    channel="system",
                    actor=f"erasure:{actor}",
                    extra={
                        "kind":              "erasure_pair_purge_start",
                        "request_id":        request_id,
                        "erasure_request_id": request_id,
                        "purged_pair_ref":   ref,
                        "subject_preview":   "[REDACTED]",
                    },
                ))
            except Exception:
                pass
            try:
                n = log.purge(
                    pid,
                    legal_basis=legal_basis,
                    requester_ref=requester_ref,
                    reason=f"[erase-req:{request_id}] {reason_safe}",
                )
            except Exception as e:  # pragma: no cover - surfaces in report
                per_folder.setdefault("errors", []).append({
                    "pair_id": pid, "error": f"{type(e).__name__}: {e}"
                })
                continue
            per_folder["purged_event_count"] += int(n)
            per_folder["purged_pair_count"] += 1 if n else 0
            per_folder["pairs"].append(pid)
            if n:
                total_purged += int(n)
                purged_pairs_list.append(pid)
        cascade_manifest[folder] = per_folder

    # The composite's affected_folder_count mirrors the preview: a folder
    # counts when a purge OR a file action (draft/card) touched it.
    affected_folders: set[str] = set(per_folder_pairs)

    # 2b) Draft files across the sweep scope: rewrite every occurrence of
    # the subject; an unparseable draft is deleted (it cannot be certified
    # clean). Runs before the composite tombstone so the counts land on it.
    drafts_redacted = drafts_deleted = 0
    for log in _logs_for_sweep(sweep_report.folder_context, cascade, log_root):
        folder = log.folder_path
        try:
            dr = draft_store.redact(folder, subject_norm, log_root=log_root)
        except Exception as e:  # pragma: no cover - surfaces in report
            cascade_manifest.setdefault(folder, {}).setdefault(
                "errors", []).append({"drafts": f"{type(e).__name__}: {e}"})
            continue
        if not dr.get("ok"):
            cascade_manifest.setdefault(folder, {}).setdefault(
                "errors", []).append({"drafts": dr.get("error", "refused")})
            continue
        if dr["redacted"] or dr["deleted"]:
            cascade_manifest.setdefault(folder, {})["drafts"] = {
                "redacted": dict(dr["redacted"]), "deleted": list(dr["deleted"])}
            drafts_redacted += len(dr["redacted"])
            drafts_deleted += len(dr["deleted"])
            affected_folders.add(folder)
    report.draft_surfaces_redacted = drafts_redacted
    report.draft_surfaces_deleted = drafts_deleted

    # 2c) Saved card files across the sweep scope: delete cards whose id
    # carries the subject, rewrite occurrences inside the rest; unreadable
    # files go too (they cannot be certified clean). Runs before the
    # composite tombstone so the counts land on it. Deleted ids and OS
    # error strings may carry the subject (the id is the file name), so
    # both pass through replace_ci before entering the manifest.
    cards_redacted = cards_deleted = 0
    for log in _logs_for_sweep(sweep_report.folder_context, cascade, log_root):
        folder = log.folder_path
        try:
            cr = card_store.redact(folder, subject_norm, log_root=log_root)
        except Exception as e:  # pragma: no cover - surfaces in report
            cascade_manifest.setdefault(folder, {}).setdefault(
                "errors", []).append(
                {"cards": replace_ci(f"{type(e).__name__}: {e}",
                                     subject_norm)[0]})
            continue
        if not cr.get("ok"):
            cascade_manifest.setdefault(folder, {}).setdefault(
                "errors", []).append({"cards": cr.get("error", "refused")})
            continue
        if cr["redacted"] or cr["deleted"]:
            cascade_manifest.setdefault(folder, {})["cards"] = {
                "redacted": dict(cr["redacted"]),
                "deleted": [replace_ci(c, subject_norm)[0]
                            for c in cr["deleted"]],
            }
            cards_redacted += len(cr["redacted"])
            cards_deleted += len(cr["deleted"])
            affected_folders.add(folder)
    report.card_files_redacted = cards_redacted
    report.card_files_deleted = cards_deleted

    report.purged_event_count = total_purged
    report.purged_pairs = sorted(affected_pair_ids)
    report.cascade_manifest = cascade_manifest

    # Replay-safety (MCP delivery is at-least-once). A second execute of a
    # subject already in the forgotten ledger, where this run purged and
    # redacted nothing new, is a duplicate delivery — the per-pair purges are
    # already idempotent (the sweep finds nothing, so no tombstones are
    # rewritten), but the composite tombstone, the ledger row, and the
    # breadcrumb would otherwise be appended unconditionally, forking the
    # audit record for zero new effect. Detect the pure replay and skip those
    # non-idempotent writes; a genuine re-erasure (new data slipped in) still
    # has effect and takes the normal path.
    had_effect = bool(
        total_purged or drafts_redacted or drafts_deleted
        or cards_redacted or cards_deleted)

    if was_already_forgotten and not had_effect:
        report.replayed_noop = True
        # The first execute recorded the composite + ledger entry; nothing
        # new happened here. Fall through to the idempotent decisions-store
        # scrub below without writing a duplicate composite or ledger row.
    else:
        # 3) Composite tombstone in the ROOT folder summarising the whole
        # sweep — distinct from the individual per-pair purge tombstones
        # mutation_log.purge already wrote.
        root_log = MutationLog(
            sweep_report.folder_context,
            log_root=log_root or LOG_ROOT_DEFAULT,
        )
        composite_id = "erase-composite:" + uuid.uuid4().hex[:16]
        root_log.append(LogEvent(
            event="system",
            folder_path=sweep_report.folder_context,
            pair_id=composite_id,
            channel="system",
            actor=f"erasure:{actor}",
            extra={
                "kind":                    "erasure_composite",
                "request_id":              request_id,
                "legal_basis":             legal_basis,
                "requester_ref":           requester_ref,
                "reason":                  reason_safe,
                "subject_preview":         "[REDACTED]",
                "subject_length":          len(subject_norm),
                "affected_pair_count":     len(affected_pair_ids),
                "affected_folder_count":   len(affected_folders),
                "purged_event_count":      total_purged,
                "purged_pair_refs":        sorted(affected_pair_refs),
                "hits_by_kind_count": {
                    k: len(v) for k, v in sweep_report.hits_by_kind.items()
                },
                "draft_surfaces_redacted": drafts_redacted,
                "draft_surfaces_deleted":  drafts_deleted,
                "card_files_redacted":     cards_redacted,
                "card_files_deleted":      cards_deleted,
                "cascade":                 bool(cascade),
            },
        ))
        report.composite_tombstone_id = composite_id

        # 4) forgotten_subjects ledger — refuse future re-ingestion. Only add
        # when the subject is not already on the list, so a re-erasure that
        # had new effect documents the purge (composite above) without a
        # duplicate ledger row.
        if guard_added:
            _write_forgotten_breadcrumb(
                root_log, subject_hash,
                request_id=request_id, actor=actor)

    # 5) Scrub the Privacy Lock decisions store of any human-readable preview
    # still carrying the subject (CL4 — the GDPR erase must reach decisions.jsonl
    # too, not just the chain). Best-effort: the decisions store is global and
    # redacted-at-write, so this targets residue the redactor can't catch (plain
    # names / terms) + legacy rows. Never blocks the load-bearing chain purge.
    try:
        from .lock import DecisionsStore
        report.decisions_previews_scrubbed = DecisionsStore().erase_subject(subject_norm)
    except Exception:
        pass

    return report


# Re-export EraseGuardHit so callers can ``from workspaces.erasure import
# EraseGuardHit`` without reaching into forgotten_subjects directly.
EraseGuardHit = forgotten_subjects.EraseGuardHit


__all__ = [
    "EraseGuardHit",
    "ErasureGuardRegistrationError",
    "ExecutionReport",
    "SweepHit",
    "SweepReport",
    "dry_run",
    "execute",
    "request",
    "status",
    "sweep",
]
