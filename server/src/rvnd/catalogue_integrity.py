# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Catalogue integrity primitives — checksums + HMAC + audit-sidecar.

The Workspace MCP skill catalogue lives at::

    <workspace>/plugin/references/skill-companions.json

A malicious actor with write access to that file could silently introduce
fake plugins, redirect dispatch to compromised code, or weaken Privacy
Lock's allow-list. This module adds two integrity controls:

1. **Per-plugin checksums**. The catalogue's ``integrity.checksums.<plugin_id>``
   block records SHA-256 of the plugin's ``plugin.json`` + every ``SKILL.md``
   at the time of registration. ``verify_plugin_integrity()`` recomputes
   them at load time and fails if a SKILL.md was modified post-registration.

2. **Catalogue-level HMAC-SHA256**. ``integrity.hmac`` is a MAC over the
   canonical JSON of ``{version, families, integrity.checksums}`` using a
   per-installation secret at ``~/.workspace/catalogue-hmac.key`` (mode 0600).
   Tampering with the catalogue without re-signing fails ``verify_catalogue()``.

3. **Sidecar audit log**. Every mutation of the catalogue writes one JSONL
   line to ``<catalogue_dir>/catalogue-mutations.jsonl`` recording the
   actor, action, before/after HMACs, plugin_id, and reason. The sidecar
   is append-only.

**Threat model.** Defends against local-disk tampering (an attacker with
write access can modify the catalogue but cannot forge the HMAC without
the per-installation key). Does NOT defend against a local attacker who
also has the HMAC key — for that level of defence the secret has to leave
the machine, which is the territory of the future Ed25519 tenant-keypair
layer.

**Modes.** Set ``WORKSPACE_CATALOGUE_MODE`` to one of:

* ``legacy``  — no verification (deprecated; suppresses warnings)
* ``warn``    — verify, log warnings on failure, do NOT block dispatch (default)
* ``enforce`` — verify, hard-fail on integrity violation

Run levels matter: ``enforce`` is for production / publicly-distributed
catalogues. ``warn`` is the right default for a single-author setup where
the catalogue is authored locally and tampering is unlikely but worth
detecting.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INTEGRITY_VERSION = 1
HMAC_ALG = "HMAC-SHA256"
DEFAULT_SECRET_PATH = Path.home() / ".workspace" / "catalogue-hmac.key"

MODE_LEGACY = "legacy"
MODE_WARN = "warn"
MODE_ENFORCE = "enforce"
VALID_MODES = {MODE_LEGACY, MODE_WARN, MODE_ENFORCE}


# ---------------------------------------------------------------------------
# Verification result
# ---------------------------------------------------------------------------

@dataclass
class IntegrityResult:
    """Outcome of an integrity check."""

    ok: bool
    mode: str
    catalogue_hmac_ok: Optional[bool]            # None = no HMAC present (legacy)
    plugin_results: dict[str, dict[str, Any]]    # plugin_id → {ok, errors[]}
    warnings: list[str]                          # human-readable
    errors: list[str]                            # human-readable (only populated in enforce)

    @property
    def has_integrity_block(self) -> bool:
        return self.catalogue_hmac_ok is not None


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------

def current_mode() -> str:
    """Return the active integrity mode from WORKSPACE_CATALOGUE_MODE."""
    m = (os.environ.get("WORKSPACE_CATALOGUE_MODE") or MODE_WARN).strip().lower()
    if m not in VALID_MODES:
        logger.warning(
            "WORKSPACE_CATALOGUE_MODE=%r is not one of %s; defaulting to 'warn'",
            m, sorted(VALID_MODES),
        )
        return MODE_WARN
    return m


# ---------------------------------------------------------------------------
# HMAC secret management
# ---------------------------------------------------------------------------

class CatalogueSecretError(RuntimeError):
    """The catalogue HMAC secret is absent, unsafe, or unreadable."""


class CatalogueSecretMissingError(CatalogueSecretError):
    """No catalogue HMAC secret exists at the configured path."""


