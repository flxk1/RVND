# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Folder mirrors — Lock + Oversight outputs as files.

A folder mirror is a derived file the controller can review, compare against
the original, and (for Lock mirrors) approve into the Oversight surface.
Each mirror has a sibling ``.spans.json`` sidecar listing the byte-ranges
that were transformed and why.

Layout (per folder):

    <folder>/mirrors/lock/<orig>.cleaned.md
    <folder>/mirrors/lock/<orig>.spans.json
    <folder>/mirrors/oversight/<orig>.approved.md
    <folder>/mirrors/oversight/<orig>.spans.json

Every mirror operation appends an audit event to the folder's mutation log
(``event="system"``, ``extra.kind`` ∈ {"mirror_lock", "mirror_oversight"}).
The audit event carries the source path, mirror path, and the span count —
not the spans themselves; those live in the sidecar.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal

from .mutation_log import LogEvent, MutationLog


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


SPANS_SCHEMA = "workspace.mirror.spans/v1"

MirrorKind = Literal["lock", "oversight"]


class MirrorRedactionError(RuntimeError):
    """Raised when Lock cannot prove that mirror output is safe to write."""


@dataclass
class Span:
    """One transformed region of the source."""

    start: int            # byte offset in the original source
    end: int              # byte offset (exclusive)
    kind: str             # tier_b.email, tier_a.iban, etc.
    original_hash: str    # sha256 of the original span text
    replacement: str      # what replaced the span

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MirrorRecord:
    """The result of generating or approving a mirror."""

    source_path: str
    mirror_path: str
    spans_path: str
    kind: MirrorKind
    created_at: float
    audit_id: str
    span_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mirrors_root(folder: str | Path) -> Path:
    return Path(folder).expanduser().resolve() / "mirrors"


def _lock_dir(folder: str | Path) -> Path:
    return _mirrors_root(folder) / "lock"


def _oversight_dir(folder: str | Path) -> Path:
    return _mirrors_root(folder) / "oversight"


def _sidecar_for(mirror_path: Path) -> Path:
    """The .spans.json that accompanies a mirror file.

    Naming convention: ``<basename>.spans.json`` next to the mirror.
    We strip the ``.cleaned.md`` / ``.approved.md`` suffix so the spans
    file is shared between the two stages of a single derivation.
    """
    name = mirror_path.name
    for suffix in (".cleaned.md", ".approved.md"):
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            return mirror_path.parent / f"{stem}.spans.json"
    # Fallback for non-standard names.
    return mirror_path.with_suffix(".spans.json")


def _safe_source_basename(source_path: str | Path) -> str:
    """Compute a deterministic filesystem-safe basename from the source.

    Preserves the original filename when possible; falls back to a SHA-256
    short hash if the source has no usable name component (rare).
    """
    name = Path(source_path).name
    if not name:
        return hashlib.sha256(str(source_path).encode()).hexdigest()[:16]
    # Strip anything that would confuse downstream tools (newlines, slashes).
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def _sha256_bytes(b: bytes) -> str:
    return f"sha256:{hashlib.sha256(b).hexdigest()}"


# ---------------------------------------------------------------------------
# Lock extraction — produces the cleaned text + spans
# ---------------------------------------------------------------------------


def _extract_spans_with_lock(text: str) -> tuple[str, list[Span]]:
    """Run Privacy Lock over a body of text and return (cleaned_text, spans).

    Strategy: ask workspaces.lock to redact the text, then walk the *redaction
    markers* in the cleaned output back against the original to recover the
    span byte offsets. workspaces.lock does NOT expose per-finding offsets in
    its public API as of 0.6.8, so we approximate by:

      1. Asking it for ``redacted_text`` (or a fallback iterative redaction).
      2. For each ``Finding``, locating its detail-driven pattern in the
         original, recording the byte range.

    The result is a "good-enough" span list for the user-review surface;
    downstream consumers should treat it as advisory, not as a canonical
    audit trail.
    """
    if not text:
        return "", []

    spans: list[Span] = []
    cleaned: str = text

    try:
        from workspaces.lock import lock_text, Mode
    except Exception as exc:
        raise MirrorRedactionError("Privacy Lock is unavailable") from exc

    try:
        decision = lock_text(text, mode=Mode.STANDARD, source="triple")
    except Exception as exc:
        raise MirrorRedactionError("Privacy Lock scan failed") from exc

    # Get a cleaned variant — even on a "refuse" we look for the
    # ``redact_and_retry`` remediation that carries a usable redacted_text.
    candidate_redacted: str | None = None
    if decision.action == "minimise":
        candidate_redacted = decision.redacted_text
    elif decision.action == "refuse":
        for f in decision.findings:
            for action in f.remediation_actions:
                payload = action.payload or {}
                if action.kind == "redact_and_retry" and payload.get("redacted_text"):
                    candidate_redacted = payload["redacted_text"]
                    break
            if candidate_redacted:
                break
    elif decision.action == "allow":
        return text, []

    if candidate_redacted is None:
        raise MirrorRedactionError(
            "Privacy Lock did not provide safe redacted text"
        )

    cleaned = candidate_redacted

    # Recover spans: for each finding, find the substring it replaced.
    # Heuristic — scan the original for what's now missing in the cleaned
    # text. We do this by walking through the cleaned text and the original
    # in parallel, identifying contiguous mismatched regions as spans.
    spans.extend(_diff_spans(text, cleaned, decision.findings))
    return cleaned, spans


