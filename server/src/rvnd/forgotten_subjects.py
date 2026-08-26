# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Forgotten-subjects ledger — refuse future re-ingestion of erased subjects.

After a B5 ``erasure.execute()`` sweep, the subject identifier(s) the user
requested erased are recorded here so that downstream ingest paths can
refuse silent re-ingestion (e.g. someone re-drops the same source document
into the inbox; an agent quotes the subject in a captured prompt; a web
search result mentions the subject).

Storage shape (one JSONL line per forgotten subject):

.. code-block:: json

    {
      "subject_hash": "<sha256(salt + subject)>",
      "salt": "<random per-folder salt>",
      "added_at": 1737000000.0,
      "request_id": "req:42"
    }

The salt is folder-scoped (one ``salt`` file per folder under
``<folder>/forgotten_subjects/`` containing a 32-byte hex value). The
plaintext subject is NEVER persisted — only its salted hash. This means
the ledger itself does not become a way to recover the very text we were
asked to forget.

The match operation tokenises the input text the same way the ledger
remembers candidate subjects (lowercase Unicode word boundaries, exact
match) and returns the list of forgotten-subject hashes that fire on it.
False negatives are accepted (paraphrase, transliteration); false
positives are not (every match has a deterministic provenance).

Status: beta, experimental, no legal advice, ongoing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


FORGOTTEN_DIR_NAME = "forgotten_subjects"
"""Subdir under each folder containing salt + forgotten.jsonl."""

_SALT_FILE = "salt"
_LEDGER_FILE = "forgotten.jsonl"
_LOCK_FILE = ".ledger.lock"

_TOKEN_RE = re.compile(r"\b\w+\b", re.UNICODE)


# ---------------------------------------------------------------------------
# Custom exception (used by ingest paths that integrate the guard)
# ---------------------------------------------------------------------------


