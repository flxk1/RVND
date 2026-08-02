# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Inbox watcher for folder document ingestion.

Watches each folder's ``Inbox/`` subdirectory for new files. On detection,
hashes the file, dispatches the configured extractor, and writes the
resulting pair(s) into the folder's L0 memory. Idempotent on file-hash:
re-ingesting the same bytes produces the same ``pair_id`` and reads as a
single live pair.

This is the felt "L0 is working" moment. Drop a contract PDF into
``/companies/acme/Legal/Inbox/`` and the extracted pair appears in the
folder's KG, visible to ``/companies/acme/Legal/`` and ``/companies/acme/``
(per the asymmetric rule) but invisible to ``/companies/acme/HR/``.

Current contract:
  - ``InboxWatcher`` class (polling — no watchdog dependency).
  - ``Extractor`` protocol with a ``default_extractor`` implementation that
    captures filename + size + hash + content preview as one pair.
  - Idempotency via file-content SHA-256.
  - Per-pair source_document set to the absolute file path so cascade-delete
    and cascade-purge work.

The default extractor can be swapped for the ``workspace-doc-extractor``
classifier + facet extractors → ND dispatch route without changing the watcher's
contract.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

from . import forgotten_subjects as _fs
from .memory import WorkspaceMemory
from .mutation_log import LogEvent

_log = logging.getLogger(__name__)


# Platform-independent MIME floor. macOS has no /etc/mime.types and
# Python < 3.13's builtin table lacks text/markdown, so .md ingested as
# "(binary file …)" metadata stubs — which silently blinded the
# erase-guard (root-caused via scripts/diag_guard.sh, 2026-06-12: Linux
# and Python 3.14 passed, Mac 3.12 venv failed, same code). Formats the
# product treats as text are registered here, deterministically, so
# extraction never depends on the host OS's MIME tables.
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/markdown", ".markdown")

INBOX_SUBDIR = "Inbox"
"""Optional subdirectory where new files are dropped. When absent, the workspace root is scanned directly."""

MAX_CONTENT_PREVIEW_BYTES = 4096
"""Bytes read from the file head as the solution body when no real extractor is configured."""

_SCAN_BUDGET = 20_000_000
"""Quarantine scan budget (bytes). The FULL body is scanned up to this size; a larger (or
unreadable) file is HELD unscanned — default-deny, never admit-by-truncation."""


# Files at the workspace root that should NOT be ingested as documents.
# These are project metadata / configuration files, not content to remember.
ROOT_SKIP_FILES = {
    "README.md", "MANIFEST.md", "LICENSE", "LICENSE.md", "LICENSE.txt",
    "CHANGELOG.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md",
    "pyproject.toml", "setup.py", "setup.cfg", "Makefile",
    "package.json", "package-lock.json", "yarn.lock",
    "requirements.txt", "Pipfile", "Pipfile.lock", "poetry.lock",
    "tsconfig.json", ".gitignore", ".gitattributes",
}

# Directories that should be skipped when walking recursively. Hidden dirs
# (starting with ".") are skipped by default; this list catches the common
# project-tooling directories regardless of hidden status.
RECURSIVE_SKIP_DIRS = {
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    "build", "dist", "target", ".venv", "venv", ".git", ".idea",
    ".vscode", ".tox", ".eggs",
}


# ---------------------------------------------------------------------------
# Extractor protocol
# ---------------------------------------------------------------------------


@dataclass
class ExtractedFile:
    """Metadata about an ingested file. Returned by Extractor.extract()."""

    file_path: str
    file_size: int
    file_hash: str
    mime_type: str
    content_preview: str
    pairs: list[dict]


class Extractor(Protocol):
    """Contract every extractor implements.

    ``extract(file_path, folder_context)`` returns an ExtractedFile carrying
    one or more pair dicts ready to hand to ``WorkspaceMemory.remember()``.

    The default extractor captures a single pair per file. The
    workspace-doc-extractor can return many — one per facet × per
    Problem/Solution pair the document yields.
    """

    def extract(self, file_path: str, folder_context: str) -> ExtractedFile:
        ...