def _validate_secret_parent(p: Path) -> None:
    """Require a current-user-owned parent that cannot be modified by peers."""
    try:
        info = p.parent.stat()
    except OSError as exc:
        raise CatalogueSecretError(
            f"catalogue HMAC secret parent cannot be inspected at {p.parent}: {exc}"
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise CatalogueSecretError(
            f"catalogue HMAC secret parent is not a directory at {p.parent}"
        )
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise CatalogueSecretError(
            f"catalogue HMAC secret parent must be owner-only writable at {p.parent}"
        )


def _read_secret(secret_path: Optional[Path] = None) -> bytes:
    """Read and validate the canonical secret without changing the filesystem."""
    p = secret_path if secret_path is not None else DEFAULT_SECRET_PATH
    if os.name != "posix":
        raise CatalogueSecretError(
            "catalogue HMAC secret ACL verification is unavailable on this platform"
        )
    _validate_secret_parent(p)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(p, flags)
    except FileNotFoundError as exc:
        raise CatalogueSecretMissingError(
            f"catalogue HMAC secret is missing at {p}"
        ) from exc
    except OSError as exc:
        raise CatalogueSecretError(f"catalogue HMAC secret cannot be opened at {p}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CatalogueSecretError(
                f"catalogue HMAC secret is not a regular file at {p}"
            )
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise CatalogueSecretError(
                f"catalogue HMAC secret must be current-user owned with mode 0600 at {p}"
            )
        with os.fdopen(fd, "rb", closefd=True) as stream:
            fd = -1
            data = stream.read(33)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) != 32:
        raise CatalogueSecretError(
            f"catalogue HMAC secret must be exactly 32 bytes at {p}"
        )
    return data

def _ensure_secret(secret_path: Optional[Path] = None) -> bytes:
    """Return the secret, atomically creating it on first signing use.

    Existing invalid secrets are never replaced. Verification uses
    :func:`_read_secret` and therefore never creates filesystem state.
    """
    p = secret_path if secret_path is not None else DEFAULT_SECRET_PATH
    if p.exists():
        return _read_secret(p)
    if os.name != "posix":
        raise CatalogueSecretError(
            "automatic catalogue HMAC secret creation requires verified POSIX permissions"
        )
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _validate_secret_parent(p)
    new_secret = secrets.token_bytes(32)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{p.name}.", dir=p.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            fd = -1
            stream.write(new_secret)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # A hard link publishes a fully-written file without replacing a
            # concurrent winner. Both names refer to the same inode.
            os.link(tmp, p)
        except FileExistsError:
            # Another creator atomically published the canonical winner.
            pass
        dir_fd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        raise CatalogueSecretError(
            f"catalogue HMAC secret could not be created at {p}: {exc}"
        ) from exc
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            tmp.unlink()
        except FileNotFoundError:
            # Cleanup is idempotent if an external cleanup already removed it.
            pass
    logger.info("catalogue HMAC secret is available at %s", p)
    return _read_secret(p)


# ---------------------------------------------------------------------------
# Canonical JSON for HMAC input
# ---------------------------------------------------------------------------

