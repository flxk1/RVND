# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Per-folder append-only mutation log.

Every state-changing event in Workspace L0 writes one JSONL line to the log
directory of the folder it was scoped to. Folder views are computed at read
time by replaying the log and applying the most-recent-state-wins rule per
``pair_id``.

Schema (append-only memory + lifecycle events):

.. code-block:: json

    {
      "ts": 1716230400.123,
      "event": "ingest" | "extract" | "admit" | "live" | "supersede" | "stale" | "delete" | "purge",
      "channel": "document" | "websearch" | "llm_answer" | "system",
      "folder_path": "/companies/acme/HR/onboarding/",
      "pair_id": "sha256:...",
      "problem_id": "sha256:...",
      "source_hash": "sha256:...",
      "lifecycle_state": "ingested" | "classified" | ... | "deleted",
      "actor": "user" | "agent:<skill-id>",
      "audit_id": "uuid",
      "extra": { ... }     // optional, opaque per-event payload
    }

Invariants:

- Files are append-only. The mutation log is the source of truth.
- One log directory per folder; folder identity is a SHA-256 of the absolute
  folder path. A folder-local marker file (``.workspace-folder-id`` containing
  a UUID; the legacy ``.workspaceversum-folder-id`` name is still accepted)
  keys the log instead when present, so moves don't re-key it.
- Malformed lines are skipped on replay; the constructor does not raise on a
  corrupt log. The log must tolerate partial-write recovery.
- ``audit_id`` is unique per event (UUID v4). Replaying the same event twice
  creates two entries with different ``audit_id``; the state transition is
  idempotent at the read-aggregation layer, not at the append layer.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from ._storage_paths import LOG_ROOT_DEFAULT


_log = logging.getLogger(__name__)


class DiskFullError(OSError):
    """Raised when an append/rewrite to the mutation log hits ENOSPC.

    Subclass of OSError so existing OSError-handling callers still see it,
    but distinct enough that the caller can react to disk-full specifically
    (e.g. surface to the user, refuse the workflow, fall back to a different
    log root). The mutation log truncates the partial write before raising
    so the on-disk chain stays well-formed.
    """


GENESIS_HASH = "GENESIS"
"""Sentinel prev_hash for the first event in a new log (post-0.6.5)."""


# ---------------------------------------------------------------------------
# B1 (0.6.8): purge tombstone — controlled erasure that preserves the chain
# ---------------------------------------------------------------------------

# GDPR Art. 17(1) erasure grounds. Required argument to ``purge()``; the
# tombstone records which ground was invoked so the audit trail explains
# why the data is gone.
VALID_LEGAL_BASES = frozenset({
    "art_17_1_a",   # no longer necessary
    "art_17_1_b",   # consent withdrawn
    "art_17_1_c",   # data subject objects + no overriding ground
    "art_17_1_d",   # unlawful processing
    "art_17_1_e",   # legal obligation
    "art_17_1_f",   # child-data collected under services-to-children offer
})


# ---------------------------------------------------------------------------
# Cross-process file lock helpers (B1 / 0.6.8)
# ---------------------------------------------------------------------------


_IS_WINDOWS = os.name == "nt"


def _file_lock_backend():
    """Return the platform OS-lock module or refuse to run unlocked."""
    if _IS_WINDOWS:
        try:
            import msvcrt
        except ImportError as exc:  # pragma: no cover - broken Windows runtime
            raise RuntimeError(
                "Windows mutation-log locking requires msvcrt; refusing "
                "to access the log without an OS lock"
            ) from exc
        return "msvcrt", msvcrt

    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - unsupported Unix runtime
        raise RuntimeError(
            "mutation-log locking requires fcntl on this platform; refusing "
            "to access the log without an OS lock"
        ) from exc
    return "fcntl", fcntl


@contextlib.contextmanager
def _file_lock(fh, *, exclusive: bool):
    """Hold an advisory lock around a region of code.

    Uses ``fcntl.flock`` on POSIX (Linux + macOS). On Windows falls back to
    ``msvcrt.locking`` over the first byte of the file — coarse but correct
    for our purposes (we just need mutual exclusion across processes).

    Lock acquisition and release fail closed.  Mutation-log integrity depends
    on serialising read-tail/write and rewrite regions, so an unavailable or
    failed platform lock is an operation failure, never permission to proceed
    unlocked.
    """
    backend, lock_module = _file_lock_backend()
    locked = False
    try:
        if backend == "fcntl":
            mode = lock_module.LOCK_EX if exclusive else lock_module.LOCK_SH
            lock_module.flock(fh.fileno(), mode)
            locked = True
        else:
            # msvcrt.locking is exclusive only; SH degrades to EX on win.
            fh.seek(0)
            lock_module.locking(fh.fileno(), lock_module.LK_LOCK, 1)
            locked = True
        yield
    finally:
        if locked and backend == "fcntl":
            lock_module.flock(fh.fileno(), lock_module.LOCK_UN)
        elif locked:
            fh.seek(0)
            lock_module.locking(fh.fileno(), lock_module.LK_UNLCK, 1)


