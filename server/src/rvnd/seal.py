# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""At-rest encryption for a folder's memory — ``workspaces seal`` / ``unseal``.

Draw a workspace around a folder and lock it: ``seal`` encrypts the folder's
memory store (its mutation-log directory under ``~/.workspace/log/<hash>/``) into
a single sealed blob and removes the plaintext; ``unseal`` restores it with
the passphrase.

What this protects: the memory at rest — a folder synced to the cloud, copied,
backed up, or on a lost disk shows ciphertext, not your captured documents and
exchanges. What it does not protect: data in use. While unsealed (i.e. while
you are working), the plaintext is on disk and a host with file access can read
it. This is temporary, opt-in protection for the at-rest window, not a
boundary around a live session — see docs/THREAT-MODEL.md.

Design notes that keep it safe:
- The audit chain is untouched. Seal/unseal operate on the stored files as
  opaque bytes; ``verify_chain`` runs on the restored plaintext exactly as
  before. Sealing does not re-sign or rewrite events.
- The key is derived from a passphrase with scrypt and is never written to
  disk. Lose the passphrase and the sealed memory is unrecoverable by design.
- AES-256-GCM authenticates the ciphertext, and the folder hash is bound in as
  associated data, so a wrong passphrase or a blob moved between folders fails
  cleanly rather than returning garbage.
- Signing keys (``~/.workspace/keys/``) are not sealed — they are per-host
  identity, separate from any one folder's memory.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .mutation_log import LOG_ROOT_DEFAULT, folder_hash, legacy_folder_hash

_MAGIC = "workspace-seal"
_VERSION = 1
# scrypt cost. n=2**14 keeps memory ~16 MB (under the stdlib default maxmem),
# enough to make a passphrase guess expensive without needing a tuning knob.
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32


class SealError(RuntimeError):
    """Raised when a seal/unseal operation cannot complete."""


def _resolve_log_dir(folder: str | Path, log_root: str | Path | None) -> Path:
    root = Path(log_root) if log_root else LOG_ROOT_DEFAULT
    primary = root / folder_hash(folder)
    if primary.exists():
        return primary
    legacy = root / legacy_folder_hash(folder)
    if legacy.exists():
        return legacy
    return primary


def _sealed_path(log_dir: Path) -> Path:
    return log_dir.parent / (log_dir.name + ".sealed")


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN,
    )


# ── transparent per-record encryption — data-IN-USE hardening ─────────────────────────────
# ``seal_folder`` is whole-store, at-rest-only: it leaves plaintext on disk while you work.
# These encrypt a SINGLE record so the on-disk bytes stay ciphertext even during a session —
# a raw-file read (another process, a sync client, a backup, a casual ``cat``) sees ciphertext,
# not content. The key is scrypt-derived from the passphrase and held only in this process; the
# folder hash is bound as AAD so a record can't be replayed into another folder.
#
# Limitation — hardening, not a boundary: it does not stop a host that owns this
# process — that host holds the key, so it can decrypt or just ask Rvnd. It raises the bar
# against everything that is not the Rvnd process. Constrain the owning host at the OS level.
_REC_MAGIC = b"RVEC1"


def encrypt_record(plaintext: bytes, *, passphrase: str, folder: str | Path) -> bytes:
    """Encrypt one record for an always-encrypted store. Returns ``magic|salt|nonce|ciphertext``."""
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _derive_key(passphrase, salt)
    aad = folder_hash(str(folder)).encode("utf-8")
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return _REC_MAGIC + salt + nonce + ct


def decrypt_record(blob: bytes, *, passphrase: str, folder: str | Path) -> bytes:
    """Decrypt a record in memory. Raises ``SealError`` on a wrong passphrase or a record from a
    different folder (the AAD authentication fails — tamper/replay evidence)."""
    if blob[:5] != _REC_MAGIC:
        raise SealError("not an Rvnd encrypted record")
    salt, nonce, ct = blob[5:21], blob[21:33], blob[33:]
    key = _derive_key(passphrase, salt)
    aad = folder_hash(str(folder)).encode("utf-8")
    try:
        return AESGCM(key).decrypt(nonce, ct, aad)
    except InvalidTag as e:
        raise SealError("record decryption failed — wrong passphrase or wrong folder") from e


def is_sealed(folder: str | Path, *, log_root: str | Path | None = None) -> bool:
    """True if the folder's memory is currently sealed."""
    return _sealed_path(_resolve_log_dir(folder, log_root)).exists()


# Plaintext knowledge sinks that live under the WORKSPACE FOLDER itself (not the
# log dir). Sealing the log dir alone leaves these in plaintext at rest, so a
# sealed workspace's knowledge would leak through them — ``.versum`` is the
# memory→versum knowledge sink (memory split), ``grounding`` is the grounder's
# versum sink + legacy store. They are packed into the same sealed blob under a
# distinct prefix so unseal routes them back to the folder, never to the log dir.
_FOLDER_MEMORY_SINKS: tuple[str, ...] = (".versum", "grounding")
_SINK_PREFIX = "__folder_sink__/"