def _hash_file(path: Path, *, chunk_size: int = 65536) -> str:
    """SHA-256 of the file contents. 32-char prefix for keys."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return "sha256:" + h.hexdigest()[:32]


def _read_preview(path: Path, max_bytes: int = MAX_CONTENT_PREVIEW_BYTES) -> str:
    """Read up to ``max_bytes`` from the file head. Decode as UTF-8 best-effort."""
    try:
        with path.open("rb") as fh:
            raw = fh.read(max_bytes)
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


class DefaultExtractor:
    """Minimum-viable extractor: one pair per file, captures metadata only.

    Used until workspace-doc-extractor is wired in. Every file becomes a single
    Problem/Solution pair whose problem describes the ingest and whose
    solution carries the content preview. Idempotent on file hash.
    """

    extractor_id = "default-extractor"
    extractor_version = "0.1.0"

    def extract(self, file_path: str, folder_context: str) -> ExtractedFile:
        p = Path(file_path)
        size = p.stat().st_size
        h = _hash_file(p)
        mime, _ = mimetypes.guess_type(str(p))
        mime = mime or "application/octet-stream"
        preview = _read_preview(p) if mime.startswith("text/") or mime in (
            "application/json", "application/xml",
        ) else f"(binary file, {size} bytes, mime={mime})"

        pair_id = h     # stable across re-ingests
        problem_id = f"sha256:problem-{h[7:]}"   # derived from file hash

        pair: dict = {
            "id": pair_id,
            "problem": {
                "id": problem_id,
                "scope": "inbox",
                "type": "document_ingest",
                "summary": p.name,
                "facets": {
                    "filename": p.name,
                    "size_bytes": size,
                    "mime_type": mime,
                    "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                "source_document": str(p.resolve()),
            },
            "solution": {
                "id": pair_id,
                "problem_id": problem_id,
                "body": preview,
                "body_format": "prose" if mime.startswith("text/") else "metadata",
                "authority_tier": 3,    # primary document but no validator yet
                "confidence": 1.0,      # the file IS what it is
                "cited_sources": [str(p.resolve())],
                "extractor_chain": [self.extractor_id],
                "extractor_version": self.extractor_version,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
        return ExtractedFile(
            file_path=str(p.resolve()),
            file_size=size,
            file_hash=h,
            mime_type=mime,
            content_preview=preview,
            pairs=[pair],
        )


# ---------------------------------------------------------------------------
# Inbox watcher
# ---------------------------------------------------------------------------


class InboxWatcher:
    """Polls one folder's Inbox/ for new files.

    Lifecycle:

    .. code-block:: python

        watcher = InboxWatcher("/companies/acme/HR/", log_root=...)
        watcher.run_once()           # one-shot scan; returns pair_ids ingested
        watcher.run_forever()        # loop with poll_interval; Ctrl+C to stop

    Idempotency: a file already ingested (by SHA-256 hash) is skipped. The
    mutation log already deduplicates at the pair_id level — but the watcher
    short-circuits to avoid re-hashing every file on every scan.

    Stable across crashes: the watcher reads the WorkspaceMemory state on startup
    to learn what's already been ingested. No separate "seen-files" file.
    """

    def __init__(
        self,
        folder_context: str | Path,
        *,
        log_root: str | Path | None = None,
        extractor: Extractor | None = None,
        actor: str = "agent:inbox-watcher",
        inbox_subdir: str = INBOX_SUBDIR,
    ):
        self.folder_context = str(Path(folder_context).expanduser().resolve())
        self._log_root = log_root
        self._extractor = extractor or DefaultExtractor()
        self._actor = actor
        self._inbox_subdir = inbox_subdir
        self._inbox_path = Path(self.folder_context) / inbox_subdir
        self._memory = WorkspaceMemory(
            self.folder_context,
            log_root=self._log_root,
            actor=self._actor,
        )

    @property
    def inbox_path(self) -> Path:
        return self._inbox_path

    @property
    def scan_path(self) -> Path:
        """Where this watcher scans for files.

        - If a folder named ``Inbox/`` exists inside the workspace, scan that.
          (Back-compat with workspaces that organise drops into Inbox/.)
        - Otherwise, scan the workspace root, skipping project metadata files
          (READMEs, manifests, lockfiles, hidden files).
        """
        if self._inbox_path.is_dir():
            return self._inbox_path
        return Path(self.folder_context)

    @property
    def uses_inbox(self) -> bool:
        """True when scanning Inbox/; False when scanning the workspace root."""
        return self._inbox_path.is_dir()

    def _already_ingested_hashes(self) -> set[str]:
        """Return the set of file hashes the watcher's folder has already ingested."""
        seen: set[str] = set()
        for p in self._memory.all_pairs():
            problem = p.get("problem", {})
            if isinstance(problem, dict) and problem.get("source_document"):
                # The default extractor uses the file hash as the pair_id.
                pid = p.get("id", "")
                if pid:
                    seen.add(pid)
        return seen

    def _enumerate_inbox(self) -> Iterable[Path]:
        """Yield file paths to ingest from this workspace's scan_path.

        Scan mode:
        - Inbox/ exists inside the workspace → scan its contents (back-compat).
        - Else → scan the workspace root, skipping project-metadata files.

        Does NOT recurse into sub-folders. Each sub-folder is its own
        workspace per the asymmetric hierarchical rule; to ingest all
        sub-folders, the CLI iterates separately per sub-folder.
        """
        scan_root = self.scan_path
        if not scan_root.exists():
            return
        for child in sorted(scan_root.iterdir()):
            if child.name.startswith("."):
                continue
            if not child.is_file():
                continue
            # In workspace-root mode, skip project metadata files.
            if not self.uses_inbox and child.name in ROOT_SKIP_FILES:
                continue
            yield child

    def run_once(self) -> list[str]:
        """One-shot scan. Returns the list of pair_ids newly ingested.

        Already-ingested files (matched by file hash) are skipped.

        Scans either ``<folder>/Inbox/`` (if it exists) or the workspace
        root directly. Workspace root scan skips project metadata files
        (README.md, manifests, lockfiles) and hidden files.
        """
        # If neither Inbox/ nor workspace folder exists, nothing to do.
        if not Path(self.folder_context).exists():
            return []

        already = self._already_ingested_hashes()
        new_pair_ids: list[str] = []

        for path in self._enumerate_inbox():
            try:
                extracted = self._extractor.extract(str(path), self.folder_context)
            except OSError:
                continue  # file may have been removed mid-scan

            if extracted.file_hash in already:
                continue  # already ingested

            for pair in extracted.pairs:
                pid = self._memory.remember(
                    pair,
                    channel="document",
                    source_hash=extracted.file_hash,
                )
                new_pair_ids.append(pid)

            already.add(extracted.file_hash)

        return new_pair_ids

    def run_forever(
        self,
        *,
        poll_interval: float = 2.0,
        on_ingested: Callable[[list[str]], None] | None = None,
    ) -> None:
        """Loop forever, polling every ``poll_interval`` seconds.

        ``on_ingested`` (if given) is called after each scan with the list
        of newly-ingested pair_ids. Use it to wire stdout reporting or to
        trigger downstream processing (B2's ND-dispatch).
        """
        while True:
            try:
                new_ids = self.run_once()
            except KeyboardInterrupt:
                return
            if new_ids and on_ingested is not None:
                try:
                    on_ingested(new_ids)
                except Exception:  # pragma: no cover
                    pass
            try:
                time.sleep(poll_interval)
            except KeyboardInterrupt:
                return


