# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Backup & restore of the RVND home (``~/.workspace``).

This directory is the irreplaceable governance record: the Ed25519 signing keys,
the per-folder tamper-evident audit chains, the workspace registry, and the
catalogue HMAC key. Lose it and the signed history is gone. This module captures
the whole tree into one portable archive and restores it.

Design notes
------------
- **What travels:** the entire ``~/.workspace`` tree, including *every* per-host
  key subdir. Chains pin to a genesis key *fingerprint*, so as long as the key
  material is in the archive the chains verify anywhere — the backup is
  host-portable. On a *new* host RVND still mints its own identity for future
  writes (host divergence is intended and shows up in the audit trail); restore
  gives you full read/verify access to the old record, not a forged same-host
  identity.
- **Encryption is optional but recommended:** the archive contains *private
  keys*. With a passphrase it is sealed with the same vetted primitive as
  ``seal`` (scrypt + AES-256-GCM). Without one it is a plaintext tar written
  ``0600`` — fine for an already-secure location, dangerous if it leaves the box.
- **Restore is safe by construction:** archive members are path-checked against
  traversal, and an existing home is never silently overwritten — it is moved
  aside to a timestamped ``.bak`` unless the caller opts out.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .seal import encrypt_record, decrypt_record, _REC_MAGIC

# A fixed associated-data label so encrypt/decrypt agree (a backup is not
# folder-scoped, unlike a sealed workspace record).
BACKUP_AAD = "rvnd-backup"
_MANIFEST_NAME = "MANIFEST.json"
_TREE_PREFIX = "workspace"          # arcname root for the ~/.workspace tree
_SCHEMA = "rvnd-backup/1"


class BackupError(RuntimeError):
    """A backup could not be created, read, or restored."""


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rvnd_version() -> str:
    from importlib.metadata import PackageNotFoundError, version
    for dist in ("workspaces", "rvnd"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return "unknown"


def _host_info() -> dict[str, str]:
    """Best-effort host identity for the manifest — never fatal."""
    info: dict[str, str] = {}
    try:
        import socket
        info["hostname"] = socket.gethostname()
    except Exception:  # noqa: BLE001
        pass  # hostname is best-effort manifest metadata — omit it if unavailable
    try:
        from . import signing
        info["host_id"] = signing._host_id()
        fp = signing.identity_fingerprint_or_none()
        if fp:
            info["identity_fingerprint"] = fp
    except Exception:  # noqa: BLE001 — signing may be unavailable; manifest is informational
        pass
    return info


def _tree_stats(home: Path) -> tuple[int, int]:
    files = 0
    total = 0
    for p in home.rglob("*"):
        if p.is_file():
            files += 1
            try:
                total += p.stat().st_size
            except OSError:
                pass  # a file that vanished mid-walk just doesn't count toward the total
    return files, total


def _is_encrypted(blob: bytes) -> bool:
    return blob[:len(_REC_MAGIC)] == _REC_MAGIC


def is_encrypted_archive(path: Path | str) -> bool:
    """True if the archive is passphrase-encrypted — read only the magic bytes."""
    with open(path, "rb") as f:
        return _is_encrypted(f.read(len(_REC_MAGIC)))


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------

def create_backup(home: Path, out_path: Path, *,
                  passphrase: Optional[str] = None) -> dict[str, Any]:
    """Archive ``home`` (``~/.workspace``) to ``out_path``.

    With ``passphrase`` the archive is encrypted (scrypt + AES-256-GCM); without
    it, it is a plaintext tar.gz written ``0600``. Returns a summary dict.
    """
    home = Path(home)
    if not home.is_dir():
        raise BackupError(f"nothing to back up: {home} does not exist "
                          "(run `workspaces init` first)")

    files, total = _tree_stats(home)
    manifest = {
        "schema":     _SCHEMA,
        "created_at": _now_iso(),
        "rvnd_version": _rvnd_version(),
        "encrypted":  bool(passphrase),
        "file_count": files,
        "total_bytes": total,
        **_host_info(),
    }
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")

    # Build the tar.gz in memory: MANIFEST.json at the root + the tree under
    # ``workspace/``. reproducible-ish (sorted) for stable archives.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        ti = tarfile.TarInfo(_MANIFEST_NAME)
        ti.size = len(manifest_bytes)
        ti.mtime = 0
        tar.addfile(ti, io.BytesIO(manifest_bytes))
        tar.add(str(home), arcname=_TREE_PREFIX, recursive=True)
    data = buf.getvalue()

    if passphrase:
        data = encrypt_record(data, passphrase=passphrase, folder=BACKUP_AAD)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Write private-key-bearing bytes with tight permissions from the start.
    fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)

    manifest["archive"] = str(out_path)
    manifest["archive_bytes"] = len(data)
    return manifest