def _existing_sinks(folder: Path) -> list[tuple[str, Path]]:
    """The folder's plaintext knowledge sinks that currently exist on disk."""
    return [(name, folder / name) for name in _FOLDER_MEMORY_SINKS
            if (folder / name).exists()]


def seal_folder(
    folder: str | Path,
    *,
    passphrase: str,
    log_root: str | Path | None = None,
) -> dict[str, Any]:
    """Encrypt the folder's memory (log dir + versum/grounding knowledge sinks) at
    rest and remove the plaintext."""
    if not passphrase:
        raise SealError("a passphrase is required to seal")
    log_dir = _resolve_log_dir(folder, log_root)
    sealed = _sealed_path(log_dir)
    if sealed.exists():
        raise SealError(f"already sealed: {sealed}")
    if not log_dir.exists():
        raise SealError(f"no memory to seal for this folder ({log_dir} does not exist)")

    # Serialise with in-flight appends: take the SAME exclusive lock the
    # mutation log holds around its read-last-then-append region. Without it,
    # an append landing between the snapshot below and the rmtree at the end
    # was silently destroyed AFTER append() had already returned its
    # audit_id — accepted evidence, gone. Holding the lock from snapshot to
    # rmtree means every append either completes before the snapshot (and is
    # sealed into the blob) or blocks until sealing is done and then refuses
    # via the in-lock sealed re-check in append(). If no events file exists
    # yet there is nothing to serialise against.
    from .mutation_log import _file_lock
    events_file = log_dir / "events.jsonl"
    lock_ctx = (
        open(events_file, "a+", encoding="utf-8")
        if events_file.exists() else contextlib.nullcontext()
    )
    with lock_ctx as lock_fh:
        with (_file_lock(lock_fh, exclusive=True)
              if lock_fh is not None else contextlib.nullcontext()):
            # Pack every file under the log dir into one manifest.
            files: dict[str, str] = {}
            for path in sorted(log_dir.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(log_dir).as_posix()
                    files[rel] = base64.b64encode(path.read_bytes()).decode("ascii")
            # Also pack the folder's plaintext knowledge sinks (.versum, grounding)
            # under a distinct prefix — they live under the workspace folder, not
            # log_dir, so the log seal alone would leave them in plaintext at rest.
            folder_sinks = _existing_sinks(Path(folder))
            for name, sink in folder_sinks:
                for path in sorted(sink.rglob("*")):
                    if path.is_file():
                        rel = _SINK_PREFIX + name + "/" + path.relative_to(sink).as_posix()
                        files[rel] = base64.b64encode(path.read_bytes()).decode("ascii")
            plaintext = json.dumps({"files": files}).encode("utf-8")

            salt = os.urandom(16)
            nonce = os.urandom(12)
            key = _derive_key(passphrase, salt)
            aad = log_dir.name.encode("ascii")  # bind to this folder's hash
            ct = AESGCM(key).encrypt(nonce, plaintext, aad)

            envelope = {
                "magic": _MAGIC,
                "v": _VERSION,
                "kdf": "scrypt",
                "n": _N, "r": _R, "p": _P,
                "salt": base64.b64encode(salt).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ct": base64.b64encode(ct).decode("ascii"),
            }
            # Write the sealed blob first; only delete the plaintext once it's
            # safely on disk.
            tmp = sealed.with_suffix(".sealed.tmp")
            tmp.write_text(json.dumps(envelope))
            os.replace(tmp, sealed)
            try:
                os.chmod(sealed, 0o600)
            except OSError:
                pass
            shutil.rmtree(log_dir)
            # remove the now-sealed plaintext knowledge sinks too
            for _name, sink in folder_sinks:
                shutil.rmtree(sink, ignore_errors=True)

    return {"sealed": True, "path": str(sealed), "files_sealed": len(files)}


def unseal_folder(
    folder: str | Path,
    *,
    passphrase: str,
    log_root: str | Path | None = None,
) -> dict[str, Any]:
    """Decrypt a sealed folder's memory and restore the plaintext directory."""
    if not passphrase:
        raise SealError("a passphrase is required to unseal")
    log_dir = _resolve_log_dir(folder, log_root)
    sealed = _sealed_path(log_dir)
    if not sealed.exists():
        raise SealError(f"not sealed: {sealed} does not exist")
    if log_dir.exists():
        raise SealError(f"refusing to overwrite existing plaintext at {log_dir}")
    for name in _FOLDER_MEMORY_SINKS:
        if (Path(folder) / name).exists():
            raise SealError(
                f"refusing to overwrite existing plaintext at {Path(folder) / name}")

    envelope = json.loads(sealed.read_text())
    if envelope.get("magic") != _MAGIC:
        raise SealError("not a Workspace seal file")
    salt = base64.b64decode(envelope["salt"])
    nonce = base64.b64decode(envelope["nonce"])
    ct = base64.b64decode(envelope["ct"])
    key = hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt,
        n=envelope.get("n", _N), r=envelope.get("r", _R),
        p=envelope.get("p", _P), dklen=_DKLEN,
    )
    aad = log_dir.name.encode("ascii")
    try:
        plaintext = AESGCM(key).decrypt(nonce, ct, aad)
    except InvalidTag:
        raise SealError("wrong passphrase or corrupted seal — nothing restored")

    manifest = json.loads(plaintext.decode("utf-8"))
    log_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for rel, b64 in manifest.get("files", {}).items():
        if rel.startswith(_SINK_PREFIX):
            # a folder knowledge sink (.versum / grounding) → back under the folder
            dest = Path(folder) / rel[len(_SINK_PREFIX):]
        else:
            dest = log_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(b64))
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        n += 1
    sealed.unlink()
    return {"unsealed": True, "files_restored": n}