def _canonical_json(obj: Any) -> bytes:
    """Stable serialisation for MAC input: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _hmac_payload(catalogue: dict[str, Any]) -> bytes:
    """The bytes that get MACed: {version, families, integrity.checksums}.

    Note that `integrity.hmac` and `integrity.signed_at` are EXCLUDED from
    the MAC input (they ARE the MAC) — otherwise we'd have a chicken-and-egg
    problem.
    """
    families = catalogue.get("families") or {}
    integrity = catalogue.get("integrity") or {}
    checksums = integrity.get("checksums") or {}
    payload = {
        "version":   int(catalogue.get("version") or 1),
        "families":  families,
        "checksums": checksums,
    }
    return _canonical_json(payload)


def compute_hmac(catalogue: dict[str, Any],
                 secret_path: Optional[Path] = None) -> str:
    """Compute HMAC-SHA256 for the catalogue. Returns hex digest."""
    secret = _ensure_secret(secret_path)
    payload = _hmac_payload(catalogue)
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_hmac(catalogue: dict[str, Any],
                secret_path: Optional[Path] = None) -> bool:
    """Return True iff catalogue.integrity.hmac matches computed HMAC.

    Returns False if integrity block or hmac field is missing. Use
    ``current_mode()`` to decide whether that's fatal.
    """
    integrity = catalogue.get("integrity") or {}
    expected = integrity.get("hmac") or ""
    if not expected:
        return False
    secret = _read_secret(secret_path)
    actual = hmac.new(secret, _hmac_payload(catalogue), hashlib.sha256).hexdigest()
    # constant-time compare
    return hmac.compare_digest(expected, actual)


# ---------------------------------------------------------------------------
# Per-plugin checksums
# ---------------------------------------------------------------------------

def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def compute_plugin_checksums(plugin_root: str | Path) -> dict[str, Any]:
    """Compute integrity checksums for a plugin.

    Walks the plugin root for ``plugin.json`` (under ``.claude-plugin/``)
    and every ``SKILL.md`` under ``skills/<name>/``. Returns:

        {
          "plugin_root_resolved": "<absolute>",
          "files": {
            "plugin.json":               "sha256:<hex>",
            "skills/<name>/SKILL.md":    "sha256:<hex>",
            ...
          }
        }

    Plugin authors call this from their install/register scripts and write
    the result into the catalogue's ``integrity.checksums.<plugin_id>``.
    """
    root = Path(plugin_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"plugin root is not a directory: {root}")

    files: dict[str, str] = {}

    # plugin.json — under .claude-plugin/
    pj = root / ".claude-plugin" / "plugin.json"
    if pj.exists():
        files[".claude-plugin/plugin.json"] = _sha256_file(pj)

    # SKILL.md files — under skills/<name>/SKILL.md
    skills_dir = root / "skills"
    if skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir()):
            sm = skill_dir / "SKILL.md"
            if sm.is_file():
                rel = f"skills/{skill_dir.name}/SKILL.md"
                files[rel] = _sha256_file(sm)

    return {
        "plugin_root_resolved": str(root),
        "files":                files,
    }


def verify_plugin_integrity(plugin_id: str,
                             catalogue: dict[str, Any]) -> dict[str, Any]:
    """Verify that a plugin's files on disk match the catalogue's recorded
    checksums.

    Returns ``{ok: bool, errors: [str, ...], skipped: bool}``. ``skipped``
    is True when the catalogue has no checksums block for this plugin
    (legacy entry) — caller decides how to treat that based on mode.
    """
    integrity = catalogue.get("integrity") or {}
    checksums = integrity.get("checksums") or {}
    entry = checksums.get(plugin_id) or {}
    if not entry:
        return {"ok": True, "errors": [], "skipped": True}

    errors: list[str] = []
    root = Path(entry.get("plugin_root_resolved") or "")
    if not root.exists():
        return {
            "ok": False,
            "errors": [f"plugin_root_resolved does not exist on disk: {root}"],
            "skipped": False,
        }

    files: dict[str, str] = entry.get("files") or {}
    actual_files = compute_plugin_checksums(root)["files"]
    unexpected = sorted(set(actual_files) - set(files))
    for rel in unexpected:
        errors.append(f"unregistered integrity-relevant file: {rel}")
    for rel, expected in files.items():
        target = root / rel
        if not target.exists():
            errors.append(f"missing file (was registered): {rel}")
            continue
        actual = _sha256_file(target)
        if actual != expected:
            errors.append(
                f"checksum mismatch for {rel}: expected {expected[:20]}…, "
                f"got {actual[:20]}…"
            )

    return {"ok": not errors, "errors": errors, "skipped": False}


# ---------------------------------------------------------------------------
# Full catalogue verification
# ---------------------------------------------------------------------------

def verify_catalogue(catalogue: dict[str, Any],
                     secret_path: Optional[Path] = None,
                     mode: Optional[str] = None) -> IntegrityResult:
    """Run all integrity checks against a loaded catalogue.

    The returned ``IntegrityResult.ok`` reflects whether the catalogue
    should be trusted at the CURRENT mode level:

    - ``legacy``  → always ok=True; no checks run; result.warnings empty
    - ``warn``    → ok=True; failures populate ``warnings``
    - ``enforce`` → ok reflects actual integrity; failures populate ``errors``
    """
    effective_mode = (mode or current_mode())
    warnings: list[str] = []
    errors: list[str] = []
    plugin_results: dict[str, dict[str, Any]] = {}

    integrity = catalogue.get("integrity") or {}
    has_integrity = bool(integrity.get("hmac"))

    if effective_mode == MODE_LEGACY:
        return IntegrityResult(
            ok=True, mode=effective_mode,
            catalogue_hmac_ok=None,
            plugin_results={}, warnings=[], errors=[],
        )

    # HMAC check
    cat_hmac_ok: Optional[bool] = None
    if has_integrity:
        try:
            cat_hmac_ok = verify_hmac(catalogue, secret_path)
        except CatalogueSecretError as exc:
            cat_hmac_ok = False
            msg = f"catalogue HMAC verification unavailable: {exc}"
            (errors if effective_mode == MODE_ENFORCE else warnings).append(msg)
        if not cat_hmac_ok:
            msg = ("catalogue HMAC verification failed — file may have been "
                   "modified without re-signing")
            (errors if effective_mode == MODE_ENFORCE else warnings).append(msg)
    else:
        msg = ("catalogue has no integrity block — running in unverified mode "
               "(set WORKSPACE_CATALOGUE_MODE=enforce after migrating)")
        (errors if effective_mode == MODE_ENFORCE else warnings).append(msg)

    # Per-plugin checksums
    families = catalogue.get("families") or {}
    for plugin_id in sorted(families.keys()):
        res = verify_plugin_integrity(plugin_id, catalogue)
        plugin_results[plugin_id] = res
        if res["skipped"]:
            if effective_mode == MODE_ENFORCE:
                errors.append(f"plugin '{plugin_id}' has no recorded checksums")
            continue
        if not res["ok"]:
            msg = f"plugin '{plugin_id}' integrity failed: " + "; ".join(res["errors"])
            (errors if effective_mode == MODE_ENFORCE else warnings).append(msg)

    ok = (effective_mode != MODE_ENFORCE) or (not errors)

    return IntegrityResult(
        ok=ok, mode=effective_mode,
        catalogue_hmac_ok=cat_hmac_ok,
        plugin_results=plugin_results,
        warnings=warnings, errors=errors,
    )


# ---------------------------------------------------------------------------
# Sign + write
# ---------------------------------------------------------------------------

def sign_and_save_catalogue(catalogue: dict[str, Any],
                            catalogue_path: str | Path,
                            secret_path: Optional[Path] = None) -> dict[str, Any]:
    """Compute HMAC, embed integrity block, write atomically.

    Returns the catalogue dict that was written (with integrity block).
    """
    integrity = catalogue.setdefault("integrity", {})
    integrity["version"] = INTEGRITY_VERSION
    integrity["hmac_alg"] = HMAC_ALG
    integrity["signed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # checksums are populated by callers BEFORE sign_and_save; we don't
    # touch them here. compute_hmac reads them from the catalogue.
    integrity["hmac"] = compute_hmac(catalogue, secret_path)

    path = Path(catalogue_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(catalogue, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return catalogue


# ---------------------------------------------------------------------------
# Mutation audit-log sidecar
# ---------------------------------------------------------------------------

def _audit_sidecar_path(catalogue_path: str | Path) -> Path:
    """Sidecar location next to the catalogue file."""
    return Path(catalogue_path).expanduser().resolve().parent / "catalogue-mutations.jsonl"


def append_mutation_audit(catalogue_path: str | Path,
                           *,
                           action: str,
                           plugin_id: str,
                           actor: str,
                           reason: str,
                           before_hmac: str = "",
                           after_hmac: str = "") -> dict[str, Any]:
    """Append one JSONL line recording a catalogue mutation.

    ``action`` is one of ``"add" | "update" | "remove"``. Sidecar lines
    are append-only and human-readable. They are intentionally separate from
    the signed workspace event lookup exposed by ``get_audit_event``.
    """
    if action not in {"add", "update", "remove"}:
        raise ValueError(f"unknown mutation action: {action!r}")
    entry = {
        "ts":           time.time(),
        "ts_iso":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action":       action,
        "plugin_id":    plugin_id,
        "actor":        actor or "system",
        "reason":       reason or "",
        "before_hmac":  before_hmac or "",
        "after_hmac":   after_hmac or "",
    }
    p = _audit_sidecar_path(catalogue_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_mutation_audit(catalogue_path: str | Path,
                         limit: int = 50) -> list[dict[str, Any]]:
    """Read the most-recent mutation-audit entries (newest first)."""
    p = _audit_sidecar_path(catalogue_path)
    if not p.exists():
        return []
    lines: list[dict[str, Any]] = []
    with open(p, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    lines.reverse()
    return lines[:limit]