def _diff_spans(original: str, cleaned: str,
                findings: list[Any]) -> list[Span]:
    """Walk original + cleaned in parallel; emit one Span per mismatched
    region.

    This is the simple, deterministic algorithm; it produces correct
    byte-offset ranges for every region where ``cleaned`` differs from
    ``original``. The ``kind`` per span is the first finding's type
    (approximation — workspaces.lock doesn't expose per-region attribution
    in 0.6.8).
    """
    spans: list[Span] = []
    default_kind = (
        f"tier_{(findings[0].tier or 'b').lower()}.{findings[0].type}"
        if findings else "tier_b.unknown"
    )

    i = j = 0
    while i < len(original) and j < len(cleaned):
        if original[i] == cleaned[j]:
            i += 1
            j += 1
            continue
        # Mismatch — find the end of the divergent run in both strings by
        # looking for the next aligned anchor.
        # Anchor heuristic: find the next character in cleaned that also
        # appears at-or-after position i in original AND realigns.
        # For practical PII redactions this works because the cleaned
        # text inserts a literal marker.
        # Find the next ']' in cleaned (end of [REDACTED:...] marker).
        end_marker_j = cleaned.find("]", j)
        if end_marker_j == -1:
            # Whole tail is divergent — treat as one span.
            spans.append(Span(
                start=i, end=len(original),
                kind=default_kind,
                original_hash=_sha256_bytes(original[i:].encode("utf-8")),
                replacement=cleaned[j:],
            ))
            return spans
        replacement = cleaned[j : end_marker_j + 1]
        # Resume cleaned after the marker.
        new_j = end_marker_j + 1
        # Find where original re-aligns with cleaned[new_j:]. We look for
        # the next short prefix of cleaned[new_j:] in original[i:].
        anchor = cleaned[new_j : new_j + 16] if new_j < len(cleaned) else ""
        if anchor:
            anchor_in_orig = original.find(anchor, i)
        else:
            anchor_in_orig = len(original)
        if anchor_in_orig == -1:
            anchor_in_orig = len(original)
        spans.append(Span(
            start=i, end=anchor_in_orig,
            kind=default_kind,
            original_hash=_sha256_bytes(
                original[i:anchor_in_orig].encode("utf-8")),
            replacement=replacement,
        ))
        i = anchor_in_orig
        j = new_j
    return spans


# ---------------------------------------------------------------------------
# Public API — generate, approve, list
# ---------------------------------------------------------------------------


def generate_lock_mirror(
    folder: str | Path,
    source_path: str | Path,
    *,
    log_root: str | Path | None = None,
    actor: str = "system:mirror",
) -> MirrorRecord:
    """Run Lock over ``source_path`` and write a cleaned mirror.

    Returns the ``MirrorRecord`` describing the written files. Side effects:
    creates ``<folder>/mirrors/lock/`` if missing, writes
    ``<basename>.cleaned.md`` + ``<basename>.spans.json``, and appends a
    ``system`` event to the folder's mutation log carrying
    ``extra.kind="mirror_lock"``.
    """
    folder_p = Path(folder).expanduser().resolve()
    source_p = Path(source_path).expanduser().resolve()
    if not source_p.exists():
        raise FileNotFoundError(f"source does not exist: {source_p}")

    body = source_p.read_text(encoding="utf-8", errors="replace")
    source_hash = _sha256_bytes(body.encode("utf-8"))

    cleaned, spans = _extract_spans_with_lock(body)

    out_dir = _lock_dir(folder_p)
    out_dir.mkdir(parents=True, exist_ok=True)
    basename = _safe_source_basename(source_p)
    mirror_path = out_dir / f"{basename}.cleaned.md"
    spans_path = _sidecar_for(mirror_path)

    mirror_path.write_text(cleaned, encoding="utf-8")
    sidecar = {
        "schema":      SPANS_SCHEMA,
        "source_path": str(source_p),
        "source_hash": source_hash,
        "mirror_kind": "lock",
        "created_at":  time.time(),
        "spans":       [s.to_dict() for s in spans],
    }
    spans_path.write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    log = MutationLog(folder_p, log_root=log_root)
    audit_id = log.append(LogEvent(
        event="system",
        folder_path=str(folder_p),
        pair_id=f"mirror:lock:{source_hash[:16]}",
        channel="system",
        actor=actor,
        lifecycle_state="live",
        extra={
            "kind":         "mirror_lock",
            "source_path":  str(source_p),
            "source_hash":  source_hash,
            "mirror_path":  str(mirror_path),
            "spans_path":   str(spans_path),
            "span_count":   len(spans),
        },
    ))

    return MirrorRecord(
        source_path=str(source_p),
        mirror_path=str(mirror_path),
        spans_path=str(spans_path),
        kind="lock",
        created_at=sidecar["created_at"],
        audit_id=audit_id,
        span_count=len(spans),
    )