class EraseGuardHit(Exception):
    """Raised by ingest paths when ``check_text`` returns a non-empty list.

    Attributes:
        hashes: list of forgotten-subject hashes that matched the input.
        folder: folder context the guard fired in.
    """

    def __init__(self, hashes: list[str], folder: str):
        self.hashes = list(hashes)
        self.folder = folder
        super().__init__(
            f"erase guard fired in {folder}: {len(hashes)} forgotten "
            f"subject(s) matched. Refusing to re-ingest."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _folder_dir(folder: str | Path) -> Path:
    return Path(folder).expanduser().resolve() / FORGOTTEN_DIR_NAME


def _salt_path(folder: str | Path) -> Path:
    return _folder_dir(folder) / _SALT_FILE


def _ledger_path(folder: str | Path) -> Path:
    return _folder_dir(folder) / _LEDGER_FILE


@contextmanager
def _ledger_lock(folder: str | Path) -> Iterator[None]:
    """Hold an OS-level exclusive lock for salt and ledger transactions."""
    lock_path = _folder_dir(folder) / _LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
            os.fsync(lock_file.fileno())
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows
            try:
                import msvcrt
            except ImportError as exc:  # pragma: no cover - exotic platform
                raise RuntimeError(
                    "no OS file-locking backend for forgotten-subject ledger"
                ) from exc
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_write_private(path: Path, text: str) -> None:
    """Atomically replace ``path`` with fsynced owner-only UTF-8 text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        if os.name != "nt":
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _read_or_create_salt(folder: str | Path) -> str:
    sp = _salt_path(folder)
    if sp.exists():
        salt = sp.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", salt):
            raise RuntimeError(f"invalid forgotten-subject salt at {sp}")
        return salt
    salt = secrets.token_hex(32)
    _atomic_write_private(sp, salt + "\n")
    return salt


def _ensure_salt(folder: str | Path) -> str:
    """Return the folder-scoped salt, creating it on first use.

    32 random bytes hex-encoded. Stored 0600 — the salt is not strictly
    secret (it sits next to the ledger that uses it) but tighter
    permissions reduce accidental copy-out.
    """
    with _ledger_lock(folder):
        return _read_or_create_salt(folder)


def salt_for(folder: str | Path) -> str:
    """Public accessor for the folder's forgotten-subject salt.

    Same value :func:`ensure`/:func:`add` use to hash a subject, exposed so
    a caller that already durably registered a subject via :func:`ensure`
    (e.g. ``erasure.execute``, or ``pending_erase.arm_marker`` arming a
    marker for a sealed descendant) can embed the exact ``{salt,
    subject_hash}`` pair elsewhere without re-deriving or duplicating this
    module's hashing logic. Creates the salt on first use, same as
    :func:`ensure` does internally — idempotent, never rotates an existing
    salt.
    """
    return _ensure_salt(folder)


def _hash_subject(salt: str, subject: str) -> str:
    """Salted SHA-256 of the lowercase-normalised subject string."""
    normal = subject.strip().lower()
    return hashlib.sha256(
        (salt + "\x1f" + normal).encode("utf-8")
    ).hexdigest()


def opaque_ref(folder: str | Path, text: str, *, domain: str) -> str:
    """Deterministic folder-salted ref for ``text``, for on-chain use.

    ``sha256(domain \\x1f salt \\x1f text)`` hex, under the same per-folder
    salt as the ledger. The chain is permanent, so anything referencing a
    subject-bearing string (card ids, purged pair ids) goes through this
    instead of the string itself. ``domain`` separates use sites: the same
    text yields unrelated refs per domain, so a card pair ref can never be
    equality-linked to this ledger's hash of the erased subject. Unlike
    :func:`_hash_subject` the text is NOT normalised — refs stand for exact
    identifiers, not fuzzy subject text. Callers truncate to taste.
    """
    salt = _ensure_salt(folder)
    return hashlib.sha256(
        (domain + "\x1f" + salt + "\x1f" + text).encode("utf-8")
    ).hexdigest()


def purged_pair_ref(folder: str | Path, pair_id: str) -> str:
    """The on-chain stand-in for a purged pair id.

    Purge tombstones, the erasure tracker and the composite all outlive the
    events they describe, so a subject-bearing pair id written there verbatim
    would survive its own erasure. All three write this ref instead — the
    same string on every side, so ``erasure.status`` stitches them by plain
    equality without recomputing anything.
    """
    return "pair-ref:" + opaque_ref(folder, pair_id, domain="pair-ref")[:16]


def _read_ledger(folder: str | Path) -> list[dict[str, Any]]:
    lp = _ledger_path(folder)
    if not lp.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in lp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return rows


def _read_ledger_strict(folder: str | Path) -> list[dict[str, Any]]:
    lp = _ledger_path(folder)
    if not lp.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        lp.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"corrupt forgotten-subject ledger at line {line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise RuntimeError(
                f"invalid forgotten-subject ledger row at line {line_number}"
            )
        if not row.get("subject_hash") or not row.get("salt"):
            raise RuntimeError(
                f"incomplete forgotten-subject ledger row at line {line_number}"
            )
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ensure(
    folder: str | Path, subject: str, request_id: str
) -> tuple[str, bool]:
    """Durably ensure a subject is guarded; return ``(hash, added)``."""
    if not subject or not subject.strip():
        raise ValueError("subject must be non-empty")
    with _ledger_lock(folder):
        canonical_salt = _read_or_create_salt(folder)
        rows = _read_ledger_strict(folder)
        for row in rows:
            row_salt = str(row["salt"])
            if _hash_subject(row_salt, subject) == row["subject_hash"]:
                return str(row["subject_hash"]), False

        h = _hash_subject(canonical_salt, subject)
        rows.append({
            "subject_hash": h,
            "salt": canonical_salt,
            "added_at": time.time(),
            "request_id": str(request_id or ""),
        })
        payload = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows
        )
        _atomic_write_private(_ledger_path(folder), payload)
        return h, True


def add(folder: str | Path, subject: str, request_id: str) -> str:
    """Append a forgotten subject row and return its salted hash.

    This preserves the historical duplicate-appending API. Erasure uses
    :func:`ensure` for transactional idempotency.
    """
    if not subject or not subject.strip():
        raise ValueError("subject must be non-empty")
    with _ledger_lock(folder):
        salt = _read_or_create_salt(folder)
        rows = _read_ledger_strict(folder)
        subject_hash = _hash_subject(salt, subject)
        rows.append({
            "subject_hash": subject_hash,
            "salt": salt,
            "added_at": time.time(),
            "request_id": str(request_id or ""),
        })
        payload = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows
        )
        _atomic_write_private(_ledger_path(folder), payload)
        return subject_hash


def contains(folder: str | Path, subject: str) -> bool:
    """True iff this EXACT subject is already in the folder's ledger.

    Exact salted-hash membership, unlike :func:`check`, which does token /
    substring matching for the ingest guard. Used to make erasure ``execute``
    replay-safe: a second execute of an already-forgotten subject must not
    append a duplicate ledger row or composite tombstone.
    """
    if not subject or not subject.strip():
        return False
    rows = _read_ledger_strict(folder)
    if not rows:
        return False
    return any(
        row.get("subject_hash")
        and row.get("salt")
        and _hash_subject(str(row["salt"]), subject) == row["subject_hash"]
        for row in rows
    )


def list_subjects(folder: str | Path) -> list[dict[str, Any]]:
    """Return the deduplicated list of forgotten subject records.

    Each row: ``{subject_hash, added_at, request_id}`` (salt omitted from
    the public view — it lives on disk).
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in _read_ledger(folder):
        h = row.get("subject_hash", "")
        if not h or h in seen:
            continue
        seen.add(h)
        out.append({
            "subject_hash": h,
            "added_at":     row.get("added_at", 0.0),
            "request_id":   row.get("request_id", ""),
        })
    return out


def _candidate_strings(text: str) -> list[str]:
    """Candidate substrings tested against a forgotten-subject hash.

    Shared by :func:`check` (the live ingest guard) and
    ``pending_erase._select_matching_pairs`` (the unseal-time matcher) so
    the two paths can never quietly diverge in what counts as a match.

    Tokenisation: lowercase Unicode word boundaries. Candidates considered:

      1) every whitespace-collapsed token (single-word subjects);
      2) the full lowercased text, trimmed and whitespace-collapsed
         (catches phrase subjects where the input is exactly the subject —
         common when a tool tries to re-ingest the same source, or when a
         restored chain event's haystack IS the subject verbatim).

    This is TOKEN / FULL-TEXT match, never substring — a subject embedded
    mid-sentence or mid-word (e.g. "...contact JaneDoeCase123 for...") is
    not recalled. Documented trade-off, not a hidden gap: false negatives
    (paraphrase, embedding) are accepted; false positives are not (every
    match has a deterministic, re-derivable provenance).
    """
    if not text:
        return []
    lower = text.strip().lower()
    tokens = {tok for tok in _TOKEN_RE.findall(lower)}
    full_norm = " ".join(lower.split())
    candidates = list(tokens)
    if full_norm:
        candidates.append(full_norm)
    return candidates


def check(folder: str | Path, text: str) -> list[str]:
    """Return the list of forgotten-subject hashes that appear in ``text``.

    See :func:`_candidate_strings` for the exact match rule (token /
    full-text, not substring). Returns empty list when the ledger is empty
    or no matches fire.
    """
    if not text:
        return []
    rows = _read_ledger_strict(folder)
    if not rows:
        return []

    candidate_strings = _candidate_strings(text)

    hits: list[str] = []
    seen: set[str] = set()
    # We can't reverse a salted hash. Instead, for each row we re-hash
    # candidate tokens / phrases against THAT row's salt and check the
    # row's recorded hash. This is O(rows × candidate_substrings) — fine
    # for small ledgers; the typical folder has a handful of forgotten
    # subjects, not thousands.
    for row in rows:
        h_expected = row.get("subject_hash", "")
        salt = row.get("salt", "")
        if not h_expected or not salt or h_expected in seen:
            continue
        for cand in candidate_strings:
            if not cand:
                continue
            if _hash_subject(salt, cand) == h_expected:
                hits.append(h_expected)
                seen.add(h_expected)
                break
    return hits


def check_text(folder: str | Path, text: str) -> list[str]:
    """Alias of :func:`check`. Exists so the public, ingest-path-facing
    name is stable across refactors.
    """
    return check(folder, text)