def _canonical_event_hash(event_dict: dict) -> str:
    """SHA-256 of the canonical JSON of an event, EXCLUDING ``prev_hash`` and ``signature``.

    Used by the hash chain (post-0.6.5 tamper-evidence) and the Ed25519
    signature (post-0.6.6). Both fields are derived from content; including
    them in the hash would make it circular.

    Determinism: ``sort_keys=True`` + compact separators + ``ensure_ascii=False``.
    Same content → same hash across machines and Python versions.
    """
    d = {k: v for k, v in event_dict.items() if k not in ("prev_hash", "signature")}
    canonical = json.dumps(
        d, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _signed_bytes(event_dict: dict) -> bytes:
    """The bytes that Ed25519 signs: canonical content + prev_hash.

    Including prev_hash in the signed payload binds each event's signature to
    its chain position, not just its content. An adversary can't lift a valid
    signature from one event and replay it elsewhere in the log.
    """
    canonical_hash = _canonical_event_hash(event_dict)
    prev_hash = event_dict.get("prev_hash", "")
    return f"{canonical_hash}|{prev_hash}".encode("utf-8")


@dataclass
class ChainVerificationResult:
    """Outcome of ``MutationLog.verify_chain()``.

    - ``ok``: True iff every linked event's stored ``prev_hash`` matches the
      canonical hash of its predecessor and every signed event's signature
      verifies against the active public key.
    - ``total_events``: count of well-formed events walked.
    - ``legacy_events``: events with no ``prev_hash`` field — accepted (they
      pre-date the 0.6.5 chain) but not validated.
    - ``unsigned_events``: events with no ``signature`` field — accepted (they
      pre-date the 0.6.6 Ed25519 layer) but not validated.
    - ``broken_links``: list of hash-chain mismatches. Each: position, audit_id,
      expected, found, reason.
    - ``signature_failures``: list of signature verification failures. Each:
      position, audit_id, reason.
    - ``malformed_lines``: count of lines that failed JSON parse.
    """

    ok: bool
    total_events: int
    legacy_events: int
    broken_links: list[dict]
    malformed_lines: int
    unsigned_events: int = 0
    signature_failures: list[dict] = field(default_factory=list)
    # B1 (0.6.8): count of chain-link transitions explained by a purge
    # tombstone. These are not broken_links — the rewrite was authorised,
    # signed by the controller, and recorded on-chain. Surfaced separately
    # so ``workspaces status`` can distinguish "controlled erasure happened"
    # from "the chain was tampered with".
    purged_with_tombstone: int = 0
    # A2 (cross-host divergence): consecutive same-folder events whose
    # ``host_id`` changes without a ``key_rotation`` marker. Ed25519 + the hash
    # chain prove an event was signed with the registered key in chain order,
    # but an attacker who steals the identity key can re-sign a rewritten chain
    # on a DIFFERENT host; the only residual signal is that the host_id shifts
    # mid-chain. Each entry: {position, audit_id, prev_host_id, host_id}.
    # Populated by verify_chain; advisory by default (does not set ok=False),
    # because a deliberate host move that skipped the key_rotation marker is
    # indistinguishable from theft. WORKSPACE_STRICT_HOST_DIVERGENCE=1 upgrades
    # it to a hard verification failure — for single-host deployments where any
    # host shift IS an incident; emit key_rotation markers on deliberate moves.
    host_divergence_warning: list[dict] = field(default_factory=list)
    # Genesis key pin (opt-in). None on a chain with no key_registration event
    # (legacy / unpinned). When present: {"registered": True, "fingerprint":
    # <hex>, "pin_file": "match" | "mismatch" | "absent"}. A mismatch or a
    # re-keyed rewrite lands in ``signature_failures`` and fails the chain.
    key_pin: "dict | None" = None

    def __bool__(self) -> bool:
        return self.ok


#: Opt-in: new chains record their signing identity in a genesis
#: key_registration event, and verify_chain enforces the pin. Enforcement of an
#: already-registered chain is NOT gated on this — an attacker cannot downgrade
#: it by unsetting the var; the var only governs whether NEW chains register.
KEY_PINNING_ENV = "WORKSPACE_KEY_PINNING"
#: Opt-in: an UNREGISTERED chain fails verification. For deployments that want
#: the pin enforced as a floor from day one rather than adopted lazily.
STRICT_KEY_PINNING_ENV = "WORKSPACE_STRICT_KEY_PINNING"
#: Relocate the TOFU pin file off the log tree (e.g. a read-only mount) so a
#: filesystem adversary who rewrites the log cannot also rewrite the pin. The
#: pin's guarantee is only as strong as this location's write protection.
KEY_PIN_DIR_ENV = "WORKSPACE_KEY_PIN_DIR"

VALID_EVENTS = frozenset({
    "ingest",
    "classify",
    "extract",
    "admit",
    "hold",
    "reject",
    "live",
    "supersede",
    "stale",
    "delete",
    "purge",
    "system",
    # Validator-before-commit and air-gap refusal patterns are first-class
    # events, not `system`-wrapped records.
    "validator_rejected",
    "air_gap_refused",
    # Deliberate signing-identity move across hosts. verify_chain reads this
    # marker to distinguish an authorised host change from a key-theft rewrite
    # (see host_divergence_warning); it must be constructible as a first-class
    # event, not only as a system-wrapped extra.kind.
    "key_rotation",
    # Genesis pin: the first event of a pinned chain records the identity key
    # the chain is signed with (fingerprint + PEM), so verify_chain can detect
    # a re-keyed rewrite. See WORKSPACE_KEY_PINNING and _register_identity.
    "key_registration",
})


VALID_CHANNELS = frozenset({
    "document",
    "websearch",
    "llm_answer",
    "system",
    "reasoning",   # derived inferences composed over the 5D edge graph
    "fact",        # a triple asserted via workspace_remember
})


def _filesystem_is_case_insensitive(path: Path) -> bool:
    """Best-effort detection: does the filesystem at this path treat
    'Foo' and 'foo' as the same entry?

    macOS APFS (default) and Windows NTFS are case-insensitive.
    Linux ext4/xfs are case-sensitive. On detection failure defaults to
    True (over-normalising is safer than under-normalising).
    """
    try:
        import os
        if not path.exists():
            path = path.parent if path.parent.exists() else Path.home()
        s = os.path.dirname(str(path))
        n = os.path.basename(str(path))
        if not n or not s:
            return True
        flipped = n.swapcase()
        if flipped == n:
            return True   # no alphabetic chars to probe with
        try:
            orig_stat = path.stat()
            flipped_path = Path(s) / flipped
            flipped_stat = flipped_path.stat()
            return orig_stat.st_ino == flipped_stat.st_ino
        except (OSError, FileNotFoundError):
            return False
    except Exception:
        return True


def folder_hash(folder_path: str | Path) -> str:
    """Stable identifier for a folder.

    Phase 2 (post-#162): on case-insensitive filesystems (APFS, NTFS),
    the resolved absolute path is lower-cased before hashing. This way
    "/Users/x/Workspaces" and "/Users/x/workspaces" — same physical folder
    on macOS — produce the same hash. Workspace identity follows the
    inode, not the path-string case.

    B6.3 (0.6.8): symlink resolution honours :envvar:`WORKSPACE_SYMLINK_MODE`.
    Default ``follow`` keeps pre-0.6.8 behaviour (symlinks dereferenced;
    same physical folder via two paths shares one log). ``isolate`` keeps
    the symlink path distinct (one log per path; intentionally breaks
    symlink-merged workspaces).

    Returns a 32-char prefix of the SHA-256 hex digest.
    """
    try:
        from .folder_context import _resolve_with_symlink_policy
        p = _resolve_with_symlink_policy(folder_path)
    except Exception:
        p = Path(folder_path).expanduser().resolve()
    absolute = str(p)
    if _filesystem_is_case_insensitive(p):
        absolute = absolute.lower()
    return hashlib.sha256(absolute.encode("utf-8")).hexdigest()[:32]


def legacy_folder_hash(folder_path: str | Path) -> str:
    """Pre-#162 hash function — case-sensitive on the input string.

    Kept only for backwards-compat: existing mutation logs were written
    under this hash before the fix. WorkspaceMemory falls back to this when a
    fresh ``folder_hash()`` lookup misses, so old data still reads.
    """
    absolute = str(Path(folder_path).expanduser().resolve())
    return hashlib.sha256(absolute.encode("utf-8")).hexdigest()[:32]


@dataclass
class LogEvent:
    """One entry in a folder's mutation log."""

    event: str                       # see VALID_EVENTS
    folder_path: str
    pair_id: str
    lifecycle_state: str = ""
    channel: str = "system"
    problem_id: str = ""
    source_hash: str = ""
    actor: str = "system"
    audit_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = ""              # set by MutationLog.append() — chain link to predecessor (0.6.5+)
    signature: str = ""              # set by MutationLog.append() — Ed25519 signature, hex (0.6.6+)
    host_id: str = ""                # set by MutationLog.append() — 12-char host fingerprint (0.6.8+)

    def __post_init__(self) -> None:
        if self.event not in VALID_EVENTS:
            raise ValueError(
                f"unknown event '{self.event}'. Valid: {sorted(VALID_EVENTS)}"
            )
        if self.channel not in VALID_CHANNELS:
            raise ValueError(
                f"unknown channel '{self.channel}'. Valid: {sorted(VALID_CHANNELS)}"
            )
        if not self.pair_id:
            raise ValueError("pair_id is required")
        # NOTE: folder_path is intentionally not validated here. The log
        # overwrites it on append() to match the log's own folder. Callers
        # can omit it; downstream readers always see the corrected value.

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "LogEvent":
        return cls(
            event=str(d.get("event", "")),
            folder_path=str(d.get("folder_path", "")),
            pair_id=str(d.get("pair_id", "")),
            lifecycle_state=str(d.get("lifecycle_state", "")),
            channel=str(d.get("channel", "system")),
            problem_id=str(d.get("problem_id", "")),
            source_hash=str(d.get("source_hash", "")),
            actor=str(d.get("actor", "system")),
            audit_id=str(d.get("audit_id", "")),
            ts=float(d.get("ts", 0.0)),
            extra=dict(d.get("extra") or {}),
            prev_hash=str(d.get("prev_hash", "")),
            signature=str(d.get("signature", "")),
            host_id=str(d.get("host_id", "")),
        )


class SealedWriteError(RuntimeError):
    """Raised on an attempt to WRITE (append/purge) to a sealed workspace. A sealed
    workspace is read-only (served in memory); unseal it before writing."""


class MutationLog:
    """Folder-scoped append-only JSONL log.

    Lifecycle:

    .. code-block:: python

        log = MutationLog("/companies/acme/HR/onboarding/")
        log.append(LogEvent(event="ingest", folder_path=..., pair_id=..., ...))
        for evt in log.replay():
            ...

    Each instance manages exactly one folder's log. To read multiple folders
    (e.g. the parent's combined view per the asymmetric hierarchical rule),
    construct one MutationLog per descendant and union the replays. That logic
    lives in the ``WorkspaceMemory`` interface (A2), not here.
    """

    def __init__(
        self,
        folder_path: str | Path,
        *,
        log_root: str | Path | None = None,
    ):
        # Persistence is a final trust boundary, not merely a consumer of a
        # path another layer was expected to validate.  This protects direct
        # stdio-MCP and Python callers as well as the HTTP bridge.  Ad-hoc
        # trusted-local use remains an explicit opt-in through the existing
        # WORKSPACES_ALLOW_UNREGISTERED escape hatch.
        from .folder_context import resolve_folder_context

        self.folder_path = resolve_folder_context(folder_path)
        self._folder_id = folder_hash(self.folder_path)
        root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
        self._log_dir = root / self._folder_id
        self._log_file = self._log_dir / "events.jsonl"
        self._root = root
        # Do not recreate the store dir if the workspace is SEALED (a .sealed blob
        # exists): leaving an empty plaintext dir beside the ciphertext would
        # make unseal refuse. Reads guard on file existence; writes refuse
        # (append/purge) while sealed.
        if not self._is_sealed():
            self._log_dir.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------------
    # Properties for inspection / tests
    # ----------------------------------------------------------------------

    @property
    def folder_id(self) -> str:
        """The 32-char folder hash this log is keyed by."""
        return self._folder_id

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    @property
    def log_file(self) -> Path:
        return self._log_file

    def _is_sealed(self) -> bool:
        """True if this workspace's memory store is sealed (a ``.sealed`` blob exists
        for the primary or legacy folder hash)."""
        try:
            if (self._root / (self._folder_id + ".sealed")).exists():
                return True
            return (self._root / (legacy_folder_hash(self.folder_path) + ".sealed")).exists()
        except Exception:
            return False

    # ----- genesis key pin (opt-in) -----------------------------------------

    def _pin_file(self) -> Path:
        """The TOFU pin file for this folder. Defaults beside the log; relocate
        it off the log tree via WORKSPACE_KEY_PIN_DIR so a filesystem adversary
        who rewrites the log cannot silently rewrite the pin to match."""
        pin_dir = os.environ.get(KEY_PIN_DIR_ENV)
        base = Path(pin_dir).expanduser() / self._folder_id if pin_dir else self._log_dir
        return base / "identity.pin"

    def _read_pin_fingerprint(self) -> "str | None":
        try:
            data = json.loads(self._pin_file().read_text(encoding="utf-8"))
            fp = str(data.get("fingerprint", ""))
            return fp or None
        except Exception:
            return None

    def _write_pin(self, fingerprint: str) -> None:
        """Persist the TOFU pin once, at registration. Never overwrites an
        existing pin (a changed pin is a signal, not something to paper over)."""
        pf = self._pin_file()
        if pf.exists():
            return
        try:
            pf.parent.mkdir(parents=True, exist_ok=True)
            tmp = pf.with_name(pf.name + ".tmp")
            tmp.write_text(json.dumps({"folder_id": self._folder_id,
                                       "fingerprint": fingerprint}),
                           encoding="utf-8")
            os.replace(tmp, pf)
        except Exception as e:                       # noqa: BLE001 — never fail append
            _log.warning("mutation_log: pin write failed on %s (%s)", pf, e)

    def _build_registration_event(self) -> "LogEvent | None":
        """A genesis key_registration event binding this chain to the on-disk
        identity key. None if no key is available (nothing to pin)."""
        try:
            from . import signing
            fp, pem = signing.register_identity()
            if not fp or not pem:
                return None
        except Exception:
            return None
        return LogEvent(
            event="key_registration",
            folder_path=self.folder_path,
            pair_id=f"key_registration:{self._folder_id}",
            actor="system",
            extra={"kind": "key_registration",
                   "identity_fingerprint": fp,
                   "identity_pub": pem},
        )

    def _ensure_registered(self) -> None:
        """When pinning is enabled, a fresh chain gets a genesis
        key_registration event before its first real event, and the TOFU pin is
        written. No-op once the chain has events (registration is a genesis act,
        never injected mid-chain) or when no signing key is available."""
        if os.environ.get(KEY_PINNING_ENV) != "1":
            return
        try:
            if self._log_file.exists() and self._log_file.stat().st_size > 0:
                return
        except OSError:
            return
        reg = self._build_registration_event()
        if reg is None:
            return
        self.append(reg)
        self._write_pin(reg.extra["identity_fingerprint"])

    # ----------------------------------------------------------------------
    # Writes
    # ----------------------------------------------------------------------

    def append(self, event: LogEvent) -> str:
        """Append one event. Returns its ``audit_id``.

        The event's ``folder_path`` is overwritten to match this log's folder
        for consistency — callers don't have to remember to set it correctly.

        Tamper-evidence (post-0.6.5): the event's ``prev_hash`` is set to the
        canonical SHA-256 of the most recent existing event in the log (or
        ``GENESIS`` for the first event). ``verify_chain()`` later validates
        that every link still resolves. Deletion, modification, or reordering
        of any event between writes will surface as a broken link.

        Concurrency: the read-last-then-append sequence is wrapped in an OS
        file lock (``fcntl.flock`` on Unix, ``msvcrt.locking`` on Windows) so
        multiple appenders to the same log produce a valid chain. Each
        appender holds the exclusive lock while it reads the previous event's
        hash and writes its own line, then releases. An unavailable or failed
        OS lock refuses the operation rather than proceeding unsynchronised.

        Refuses if the workspace is SEALED: writing would leave plaintext events
        beside the ciphertext. Unseal (or never seal) before writing.
        """
        if self._is_sealed():
            raise SealedWriteError(
                "workspace is sealed — unseal it before writing; a sealed workspace is "
                "read-only (served in memory). Writing would leak plaintext.")
        # Genesis key pin (opt-in): record the signing identity as the chain's
        # first event. Guarded against recursion — the registration event is
        # itself an append.
        if event.event != "key_registration":
            self._ensure_registered()
        event.folder_path = self.folder_path
        # Stamp host_id before hashing + signing so it's part of the canonical
        # content and the signature binds it (0.6.8 B4). Falls back to empty
        # string if signing layer can't compute one — old-shape events still
        # validate (host_id field is optional on read).
        if not event.host_id:
            try:
                from . import signing
                event.host_id = signing._host_id()
            except Exception:
                event.host_id = ""

        # Hold an exclusive lock across read-last-then-append so concurrent
        # appenders form a valid chain instead of racing on the predecessor.
        # The lock release-via-close issue is avoided: we hold the lock for
        # the duration of the open file handle and explicitly fsync before
        # the lock drops on exit.
        try:
            fh_ctx = self._log_file.open("a+", encoding="utf-8")
        except FileNotFoundError:
            # seal_folder removed the plaintext dir between our sealed check
            # above and this open. Surface the typed refusal, not a raw OSError
            # — and never recreate the plaintext dir beside the ciphertext.
            if self._is_sealed():
                raise SealedWriteError(
                    "workspace was sealed while this append was in flight — "
                    "the event was not written; unseal before writing.")
            raise
        with fh_ctx as fh:
            with _file_lock(fh, exclusive=True):
                # Re-check under the lock: seal_folder holds this same lock
                # across snapshot -> blob -> plaintext removal, so an append
                # that was already past the entry check and then blocked here
                # must refuse rather than write into a just-sealed store.
                if self._is_sealed():
                    raise SealedWriteError(
                        "workspace was sealed while this append awaited the "
                        "log lock — the event was not written; unseal before "
                        "writing.")
                if not event.prev_hash:
                    event.prev_hash = (
                        self._tail_hash_cached(fh)
                        or GENESIS_HASH
                    )
                # Sign the event (0.6.6+). Signature binds content + chain
                # position. Failure to sign (e.g. no key dir writable) is
                # tolerated — the chain still has hash-chain protection.
                if not event.signature:
                    try:
                        from .signing import sign_bytes
                        signed = _signed_bytes({
                            **asdict(event),
                            "signature": "",  # exclude from signed payload
                        })
                        event.signature = sign_bytes(signed)
                    except Exception:
                        # Signing failure → empty signature, treated as
                        # unsigned event on verify (legacy-compatible behaviour).
                        event.signature = ""
                line = event.to_jsonl() + "\n"
                # B6.1 (0.6.8): disk-full mid-append. Remember the file
                # size before we write; on ENOSPC truncate back to that
                # size so the chain never carries a half-written line.
                try:
                    pre_write_size = fh.tell()
                except OSError:
                    pre_write_size = None
                try:
                    fh.write(line)
                    fh.flush()
                except OSError as e:
                    if e.errno == errno.ENOSPC:
                        rollback_ok = False
                        if pre_write_size is not None:
                            try:
                                fh.flush()
                            except OSError as fe:
                                _log.debug("flush during ENOSPC rollback "
                                           "failed: %s", fe)
                            try:
                                os.ftruncate(fh.fileno(), pre_write_size)
                                rollback_ok = True
                            except OSError as te:
                                # Integrity-relevant: a failed rollback can
                                # leave a half-written line on the chain.
                                _log.warning(
                                    "ENOSPC rollback truncate failed on %s: "
                                    "%s — the log tail may carry a partial "
                                    "line; verify_chain will surface it",
                                    self._log_file, te,
                                )
                        if rollback_ok:
                            _log.error(
                                "mutation_log: disk full during append to "
                                "%s; truncated partial write back to %s "
                                "bytes", self._log_file, pre_write_size,
                            )
                        else:
                            _log.error(
                                "mutation_log: disk full during append to "
                                "%s; a partial line may remain on the log "
                                "tail", self._log_file,
                            )
                        raise DiskFullError(
                            errno.ENOSPC,
                            "no space left on device while appending mutation_log event",
                            str(self._log_file),
                        ) from e
                    raise
                try:
                    os.fsync(fh.fileno())
                except OSError as e:
                    # Integrity-relevant: without fsync the appended event
                    # may be lost on power failure. The append succeeded;
                    # durability is what's degraded — say so.
                    _log.warning("fsync after append to %s failed (%s); "
                                 "event durability not guaranteed",
                                 self._log_file, e)
                # The event just written is the new tail; remember its hash and
                # the file size so the next append on this instance can skip
                # the full-file scan (see _tail_hash_cached).
                head = _canonical_event_hash(json.loads(event.to_jsonl()))
                try:
                    self._tail_cache = (head, os.fstat(fh.fileno()).st_size)
                except OSError:
                    self._tail_cache = None
        # D6: refresh the signed head anchor so a later tail-truncation is detectable.
        self._write_anchor(head=head)
        return event.audit_id

    def _last_event_canonical_hash(self) -> str:
        """Canonical hash of the most-recent well-formed event, or empty string.

        B6.2: tolerant of malformed-UTF-8 / malformed-JSON lines — skips them
        rather than raising, so a corrupted middle line does not orphan the
        chain or block subsequent appends.
        """
        if not self._log_file.exists():
            return ""
        last_obj: dict | None = None
        with self._log_file.open("rb") as fh:
            with _file_lock(fh, exclusive=False):
                for raw in fh:
                    try:
                        line = raw.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        continue
                    if not line:
                        continue
                    try:
                        last_obj = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
        if last_obj is None:
            return ""
        return _canonical_event_hash(last_obj)

    # ----- D6: signed head anchor (tail-truncation evidence) ----------------
    def _anchor_file(self):
        """Sidecar holding the SIGNED hash of the chain's current head. Without it,
        dropping the last N signed events still validates (each remaining prev_hash
        still links) — tail-truncation is invisible. The anchor closes that gap."""
        return self._log_dir / "events.anchor"

    def _write_anchor(self, head: str = "") -> None:
        """Update the signed head anchor after an append. Best-effort: a failure here
        never breaks the append (the event is already durably written) — verify
        tolerates a missing anchor (unanchored = legacy). ``head`` skips the
        full-file scan when the caller already knows the tail hash (an append
        just computed it); compaction and other callers leave it empty."""
        try:
            head = head or self._last_event_canonical_hash()
            if not head:
                return
            from .signing import sign_bytes
            sig = sign_bytes(f"head|{head}".encode("utf-8"))
            af = self._anchor_file()
            tmp = af.with_name(af.name + ".tmp")
            tmp.write_text(json.dumps({"head_hash": head, "signature": sig}),
                           encoding="utf-8")
            os.replace(tmp, af)
        except Exception as e:                       # noqa: BLE001 — never fail append
            _log.warning("mutation_log: head-anchor update failed on %s (%s)",
                         self._log_file, e)

    def _verify_anchor(self, chain_hashes: set, public_key) -> "dict | None":
        """D6 check: the anchored head must still be PRESENT in the chain. Absent =>
        the tail was truncated (or the anchored event removed) — the gap D6 closes.
        Growth is fine (the anchored head is then an interior ancestor, still
        present). A signed anchor that cannot be verified, or whose head is gone, is
        a broken link. No anchor => None (unanchored, tolerated)."""
        af = self._anchor_file()
        if not af.exists():
            return None
        try:
            data = json.loads(af.read_text(encoding="utf-8"))
            head = str(data.get("head_hash", ""))
            sig = str(data.get("signature", ""))
        except Exception:
            return {"position": -1, "audit_id": None, "reason": "anchor_unreadable"}
        if not head or not sig:
            return {"position": -1, "audit_id": None, "reason": "anchor_malformed"}
        # The anchor is signed: a corrupted/forged anchor must not pass (when keys
        # are available — consistent with the per-event signature layer).
        if public_key is not None:
            try:
                from .signing import verify_signature
                if not verify_signature(f"head|{head}".encode("utf-8"), sig, public_key):
                    return {"position": -1, "audit_id": None,
                            "reason": "anchor_signature_invalid"}
            except Exception:
                return {"position": -1, "audit_id": None, "reason": "anchor_verify_error"}
        if head not in chain_hashes:
            return {"position": -1, "audit_id": None,
                    "reason": "tail_truncation_anchored_head_missing"}
        return None

    def _tail_hash_cached(self, fh) -> str:
        """Tail hash for an append, under the held exclusive lock.

        After this instance appends, the tail is the event it just wrote — so a
        (head hash, file size) pair cached at that moment stays valid until some
        other writer grows or rewrites the file, which a size comparison
        detects. On a match the full-file scan is skipped; every other case
        falls back to the scan. Chain integrity never rests on the cache:
        ``verify_chain`` re-derives every link from the file."""
        cached = getattr(self, "_tail_cache", None)
        if cached:
            try:
                if os.fstat(fh.fileno()).st_size == cached[1]:
                    return cached[0]
            except OSError:
                pass
        return self._last_event_canonical_hash_locked(fh)

    def _last_event_canonical_hash_locked(self, fh) -> str:
        """Variant of ``_last_event_canonical_hash`` for use INSIDE an already-locked
        ``a+`` handle. Saves the seek position, rewinds, scans, then restores
        the file pointer for the upcoming write.

        B6.2: re-opens the same file in binary mode for the scan so a single
        malformed-UTF-8 line cannot raise out of the iterator and block the
        impending append. The original text-mode handle is left at end-of-file
        so the caller's write continues uninterrupted.
        """
        try:
            saved_pos = fh.tell()
        except OSError:
            saved_pos = None
        last_obj: dict | None = None
        try:
            with open(self._log_file, "rb") as bin_fh:
                for raw in bin_fh:
                    try:
                        line = raw.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        continue
                    if not line:
                        continue
                    try:
                        last_obj = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
        except OSError:
            return ""
        try:
            if saved_pos is not None:
                fh.seek(saved_pos)
            else:
                fh.seek(0, os.SEEK_END)
        except OSError as e:
            _log.debug("seek restore after tail-scan failed: %s", e)
        if last_obj is None:
            return ""
        return _canonical_event_hash(last_obj)

    def append_raw(self, **kwargs: Any) -> str:
        """Convenience: build a LogEvent inline and append. Returns audit_id.

        Required kwargs: ``event``, ``pair_id``. All other LogEvent fields are
        optional with defaults. ``folder_path`` is set automatically.
        """
        kwargs.setdefault("folder_path", self.folder_path)
        evt = LogEvent(**kwargs)
        return self.append(evt)

    # ----------------------------------------------------------------------
    # Reads
    # ----------------------------------------------------------------------

    def replay(self) -> Iterator[LogEvent]:
        """Yield every event in append order. Skips malformed lines silently.

        For partial-write recovery: a corrupt or half-written final line is
        ignored, not raised. The log's job is to never lose what was written
        cleanly, even if a process died mid-write.

        B6.2 (0.6.8): the file is opened in BINARY mode and each line is
        decoded individually; a single malformed-UTF-8 line no longer makes
        the entire iterator raise and block the next append. Decode errors
        are counted via :py:meth:`verify_chain`, not here (replay is silent
        by design).
        """
        if not self._log_file.exists():
            return
        with self._log_file.open("rb") as fh:
            for raw in fh:
                try:
                    line = raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                try:
                    yield LogEvent.from_dict(obj)
                except (ValueError, TypeError):
                    continue

    def replay_filtered(
        self,
        predicate: Callable[[LogEvent], bool],
    ) -> Iterator[LogEvent]:
        """Yield events matching the predicate, in append order."""
        for evt in self.replay():
            if predicate(evt):
                yield evt

    def latest_state(self, pair_id: str) -> str | None:
        """Return the most-recent ``lifecycle_state`` for a pair, or None if unknown.

        Implements the most-recent-state-wins rule used by
        ``WorkspaceMemory.view_for`` to compute the current state of a pair
        from its event history.
        """
        latest: str | None = None
        for evt in self.replay():
            if evt.pair_id == pair_id and evt.lifecycle_state:
                latest = evt.lifecycle_state
        return latest

    def pair_ids(self, *, exclude_states: tuple[str, ...] = ("deleted", "purged")) -> set[str]:
        """Return the set of pair_ids whose current state is not in ``exclude_states``.

        Default excludes ``deleted`` and ``purged`` so callers get the "live"
        pair set without re-running the most-recent-state computation per pair.
        """
        seen: dict[str, str] = {}  # pair_id -> most-recent lifecycle_state
        for evt in self.replay():
            if evt.lifecycle_state:
                seen[evt.pair_id] = evt.lifecycle_state
        return {pid for pid, state in seen.items() if state not in exclude_states}

    def count(self) -> int:
        """Total number of events in the log (including malformed ones — they still take a line)."""
        if not self._log_file.exists():
            return 0
        with self._log_file.open("r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)

    def verify_chain(self) -> ChainVerificationResult:
        """Walk the log and validate the hash chain + Ed25519 signatures.

        Two layers of protection:

        1. **Hash chain (0.6.5+):** for each well-formed event, recompute what
           its ``prev_hash`` SHOULD be (canonical hash of the previous event
           in the log, or ``GENESIS``) and compare against the stored value.
           Mismatches are silent-tamper evidence at the per-event level.

        2. **Ed25519 signature (0.6.6+):** for each well-formed event with a
           ``signature``, verify the signature against the canonical content
           + prev_hash. Mismatches catch chain-rewrite attacks — an adversary
           with filesystem access could recompute prev_hashes downstream of a
           deletion, but cannot forge signatures without the private key.

        Legacy events without ``prev_hash`` (pre-0.6.5) are accepted but not
        validated against the hash chain. Events without ``signature``
        (pre-0.6.6 or signing failure) are accepted but not validated against
        the signature layer.

        Returns a ``ChainVerificationResult``; truthy iff ``ok``.
        """
        broken: list[dict] = []
        signature_failures: list[dict] = []
        malformed = 0
        total = 0
        legacy = 0
        unsigned = 0
        purged_with_tombstone = 0
        chain_hashes: set[str] = set()   # D6: every well-formed event's canonical hash
        expected_prev = GENESIS_HASH
        # B1 (0.6.8): when a purge tombstone is the immediate predecessor,
        # the next event's prev_hash will not match the tombstone's canonical
        # hash directly (the tombstone was inserted into a previously-
        # complete chain). We track whether the previous event was a purge
        # event so a single subsequent break is interpreted as an
        # authorised re-link, not tampering.
        prev_was_purge = False
        # D5: once the chain has carried a signature, every later event must too
        # — a subsequent unsigned event is a stripped signature (tamper).
        seen_signed = False
        # A2: track host_id across the chain to surface cross-host divergence.
        host_divergence: list[dict] = []
        prev_host_id: str | None = None
        # Genesis key pin: the fingerprint the first key_registration event
        # commits the chain to (None on an unpinned/legacy chain).
        registered_fp: str | None = None

        # Try to obtain the public key for signature verification. If keys
        # aren't available (e.g. fresh checkout, no signing dependency),
        # signature checks are skipped — events still validate via hash chain.
        # Read identity.pub first: verification must not require the private
        # key (or its passphrase); generating is the fallback for a fresh dir.
        public_key = None
        try:
            from .signing import ensure_keypair, identity_public_key_or_none
            public_key = identity_public_key_or_none()
            if public_key is None:
                _, public_key = ensure_keypair()
        except Exception:
            public_key = None

        if not self._log_file.exists():
            # An absent log is only ok when nothing ever anchored it. The anchor
            # lives in its own file and survives the log's deletion, so a surviving
            # anchor commits to a head that no longer exists — the same tail
            # truncation D6 closes, taken to the limit. Verify against an empty
            # chain rather than short-circuiting, or removing the log would read
            # as a clean empty history.
            anchor_break = self._verify_anchor(set(), public_key)
            return ChainVerificationResult(
                ok=anchor_break is None, total_events=0, legacy_events=0,
                broken_links=[] if anchor_break is None else [anchor_break],
                malformed_lines=0,
                unsigned_events=0, signature_failures=[],
                purged_with_tombstone=0,
            )

        with self._log_file.open("rb") as fh:
            with _file_lock(fh, exclusive=False):
                for position, raw in enumerate(fh):
                    # B6.2: tolerate malformed-UTF-8 lines without raising.
                    # Unicode errors count as malformed_lines but do not
                    # populate ``broken_links`` (which is reserved for
                    # tamper-evidence — a corrupted byte sequence is
                    # accidental damage, not intentional rewrite).
                    try:
                        line = raw.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        malformed += 1
                        continue
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        malformed += 1
                        broken.append({
                            "position": position,
                            "audit_id": None,
                            "reason": "malformed_json",
                            "expected": expected_prev,
                            "found": None,
                        })
                        continue
                    total += 1
                    stored_prev = obj.get("prev_hash", "")
                    if not stored_prev:
                        legacy += 1
                    else:
                        if stored_prev != expected_prev:
                            # B1: if the immediately-preceding event was a
                            # purge tombstone, this mismatch is an
                            # authorised re-link, not tampering. Count it
                            # separately and let the chain continue.
                            if prev_was_purge:
                                purged_with_tombstone += 1
                            else:
                                broken.append({
                                    "position": position,
                                    "audit_id": obj.get("audit_id"),
                                    "reason": "prev_hash_mismatch",
                                    "expected": expected_prev,
                                    "found": stored_prev,
                                })
                    # Signature verification (if signature present + verifier available).
                    stored_sig = obj.get("signature", "")
                    if not stored_sig:
                        unsigned += 1
                        # D5: once the chain has begun signing (≥0.6.6), an
                        # unsigned event after that epoch is a stripped signature
                        # — the tamper path that let an attacker delete+re-link
                        # events and drop their signatures while verify_chain
                        # still returned ok. Pre-signing legacy events (before
                        # any signature) remain tolerated; only post-epoch
                        # unsigned events fail the chain.
                        if seen_signed:
                            signature_failures.append({
                                "position": position,
                                "audit_id": obj.get("audit_id"),
                                "reason": "unsigned_event_after_signing_epoch",
                            })
                    else:
                        seen_signed = True
                        if public_key is not None:
                            try:
                                from .signing import verify_signature
                                signed_data = _signed_bytes({**obj, "signature": ""})
                                if not verify_signature(signed_data, stored_sig, public_key):
                                    signature_failures.append({
                                        "position": position,
                                        "audit_id": obj.get("audit_id"),
                                        "reason": "ed25519_signature_invalid",
                                    })
                            except Exception as e:
                                signature_failures.append({
                                    "position": position,
                                    "audit_id": obj.get("audit_id"),
                                    "reason": f"signature_verify_error: {type(e).__name__}",
                                })
                    # Two-key purge tombstone validation. A tombstone that
                    # CLAIMS controller co-signature (controller_keyid set,
                    # erasure_mode "two-key", or a non-empty controller_sig)
                    # must validate that controller_sig against the registered
                    # controller pubkey — otherwise it is a forgery (an
                    # attacker asserting a controller authorised the erasure).
                    # Single-key tombstones (no controller claim) are not
                    # subject to this check; the operator signature above is
                    # their sole authority (L0 default).
                    _extra = obj.get("extra", {}) or {}
                    if _extra.get("kind") == "purge_tombstone":
                        _csig = _extra.get("controller_sig", "") or ""
                        _ckeyid = _extra.get("controller_keyid")
                        _mode = _extra.get("erasure_mode", "")
                        _claims_two_key = bool(_ckeyid) or _mode == "two-key" or bool(_csig)
                        if _claims_two_key:
                            try:
                                from .signing import verify_controller_signature_strict
                                # Reconstruct exactly what the controller signed:
                                # the tombstone with extra.controller_sig removed
                                # and an empty top-level signature.
                                import copy as _copy
                                _probe = _copy.deepcopy(obj)
                                _probe.get("extra", {}).pop("controller_sig", None)
                                _probe["signature"] = ""
                                _payload = _signed_bytes(_probe)
                                if not verify_controller_signature_strict(_payload, _csig):
                                    signature_failures.append({
                                        "position": position,
                                        "audit_id": obj.get("audit_id"),
                                        "reason": "controller_cosignature_invalid_or_unverifiable",
                                    })
                            except Exception as e:
                                signature_failures.append({
                                    "position": position,
                                    "audit_id": obj.get("audit_id"),
                                    "reason": f"controller_verify_error: {type(e).__name__}",
                                })

                    # Genesis key pin: capture the fingerprint the first
                    # key_registration event commits the chain to.
                    if (registered_fp is None
                            and obj.get("event") == "key_registration"):
                        registered_fp = str(_extra.get("identity_fingerprint", "")) or None

                    # A2: cross-host divergence. If host_id shifts between
                    # consecutive (host-stamped) events without a key_rotation
                    # marker, the signing key may have moved hosts — the residual
                    # signal for a key-theft chain-rewrite that still verifies.
                    _this_host = obj.get("host_id") or ""
                    _is_rotation = (
                        obj.get("event") == "key_rotation"
                        or (obj.get("extra", {}) or {}).get("kind") == "key_rotation"
                    )
                    if _this_host:
                        if (prev_host_id is not None
                                and _this_host != prev_host_id
                                and not _is_rotation):
                            host_divergence.append({
                                "position": position,
                                "audit_id": obj.get("audit_id"),
                                "prev_host_id": prev_host_id,
                                "host_id": _this_host,
                            })
                        prev_host_id = _this_host

                    # Track whether THIS event is a purge tombstone for the
                    # NEXT iteration's hash-chain interpretation, and also
                    # surface the count directly — even when the re-linker
                    # produced a fully clean chain (the usual case), the
                    # presence of a tombstone is the signal that an
                    # authorised erasure happened.
                    if obj.get("event") == "purge":
                        purged_with_tombstone += 1
                        prev_was_purge = True
                    else:
                        prev_was_purge = False
                    # Always update expected_prev to the canonical hash of the
                    # event we just saw, so the next iteration compares against
                    # the right predecessor.
                    expected_prev = _canonical_event_hash(obj)
                    chain_hashes.add(expected_prev)

        # D6: the signed head anchor must still point into the chain (else the tail
        # was truncated). Runs after the walk so chain_hashes is complete.
        anchor_break = self._verify_anchor(chain_hashes, public_key)
        if anchor_break is not None:
            broken.append(anchor_break)

        # Genesis key pin. Enforcement is NOT env-gated — once a chain carries a
        # registration, it is always checked, so unsetting the pinning var can't
        # downgrade it. The external pin file is the teeth: a full-directory
        # rewrite re-keys the chain AND its embedded registration consistently,
        # but cannot rewrite a pin relocated off the log tree.
        key_pin = self._check_key_pin(registered_fp, public_key, signature_failures)

        strict_host = os.environ.get("WORKSPACE_STRICT_HOST_DIVERGENCE") == "1"
        ok = (len(broken) == 0 and len(signature_failures) == 0
              and not (strict_host and host_divergence))
        return ChainVerificationResult(
            ok=ok,
            total_events=total,
            legacy_events=legacy,
            broken_links=broken,
            malformed_lines=malformed,
            unsigned_events=unsigned,
            signature_failures=signature_failures,
            purged_with_tombstone=purged_with_tombstone,
            host_divergence_warning=host_divergence,
            key_pin=key_pin,
        )

    def _check_key_pin(self, registered_fp, public_key,
                       signature_failures) -> "dict | None":
        """Enforce the genesis key pin. Appends to ``signature_failures`` (which
        fail the chain) on a mismatch; returns the pin status for the result.

        - unregistered chain: None, tolerated — unless WORKSPACE_STRICT_KEY_PINNING
          is set, which records ``chain_unregistered`` and fails the chain.
        - registered chain: the on-disk verifying key must match the registered
          fingerprint (``key_pin_mismatch`` if not), and the TOFU pin file, when
          present, must match the registered fingerprint (``key_pin_tampered`` if
          not — the case a relocated pin catches after a full re-key)."""
        strict = os.environ.get(STRICT_KEY_PINNING_ENV) == "1"
        if registered_fp is None:
            if strict:
                signature_failures.append({"position": None, "audit_id": None,
                                           "reason": "chain_unregistered"})
            return None

        pin_file_fp = self._read_pin_fingerprint()
        pin_state = ("absent" if pin_file_fp is None
                     else "match" if pin_file_fp == registered_fp else "mismatch")

        if public_key is not None:
            try:
                from . import signing
                ondisk_fp = signing.fingerprint_of(public_key)
                if ondisk_fp != registered_fp:
                    signature_failures.append({
                        "position": None, "audit_id": None,
                        "reason": "key_pin_mismatch"})
            except Exception as e:
                signature_failures.append({
                    "position": None, "audit_id": None,
                    "reason": f"key_pin_check_error: {type(e).__name__}"})

        if pin_state == "mismatch":
            signature_failures.append({"position": None, "audit_id": None,
                                       "reason": "key_pin_tampered"})

        return {"registered": True, "fingerprint": registered_fp,
                "pin_file": pin_state}

    # ----------------------------------------------------------------------
    # Maintenance
    # ----------------------------------------------------------------------

    def purge(
        self,
        pair_id: str,
        *,
        legal_basis: str = "",
        requester_ref: str = "",
        reason: str = "",
    ) -> int:
        """Physical erasure of every event referencing ``pair_id`` (GDPR Art. 17).

        Rewrites the log file, removing matching events, re-linking the
        ``prev_hash`` of every surviving event whose immediate predecessor
        was purged, re-signing those re-linked events with the operator key,
        and writing a single ``purge`` tombstone event at the end of the log
        that records what was erased and why. The tombstone names the pair
        through an opaque folder-salted ref (``forgotten_subjects.
        purged_pair_ref``), never the raw ``pair_id`` — the raw id may carry
        the very subject being erased.

        IRREVERSIBLE. Returns the number of events purged.

        Args:
            pair_id: the pair to erase.
            legal_basis: one of ``VALID_LEGAL_BASES`` (Art. 17(1)(a-f)).
                Required.
            requester_ref: opaque reference to the requesting data subject
                (e.g. case id, email hash). Required.
            reason: free-text reason describing the request. Required.

        Controller co-signature is optional. With a controller keypair the
        tombstone is co-signed (two-key); without one the operator signature
        alone authorises the purge (single-key) and the tombstone's
        ``extra.erasure_mode`` records which path was taken. ``verify_chain``
        validates the operator signature in both cases.

        Raises:
            ValueError: if ``legal_basis`` is missing or not in
                ``VALID_LEGAL_BASES``; if ``requester_ref`` or ``reason``
                is empty.

        Use sparingly — this breaks the audit-replay model. The default
        user-facing "delete" is a logical delete (an event with
        ``lifecycle_state="deleted"``); only true GDPR-Art.17-style erasure
        should call purge.
        """
        if self._is_sealed():
            raise SealedWriteError(
                "workspace is sealed — unseal it before purging; a sealed workspace is "
                "read-only.")
        # Argument validation — refuse undocumented purges.
        if not legal_basis:
            raise ValueError(
                "purge requires legal_basis (one of "
                f"{sorted(VALID_LEGAL_BASES)})"
            )
        if legal_basis not in VALID_LEGAL_BASES:
            raise ValueError(
                f"unknown legal_basis '{legal_basis}'. Valid: "
                f"{sorted(VALID_LEGAL_BASES)}"
            )
        if not requester_ref:
            raise ValueError("purge requires requester_ref")
        if not reason:
            raise ValueError("purge requires reason")

        # Controller co-signature is optional (L0-first default). If a
        # controller keypair has been deliberately initialised
        # (`workspaces keys init-controller`), the tombstone is co-signed by
        # both operator and controller (two-key erasure — meaningful when
        # operator and controller are separate custodians, e.g. L1/L2).
        # If no controller key exists, the purge proceeds with the operator
        # signature alone (single-key erasure) and the tombstone records
        # that fact via ``erasure_mode``. At L0 (single user, one
        # machine) a second key on the same disk adds ceremony without real
        # separation of custody, so it is not forced.
        from . import signing
        _has_controller = signing.public_controller_key_fingerprint() is not None

        if not self._log_file.exists():
            return 0

        # Read + rewrite under an exclusive lock so a concurrent appender
        # cannot interleave between the read and the atomic replace.
        with self._log_file.open("a+", encoding="utf-8") as fh:
            with _file_lock(fh, exclusive=True):
                fh.seek(0)
                raw_lines = fh.readlines()

        # Parse all events; collect purge targets + survivors.
        parsed: list[tuple[str, dict | None]] = []
        purged_audit_ids: list[str] = []
        for line in raw_lines:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                parsed.append((line, None))
                continue
            if obj.get("pair_id") == pair_id:
                purged_audit_ids.append(str(obj.get("audit_id", "")))
                continue
            parsed.append((line, obj))

        purged_count = len(purged_audit_ids)
        if purged_count == 0:
            return 0

        # Walk the survivors. Each survivor whose stored prev_hash no
        # longer matches the canonical hash of its new immediate
        # predecessor must be re-linked + re-signed.
        prev_canon = GENESIS_HASH
        rewritten_lines: list[str] = []
        for line, obj in parsed:
            if obj is None:
                rewritten_lines.append(line)  # preserve malformed verbatim
                continue
            stored_prev = obj.get("prev_hash", "")
            if stored_prev and stored_prev != prev_canon:
                # Re-link. Update prev_hash, then re-sign (signature binds
                # content + chain position; the chain position has moved).
                obj["prev_hash"] = prev_canon
                try:
                    signed = _signed_bytes({**obj, "signature": ""})
                    obj["signature"] = signing.sign_bytes(signed)
                except Exception as e:
                    # A re-linked survivor must be re-signed. Leaving it unsigned
                    # would (a) create exactly the post-epoch unsigned event the
                    # chain's tamper-evidence treats as a stripped signature (D5),
                    # and (b) silently degrade integrity. Abort the purge loudly —
                    # nothing has been written yet (the rewrite is atomic, below),
                    # so the original log is intact and the operator can retry once
                    # signing is available.
                    raise RuntimeError(
                        f"purge aborted: could not re-sign re-linked survivor "
                        f"{obj.get('audit_id')!r} ({type(e).__name__}: {e}). "
                        f"The log is unchanged; restore signing capability and retry."
                    ) from e
                rewritten_lines.append(
                    json.dumps(obj, ensure_ascii=False) + "\n"
                )
            else:
                # Untouched — keep verbatim (preserves operator's original
                # signature exactly).
                rewritten_lines.append(line if line.endswith("\n") else line + "\n")
            prev_canon = _canonical_event_hash(obj)

        # Build the tombstone. Operator + controller signatures are
        # computed once the canonical content (incl. prev_hash) is known.
        # The tombstone outlives the events it erases, so it names the pair
        # only through an opaque folder-salted ref — a subject-bearing pair
        # id (e.g. a legacy card id carrying a person's name) must not
        # survive its own erasure here. Consequence: a second purge of the
        # same raw id finds nothing and returns 0 instead of consuming this
        # tombstone, which previously destroyed the erasure record.
        from .forgotten_subjects import purged_pair_ref
        tombstone: dict[str, Any] = {
            "ts": time.time(),
            "event": "purge",
            "folder_path": self.folder_path,
            "pair_id": purged_pair_ref(self.folder_path, pair_id),
            "channel": "system",
            "actor": "system:purge",
            "audit_id": str(uuid.uuid4()),
            "lifecycle_state": "purged",
            "problem_id": "",
            "source_hash": "",
            "extra": {
                "kind": "purge_tombstone",
                "purged_event_audit_ids": purged_audit_ids,
                "purged_event_count": purged_count,
                "legal_basis": legal_basis,
                "requester_ref": requester_ref,
                "reason": reason,
                "operator_keyid": signing.public_key_fingerprint(),
                # Only record a controller_keyid when one was deliberately
                # initialised. Do not call any function that would auto-create it.
                "controller_keyid": signing.public_controller_key_fingerprint() if _has_controller else None,
                # Record how this erasure was authorised:
                # "two-key" = operator + controller co-signature (separate
                # custodians); "single-key" = operator signature only (L0
                # default, no controller key initialised).
                "erasure_mode": "two-key" if _has_controller else "single-key",
            },
            "prev_hash": prev_canon,
            "signature": "",
            "host_id": "",
        }
        try:
            tombstone["host_id"] = signing._host_id()
        except Exception:
            tombstone["host_id"] = ""

        # IMPORTANT ordering: the operator signature is the LAST thing
        # added because it must bind every other field (including the
        # controller co-signature). Verifier recomputes by stripping the
        # top-level ``signature`` and re-canonicalising — anything inside
        # ``extra`` therefore is covered.
        #
        # 1) Compute controller co-signature only if a controller key was
        #    deliberately initialised. ``sign_with_controller`` would
        #    otherwise auto-create the keypair on first use — which defeats
        #    the opt-in model (single-key erasure is the L0 default).
        if _has_controller:
            try:
                controller_sig = signing.sign_with_controller(
                    _signed_bytes({**tombstone, "signature": ""})
                )
            except Exception:
                controller_sig = ""
        else:
            controller_sig = ""
        tombstone["extra"]["controller_sig"] = controller_sig

        # 2) Compute operator signature over the final canonical payload
        #    (controller_sig is now part of ``extra``, included in the hash).
        try:
            op_signed = _signed_bytes({**tombstone, "signature": ""})
            tombstone["signature"] = signing.sign_bytes(op_signed)
        except Exception:
            tombstone["signature"] = ""

        rewritten_lines.append(json.dumps(tombstone, ensure_ascii=False) + "\n")

        # Atomic-ish rewrite via temp file in the same directory, under
        # the same exclusive lock as the read.
        # B6.1 (0.6.8): ENOSPC during the temp-file rewrite must not leave
        # a half-written shard masquerading as the real log. Catch ENOSPC,
        # delete the temp file, raise DiskFullError so the caller knows
        # the purge did not take effect.
        tmp = self._log_file.with_suffix(".jsonl.tmp")
        with self._log_file.open("a+", encoding="utf-8") as fh:
            with _file_lock(fh, exclusive=True):
                try:
                    with tmp.open("w", encoding="utf-8") as wfh:
                        wfh.writelines(rewritten_lines)
                        wfh.flush()
                        try:
                            os.fsync(wfh.fileno())
                        except OSError as fe:
                            _log.warning("fsync of purge rewrite shard %s "
                                         "failed (%s); shard durability not "
                                         "guaranteed", tmp, fe)
                except OSError as e:
                    if e.errno == errno.ENOSPC:
                        try:
                            tmp.unlink()
                        except OSError as ue:
                            _log.warning("could not remove temp shard %s "
                                         "after ENOSPC (%s); stale .tmp may "
                                         "remain beside the log", tmp, ue)
                        _log.error(
                            "mutation_log: disk full during purge rewrite of %s; "
                            "original log left intact, purge did not apply",
                            self._log_file,
                        )
                        raise DiskFullError(
                            errno.ENOSPC,
                            "no space left on device while rewriting mutation_log during purge",
                            str(self._log_file),
                        ) from e
                    raise
                os.replace(tmp, self._log_file)
        # D6: the purge re-linked the chain, so the head hash changed — refresh the
        # signed anchor to the new head (else verify would read it as truncation).
        self._write_anchor()
        return purged_count


def events_from_bytes(data: bytes) -> "Iterator[LogEvent]":
    """Parse newline-delimited event JSON into LogEvents, skipping malformed
    lines — the same tolerance rule as ``MutationLog.replay()``. Pure: no IO.

    Lets a sealed workspace be read from served (in-memory) bytes without touching
    the on-disk read path. See ``workspaces.workspace_lock``.
    """
    for raw in data.splitlines():
        try:
            line = raw.decode("utf-8").strip() if isinstance(raw, (bytes, bytearray)) else str(raw).strip()
        except UnicodeDecodeError:
            continue
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        try:
            yield LogEvent.from_dict(obj)
        except (ValueError, TypeError):
            continue