def approve_lock_mirror(
    folder: str | Path,
    mirror_path: str | Path,
    approver: str,
    *,
    log_root: str | Path | None = None,
) -> MirrorRecord:
    """Promote a Lock mirror into the Oversight surface.

    Copies ``mirror_path`` into ``<folder>/mirrors/oversight/`` with the
    suffix ``.approved.md``, copies the spans sidecar, and appends a
    ``system`` audit event carrying ``extra.kind="mirror_oversight"``,
    ``extra.approver=<approver>``, ``extra.lock_mirror_path=<src>``.
    """
    folder_p = Path(folder).expanduser().resolve()
    src_p = Path(mirror_path).expanduser().resolve()
    if not src_p.exists():
        raise FileNotFoundError(f"mirror does not exist: {src_p}")
    if not approver:
        raise ValueError("approve_lock_mirror requires an approver identifier")

    src_spans = _sidecar_for(src_p)
    # Recover the basename + read the source_hash for the audit event.
    src_basename = src_p.name
    if src_basename.endswith(".cleaned.md"):
        stem = src_basename[: -len(".cleaned.md")]
    else:
        stem = src_p.stem

    out_dir = _oversight_dir(folder_p)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst_p = out_dir / f"{stem}.approved.md"
    dst_spans = _sidecar_for(dst_p)

    shutil.copy2(src_p, dst_p)
    if src_spans.exists():
        # Re-emit sidecar with the new mirror_kind label.
        existing = json.loads(src_spans.read_text(encoding="utf-8"))
        existing["mirror_kind"] = "oversight"
        existing["approved_at"] = time.time()
        existing["approver"] = approver
        dst_spans.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        span_count = len(existing.get("spans", []))
        source_path = existing.get("source_path", "")
        source_hash = existing.get("source_hash", "")
    else:
        span_count = 0
        source_path = ""
        source_hash = ""

    log = MutationLog(folder_p, log_root=log_root)
    audit_id = log.append(LogEvent(
        event="system",
        folder_path=str(folder_p),
        pair_id=f"mirror:oversight:{(source_hash or 'unknown')[:16]}",
        channel="system",
        actor=f"approver:{approver}",
        lifecycle_state="live",
        extra={
            "kind":               "mirror_oversight",
            "source_path":        source_path,
            "source_hash":        source_hash,
            "mirror_path":        str(dst_p),
            "spans_path":         str(dst_spans),
            "lock_mirror_path": str(src_p),
            "approver":           approver,
            "span_count":         span_count,
        },
    ))

    return MirrorRecord(
        source_path=source_path,
        mirror_path=str(dst_p),
        spans_path=str(dst_spans),
        kind="oversight",
        created_at=time.time(),
        audit_id=audit_id,
        span_count=span_count,
    )


def list_mirrors(folder: str | Path, *, kind: str = "") -> list[MirrorRecord]:
    """Return every mirror present on disk under ``<folder>/mirrors/``.

    ``kind`` filters to ``"lock"`` or ``"oversight"`` when set.
    Records are reconstructed from disk (and the sidecar JSON) so this
    works even on hosts that did not write the original audit event.
    """
    folder_p = Path(folder).expanduser().resolve()
    out: list[MirrorRecord] = []

    if kind in ("", "lock"):
        sdir = _lock_dir(folder_p)
        if sdir.exists():
            for mirror_path in sorted(sdir.glob("*.cleaned.md")):
                out.append(_record_from_disk(mirror_path, "lock"))

    if kind in ("", "oversight"):
        odir = _oversight_dir(folder_p)
        if odir.exists():
            for mirror_path in sorted(odir.glob("*.approved.md")):
                out.append(_record_from_disk(mirror_path, "oversight"))

    return out


def _record_from_disk(mirror_path: Path, kind: MirrorKind) -> MirrorRecord:
    spans_path = _sidecar_for(mirror_path)
    src_path = ""
    span_count = 0
    created_at = mirror_path.stat().st_mtime
    if spans_path.exists():
        try:
            sidecar = json.loads(spans_path.read_text(encoding="utf-8"))
            src_path = sidecar.get("source_path", "")
            span_count = len(sidecar.get("spans") or [])
            created_at = float(sidecar.get("created_at", created_at))
        except (OSError, json.JSONDecodeError):
            pass
    return MirrorRecord(
        source_path=src_path,
        mirror_path=str(mirror_path),
        spans_path=str(spans_path),
        kind=kind,
        created_at=created_at,
        audit_id="",   # not recoverable from disk
        span_count=span_count,
    )