# ---------------------------------------------------------------------------
# Workspace Lock — "sealed but served" read-through (no unseal-to-disk)
# ---------------------------------------------------------------------------

def _open_sealed(
    sealed: Path, aad_name: str, *,
    passphrase: str | None = None, key: bytes | None = None,
) -> tuple[dict[str, str], bytes]:
    """Decrypt a sealed envelope; return ``({rel: b64}, key)``.

    Accepts either a ``passphrase`` (derives the key via scrypt) or a
    pre-derived ``key`` (so a session that already unlocked once can serve
    further reads without paying scrypt again). Pure read: never touches the
    on-disk blob. The returned key lets a caller cache it for the session.
    """
    envelope = json.loads(sealed.read_text())
    if envelope.get("magic") != _MAGIC:
        raise SealError("not a Workspace seal file")
    salt = base64.b64decode(envelope["salt"])
    nonce = base64.b64decode(envelope["nonce"])
    ct = base64.b64decode(envelope["ct"])
    if key is None:
        if not passphrase:
            raise SealError("a passphrase or key is required")
        key = hashlib.scrypt(
            passphrase.encode("utf-8"), salt=salt,
            n=envelope.get("n", _N), r=envelope.get("r", _R),
            p=envelope.get("p", _P), dklen=_DKLEN,
        )
    aad = aad_name.encode("ascii")
    try:
        plaintext = AESGCM(key).decrypt(nonce, ct, aad)
    except InvalidTag:
        raise SealError("wrong passphrase or corrupted seal")
    return json.loads(plaintext.decode("utf-8")).get("files", {}), key


def open_sealed_store(
    folder: str | Path, *,
    passphrase: str | None = None, key: bytes | None = None,
    log_root: str | Path | None = None,
) -> tuple[dict[str, bytes], bytes]:
    """Decrypt a sealed folder's store into memory; return ``({relpath: bytes}, key)``.

    The on-disk ``.sealed`` blob is left untouched and nothing is written to
    disk. Pass ``passphrase`` (first unlock) or a cached ``key`` (session
    serving). The returned key is what the session caches.
    """
    log_dir = _resolve_log_dir(folder, log_root)
    sealed = _sealed_path(log_dir)
    if not sealed.exists():
        raise SealError(f"not sealed: {sealed} does not exist")
    files, used_key = _open_sealed(sealed, log_dir.name, passphrase=passphrase, key=key)
    return {rel: base64.b64decode(b64) for rel, b64 in files.items()}, used_key


def read_through(
    folder: str | Path,
    *,
    passphrase: str,
    log_root: str | Path | None = None,
) -> dict[str, bytes]:
    """Decrypt a SEALED folder's memory **into memory** and return ``{relpath: bytes}``.

    This is the Workspace Lock "sealed but served" read path: Workspaces can answer from a
    sealed workspace (run ``verify_chain``, serve pairs) **without unsealing it** —
    the on-disk store stays ciphertext (the ``.sealed`` blob is left untouched)
    and **nothing is written to disk**. The decrypted bytes live only in the
    returned mapping, for the caller to use transiently and drop.

    Raises :class:`SealError` if the folder is not sealed or the passphrase is
    wrong (the folder-hash AAD makes a wrong key fail cleanly, not silently).
    """
    if not passphrase:
        raise SealError("a passphrase is required to read through a sealed workspace")
    mapping, _ = open_sealed_store(folder, passphrase=passphrase, log_root=log_root)
    return mapping


def read_through_file(
    folder: str | Path,
    relpath: str,
    *,
    passphrase: str,
    log_root: str | Path | None = None,
) -> bytes:
    """Read one file (e.g. ``"events.jsonl"``) from a sealed workspace, in memory."""
    data = read_through(folder, passphrase=passphrase, log_root=log_root)
    if relpath not in data:
        raise SealError(f"{relpath!r} is not in the sealed store")
    return data[relpath]