# --------------------------------------------------------------------------
# Read / restore
# --------------------------------------------------------------------------

def _load_tar_bytes(archive: Path, passphrase: Optional[str]) -> bytes:
    raw = Path(archive).read_bytes()
    if _is_encrypted(raw):
        if not passphrase:
            raise BackupError("this backup is encrypted — a passphrase is required.")
        try:
            return decrypt_record(raw, passphrase=passphrase, folder=BACKUP_AAD)
        except Exception as e:  # noqa: BLE001 — SealError etc. → one clear message
            raise BackupError("could not decrypt — wrong passphrase or corrupt archive.") from e
    # A passphrase supplied for a plaintext archive is simply ignored.
    return raw


def read_manifest(archive: Path, *, passphrase: Optional[str] = None) -> dict[str, Any]:
    """Read and return the archive's MANIFEST.json without extracting the tree."""
    data = _load_tar_bytes(archive, passphrase)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            m = tar.extractfile(_MANIFEST_NAME)
            if m is None:
                raise BackupError("not an RVND backup: MANIFEST.json is missing.")
            return json.loads(m.read().decode("utf-8"))
    except tarfile.TarError as e:
        raise BackupError(f"not a readable RVND backup archive: {e}") from e


def _safe_tree_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Members under ``workspace/`` only, rejecting path traversal / absolute
    paths / symlinks / devices — a malicious archive must not escape the target."""
    safe: list[tarfile.TarInfo] = []
    for m in tar.getmembers():
        name = m.name
        if name == _MANIFEST_NAME:
            continue
        if not (name == _TREE_PREFIX or name.startswith(_TREE_PREFIX + "/")):
            continue
        rel = name[len(_TREE_PREFIX):].lstrip("/")
        # Reject absolute, parent-traversal, or non-regular special members.
        if os.path.isabs(rel) or ".." in Path(rel).parts:
            raise BackupError(f"unsafe path in archive: {name!r} — refusing to extract.")
        if m.issym() or m.islnk() or m.isdev():
            raise BackupError(f"unsafe member in archive: {name!r} (link/device).")
        safe.append(m)
    return safe


def restore_backup(archive: Path, home: Path, *,
                   passphrase: Optional[str] = None,
                   force: bool = False,
                   dry_run: bool = False) -> dict[str, Any]:
    """Restore ``archive`` into ``home`` (``~/.workspace``).

    An existing non-empty ``home`` is moved aside to ``<home>.bak-<ts>`` (never
    overwritten in place); without ``force`` that move is refused so the caller
    must opt in. ``dry_run`` reads the manifest and validates members but writes
    nothing. Returns a summary dict.
    """
    home = Path(home)
    data = _load_tar_bytes(archive, passphrase)

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        mf = tar.extractfile(_MANIFEST_NAME)
        manifest = json.loads(mf.read().decode("utf-8")) if mf else {}
        members = _safe_tree_members(tar)  # validates before we touch anything

        existing = home.exists() and any(home.iterdir())
        result = {
            "manifest": manifest,
            "members": len(members),
            "target": str(home),
            "existing": existing,
        }
        if dry_run:
            result["dry_run"] = True
            return result
        if existing and not force:
            raise BackupError(
                f"{home} already exists and is not empty. Re-run with force to "
                "restore (the current one is moved aside to a .bak, not deleted).")

        moved_to = None
        if existing:
            ts = _now_iso().replace(":", "").replace("-", "")
            moved_to = home.with_name(home.name + ".bak-" + ts)
            shutil.move(str(home), str(moved_to))
        home.mkdir(parents=True, exist_ok=True)

        for m in members:
            rel = m.name[len(_TREE_PREFIX):].lstrip("/")
            if not rel:
                continue
            dest = home / rel
            if m.isdir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            if src is None:
                continue
            with open(dest, "wb") as fh:
                shutil.copyfileobj(src, fh)
            # Preserve private-key secrecy: keys/** stay owner-only.
            if rel.startswith("keys/") or rel.endswith((".priv", ".key")):
                os.chmod(dest, 0o600)

        result["moved_existing_to"] = str(moved_to) if moved_to else None
        return result