def ingest_file(
    file_path: str | Path,
    folder_context: str | Path,
    *,
    log_root: str | Path | None = None,
    extractor: Extractor | None = None,
    actor: str = "user",
) -> list[str]:
    """One-off file ingest. Used by ``workspace-l0 ingest <file>``.

    Returns the pair_ids written to the folder's memory. Idempotent: re-ingesting
    the same file is a no-op (returns []) unless ``extractor`` produces a
    different hash.

    B5 erase-guard (0.6.8): every extracted pair's serialised text is
    checked against the folder's ``forgotten_subjects`` ledger before any
    pair is written. If any pair contains a forgotten subject, ingest
    refuses with ``EraseGuardHit`` and a refusal audit event is appended
    so the operator can see the attempted re-ingest.
    """
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"not a file: {file_path}")

    mem = WorkspaceMemory(folder_context, log_root=log_root, actor=actor)
    ext = extractor or DefaultExtractor()
    extracted = ext.extract(str(p), str(Path(folder_context).expanduser().resolve()))

    # Idempotency check.
    if extracted.file_hash in {pair.get("id", "") for pair in mem.all_pairs()}:
        return []

    # B5 erase-guard. Check the union of all extracted-pair text against
    # the forgotten_subjects ledger. The guard is core privacy enforcement:
    # it must never be skippable by a silent import failure (the module is
    # imported at the top of this file — an unavailable guard fails the
    # whole ingest path loudly, by design).
    haystack_parts: list[str] = []
    for pair in extracted.pairs:
        problem = pair.get("problem", {}) or {}
        if isinstance(problem, dict):
            haystack_parts.append(str(problem.get("summary", "")))
        solution = pair.get("solution", {}) or {}
        if isinstance(solution, dict):
            haystack_parts.append(str(solution.get("body", "")))
    haystack = "\n".join(haystack_parts)
    if haystack:
        hits = _fs.check_text(folder_context, haystack)
        if hits:
            # Audit the refusal so the chain shows the attempt. A failed
            # audit append must not be silent: the refusal would lose its
            # evidence (and the suite's non-leak stress test rightly
            # treats a missing EraseGuardHit event as a failure).
            try:
                mem._own_log.append(LogEvent(
                    event="system",
                    folder_path=str(Path(folder_context).expanduser().resolve()),
                    pair_id=f"erase-guard:hit:{extracted.file_hash[:16]}",
                    channel="system",
                    actor=actor,
                    extra={
                        "kind":             "EraseGuardHit",
                        "file_path":        str(p),
                        "matched_hashes":   list(hits),
                        "file_hash":        extracted.file_hash,
                    },
                ))
            except Exception as audit_err:
                _log.error(
                    "erase-guard refusal on %s could NOT be audited (%s); "
                    "the refusal still applies but the chain is missing "
                    "the EraseGuardHit event", p, audit_err,
                )
            raise _fs.EraseGuardHit(
                hits, str(Path(folder_context).expanduser().resolve()),
            )

    # Ingest-cyber quarantine (default-deny). Scan the extracted body + the raw file bytes for
    # prompt-injection and file-shape threats BEFORE any pair enters memory. reject = malware /
    # executable (refused); hold = high-confidence injection / active-content (quarantined for a
    # human to release). admit passes — but "admit" means the TRIPWIRE matched no known pattern,
    # not that the input is proven safe; the agent ACTING on the content is still gated by the
    # oversight / action-gate layer. Imported un-guarded (like the erase-guard): a security gate
    # must fail loudly, never be skipped by a silent import error.
    from . import ingest_quarantine as _iq
    # FULL-body scan within a budget — truncating the scan window (an earlier 200 KB cap) let a
    # threat hide behind padding and get ADMITTED. Default-deny instead: a file larger than the
    # budget, or one whose bytes cannot be read, is HELD unscanned for a human — never
    # admit-by-truncation.
    try:
        _size = p.stat().st_size
        _raw = p.read_bytes() if _size <= _SCAN_BUDGET else b""
    except Exception:
        _size, _raw = -1, b""
    if _size > _SCAN_BUDGET or _size < 0:
        _verdict = _iq.Verdict(
            _iq.Admission.HOLD.value,
            [{"kind": "unscanned", "label": "exceeds_scan_budget", "severity": "high",
              "detail": f"size {_size} exceeds the {_SCAN_BUDGET}-byte scan budget"
                        if _size >= 0 else "file bytes unreadable at scan time",
              "confidence": 1.0, "standard": "file-shape"}],
            "held unscanned (default-deny): body exceeds the scan budget or is unreadable")
    else:
        # scan the extractor's readable text AND the raw body (an injection may sit in the file
        # even if the extractor kept only a summary); file-shape checks read the bytes.
        _scan_text = "\n".join(x for x in (haystack, _raw.decode("utf-8", "ignore")) if x) or None
        _verdict = _iq.scan(text=_scan_text, data=_raw or None, filename=p.name)
    if _verdict.quarantined:
        try:
            mem._own_log.append(LogEvent(
                event="system",
                folder_path=str(Path(folder_context).expanduser().resolve()),
                pair_id=f"quarantine:{_verdict.admission}:{extracted.file_hash[:16]}",
                channel="system", actor=actor,
                extra={"kind": "IngestQuarantine", "admission": _verdict.admission,
                       "file_path": str(p), "file_hash": extracted.file_hash,
                       "reason": _verdict.reason, "threats": _verdict.threats}))
        except Exception as audit_err:
            _log.error("ingest quarantine (%s) on %s could NOT be audited (%s); the refusal "
                       "still applies but the chain is missing the IngestQuarantine event",
                       _verdict.admission, p, audit_err)
        raise _iq.QuarantineRefused(_verdict)

    pair_ids: list[str] = []
    for pair in extracted.pairs:
        pid = mem.remember(pair, channel="document", source_hash=extracted.file_hash)
        pair_ids.append(pid)
    return pair_ids
