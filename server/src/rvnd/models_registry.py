# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Local-model registry — ties pulled GGUF/ONNX models to Workspace roles.

The registry is a small JSON file at ``~/.workspace/models/registry.json``
(override via ``WORKSPACE_MODELS_DIR`` env var). It records which models
have been pulled to disk and which Workspace role each one serves.

The pulled-model bytes themselves live alongside the registry under
``~/.workspace/models/<id>/<filename>``. This module manages only the
metadata layer — an external model-packaging plugin supplies the download
primitive (the core does not bundle one; bring your own model).

Schema (registry.json)::

    {
      "schema_version": 1,
      "models": {
        "phi-3.5-mini-q4": {
          "artifact_path": "/Users/x/.workspace/models/phi-3.5-mini-q4/...gguf",
          "sha256_verified": "...",
          "registered_at": "2026-05-27T20:00:00Z",
          "registered_via": "register",     # "pull" | "register" | "offline"
          "roles": ["validator", "lock-tier-C"]
        }
      },
      "role_map": {
        "validator": {"order_n1": "phi-3.5-mini-q4", "order_n2": "qwen-..."},
        ...
      }
    }

Role-slot keys
--------------
The role_map slot keys are positional: ``order_n1``, ``order_n2``, ``order_n3``,
… The caller chooses any order policy it likes (round-robin, n1-first with
fallback to n2, ensemble all of them, etc.). These keys are NOT quality
verdicts and do NOT imply preference.

The legacy keys ``primary`` / ``backup`` are still accepted on read for
back-compat (with a one-time deprecation warning per registry file). Writes
always use the new ``order_n1`` / ``order_n2`` form. Legacy-key support is
scheduled for removal in 0.7.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Module-level logger; back-compat warning fires once per registry-file load
# when legacy ``primary`` / ``backup`` keys are seen.
_log = logging.getLogger(__name__)
_LEGACY_KEYS_WARNED: set[str] = set()


# Public constants — callers and tests reach for these by name so the slot
# vocabulary is documented in one place.
ROLE_SLOT_N1 = "order_n1"
ROLE_SLOT_N2 = "order_n2"
LEGACY_ROLE_SLOT_N1 = "primary"   # legacy alias; removed in 0.7
LEGACY_ROLE_SLOT_N2 = "backup"    # legacy alias; removed in 0.7


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MODELS_DIR_ENV = "WORKSPACE_MODELS_DIR"
DEFAULT_MODELS_DIR = Path.home() / ".workspace" / "models"
REGISTRY_FILENAME = "registry.json"

VALID_ROLES = (
    "workspace",    # the workspace's ONE standard local model (default cascade local tier)
    "validator", "lock-tier-C", "lock-tier-c",
    "lock-c",  # 0.6.8.1: short alias for lock Tier C (used by resolver)
    "drafter", "code-fix",   # code-fix = a coding companion's own model
)


def models_dir() -> Path:
    override = os.environ.get(MODELS_DIR_ENV)
    return Path(override) if override else DEFAULT_MODELS_DIR


def registry_path() -> Path:
    return models_dir() / REGISTRY_FILENAME


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ModelRegistryError(RuntimeError):
    """Raised on invalid registry operations."""


class ModelNotFoundError(ModelRegistryError):
    """The requested model id isn't in the registry."""


class InvalidRoleError(ModelRegistryError):
    """The role string isn't one of the allowed roles."""


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass
class ModelEntry:
    id: str
    artifact_path: str = ""
    sha256_verified: str = ""
    registered_at: str = ""
    registered_via: str = "register"  # "pull" | "register" | "offline"
    roles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_path": self.artifact_path,
            "sha256_verified": self.sha256_verified,
            "registered_at": self.registered_at,
            "registered_via": self.registered_via,
            "roles": list(self.roles),
        }


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------


def _migrate_legacy_role_slot_keys(
    role_map: dict[str, Any], source_label: str,
) -> bool:
    """Translate legacy ``primary`` / ``backup`` slot keys to ``order_n1`` /
    ``order_n2`` in-place. Returns True if any migration happened.

    Emits a one-time deprecation warning per ``source_label`` (the registry
    file path, the toml path, etc.) so noisy environments don't spam the log
    on every load. Legacy-key support is scheduled for removal in 0.7.
    """
    if not isinstance(role_map, dict):
        return False
    migrated = False
    for _role, slot in role_map.items():
        if not isinstance(slot, dict):
            continue
        if LEGACY_ROLE_SLOT_N1 in slot and ROLE_SLOT_N1 not in slot:
            slot[ROLE_SLOT_N1] = slot.pop(LEGACY_ROLE_SLOT_N1)
            migrated = True
        elif LEGACY_ROLE_SLOT_N1 in slot:
            # Both present — drop the legacy one, prefer the new name.
            slot.pop(LEGACY_ROLE_SLOT_N1, None)
            migrated = True
        if LEGACY_ROLE_SLOT_N2 in slot and ROLE_SLOT_N2 not in slot:
            slot[ROLE_SLOT_N2] = slot.pop(LEGACY_ROLE_SLOT_N2)
            migrated = True
        elif LEGACY_ROLE_SLOT_N2 in slot:
            slot.pop(LEGACY_ROLE_SLOT_N2, None)
            migrated = True
    if migrated and source_label not in _LEGACY_KEYS_WARNED:
        _LEGACY_KEYS_WARNED.add(source_label)
        _log.warning(
            "models_registry: legacy role-slot keys 'primary'/'backup' "
            "detected at %s; treating as 'order_n1'/'order_n2'. Update the "
            "source to the new keys — legacy support is removed in 0.7.",
            source_label,
        )
    return migrated


def load_registry() -> dict[str, Any]:
    """Read registry.json; return the empty schema if missing.

    Also performs a one-shot migration of legacy ``primary`` / ``backup``
    role-slot keys to the neutral positional ``order_n1`` / ``order_n2`` form
    (see module docstring). The migration is in-memory only on read; the
    on-disk file is rewritten in the new form the next time ``save_registry``
    runs (i.e. on the next ``register_model`` call).
    """
    path = registry_path()
    if not path.exists():
        return {
            "schema_version": 1,
            "models": {},
            "role_map": {},
        }
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise ModelRegistryError(f"unable to read registry at {path}: {e}") from e

    # Defensive defaults
    data.setdefault("schema_version", 1)
    data.setdefault("models", {})
    data.setdefault("role_map", {})
    # Back-compat: translate legacy slot keys in place. Warns once per file.
    _migrate_legacy_role_slot_keys(data["role_map"], str(path))
    return data


def save_registry(data: dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def list_models() -> list[ModelEntry]:
    """Return every registered model as a ModelEntry."""
    data = load_registry()
    entries: list[ModelEntry] = []
    for mid, m in sorted(data["models"].items()):
        entries.append(ModelEntry(
            id=mid,
            artifact_path=m.get("artifact_path", ""),
            sha256_verified=m.get("sha256_verified", ""),
            registered_at=m.get("registered_at", ""),
            registered_via=m.get("registered_via", "register"),
            roles=list(m.get("roles", [])),
        ))
    return entries


def models_for_role(role: str) -> list[str]:
    """Return the list of model ids registered for ``role``.

    Pure read against the registry — never raises. Order: role-map slot
    ``order_n1`` first, ``order_n2`` second, then any additional models whose
    per-model ``roles`` list includes the role (registry-edit-by-hand for >2
    model setups). Empty list if no models are registered for the role or if
    the registry doesn't exist yet.

    The slot keys are positional, NOT preferential — see module docstring. The
    legacy ``primary`` / ``backup`` keys are migrated to the new names on read
    by ``load_registry``; this function never sees the legacy form.

    Matching is **strict** on the role string — callers asking for
    ``"lock-c"`` will not pick up entries registered under the longer
    ``"lock-tier-C"`` form and vice versa. This is intentional: the two
    roles map onto different consumers (the Tier C ensemble vs. the older
    in-process tier_c path), and silent aliasing would muddle the boundary.
    """
    try:
        data = load_registry()
    except ModelRegistryError:
        return []
    role_slot = data.get("role_map", {}).get(role, {})
    out: list[str] = []
    slot_n1 = role_slot.get(ROLE_SLOT_N1) if isinstance(role_slot, dict) else ""
    slot_n2 = role_slot.get(ROLE_SLOT_N2) if isinstance(role_slot, dict) else ""
    if slot_n1:
        out.append(slot_n1)
    if slot_n2 and slot_n2 not in out:
        out.append(slot_n2)
    # Fold in any extra models whose per-model roles list includes this role
    # but which aren't in the role-map slot (manual edits, future >2 setups).
    for mid, m in sorted(data.get("models", {}).items()):
        if role in m.get("roles", []) and mid not in out:
            out.append(mid)
    return out


def register_model(
    model_id: str,
    role: str,
    *,
    artifact_path: str = "",
    sha256: str = "",
    via: str = "register",
) -> ModelEntry:
    """Add (or update) a model entry and tie it to a role.

    If the same id is already registered, append the role; otherwise create a
    new entry. The role_map is updated so that the registered model takes the
    first free positional slot (``order_n1`` then ``order_n2``). Slot order is
    positional, not preferential — see module docstring.
    """
    role_canon = _canonicalise_role(role)
    data = load_registry()
    models = data["models"]
    if model_id not in models:
        models[model_id] = {
            "artifact_path": artifact_path,
            "sha256_verified": sha256,
            "registered_at": _iso_now(),
            "registered_via": via,
            "roles": [role_canon],
        }
    else:
        existing = models[model_id]
        if role_canon not in existing.get("roles", []):
            existing.setdefault("roles", []).append(role_canon)
        if artifact_path:
            existing["artifact_path"] = artifact_path
        if sha256:
            existing["sha256_verified"] = sha256
        existing["registered_at"] = _iso_now()
        existing["registered_via"] = via

    # Update role map. Slots are positional (order_n1, order_n2, ...), not
    # quality verdicts: the first free slot gets filled.
    role_map = data["role_map"]
    role_slot = role_map.setdefault(
        role_canon, {ROLE_SLOT_N1: "", ROLE_SLOT_N2: ""},
    )
    # Defensive: a hand-edited slot may still carry the new keys missing.
    role_slot.setdefault(ROLE_SLOT_N1, "")
    role_slot.setdefault(ROLE_SLOT_N2, "")
    if not role_slot.get(ROLE_SLOT_N1):
        role_slot[ROLE_SLOT_N1] = model_id
    elif role_slot[ROLE_SLOT_N1] != model_id and not role_slot.get(ROLE_SLOT_N2):
        role_slot[ROLE_SLOT_N2] = model_id
    # If both order_n1 AND order_n2 are set and neither is this model, we leave
    # things alone — caller can edit registry.json manually for non-default
    # multi-model setups.

    save_registry(data)

    m = models[model_id]
    return ModelEntry(
        id=model_id,
        artifact_path=m.get("artifact_path", ""),
        sha256_verified=m.get("sha256_verified", ""),
        registered_at=m.get("registered_at", ""),
        registered_via=m.get("registered_via", "register"),
        roles=list(m.get("roles", [])),
    )


def _probe_endpoint(model_id: str) -> dict[str, Any]:
    """Probe ``WORKSPACE_LOCAL_LLM_URL`` for ``model_id``.

    Returns a structured probe result:

    - ``endpoint_reachable=None`` when no URL is configured (not applicable).
    - ``endpoint_reachable=True`` when the URL answers ``GET /v1/models`` and
      the response's ``data[]`` includes a row with ``id == model_id``.
    - ``endpoint_reachable=False`` when the URL is unreachable, returns a
      malformed response, or returns a model list missing the queried model.

    Uses stdlib ``urllib`` so this stays dependency-light (same posture as
    ``rvnd.local_llm``).
    """
    import json as _json
    import urllib.error
    import urllib.request

    url_env = "WORKSPACE_LOCAL_LLM_URL"
    base = os.environ.get(url_env)
    if not base:
        return {
            "endpoint_reachable": None,
            "endpoint_url": "",
            "error": "no endpoint configured",
        }

    models_url = base.rstrip("/") + "/models"
    timeout = 5.0
    try:
        timeout_raw = os.environ.get("WORKSPACE_LOCAL_LLM_TIMEOUT_SECS")
        if timeout_raw:
            timeout = float(timeout_raw)
    except (TypeError, ValueError):
        timeout = 5.0

    headers: dict[str, str] = {}
    api_key = os.environ.get("WORKSPACE_LOCAL_LLM_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(models_url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        # Covers connection refused, DNS failure, timeout, HTTPError too.
        reason = getattr(e, "reason", str(e))
        return {
            "endpoint_reachable": False,
            "endpoint_url": base,
            "error": f"unreachable: {reason}",
        }
    except Exception as e:  # noqa: BLE001 — best-effort probe
        return {
            "endpoint_reachable": False,
            "endpoint_url": base,
            "error": f"{type(e).__name__}: {e}",
        }

    # OpenAI shape: {"object": "list", "data": [{"id": "..."}, ...]}
    try:
        data = payload.get("data", []) if isinstance(payload, dict) else []
        ids = [m.get("id") for m in data if isinstance(m, dict)]
    except Exception:  # noqa: BLE001
        ids = []
    if model_id in ids:
        return {
            "endpoint_reachable": True,
            "endpoint_url": base,
            "error": "",
        }
    return {
        "endpoint_reachable": False,
        "endpoint_url": base,
        "error": f"endpoint reachable but did not list {model_id!r}",
    }


def health_check(entry: ModelEntry) -> dict[str, Any]:
    """Return a structured health report for one model entry.

    Two dimensions are probed:

    - ``artifact_exists`` / ``status`` — does the on-disk file exist? Original
      0.6.8.1 behaviour. Status codes: ``ok`` / ``missing`` / ``empty``.
    - ``endpoint_reachable`` — does the configured OpenAI-compatible HTTP
      endpoint (``WORKSPACE_LOCAL_LLM_URL``) answer ``GET /v1/models`` and
      list the queried model? The health check covers both the local artifact
      and server reachability.

    Return shape (in addition to legacy keys preserved for back-compat)::

        {
          "id": ...,
          "status":             "ok" | "missing" | "empty",
          "detail":             "...",
          "size_bytes":         <int>,
          "exists":             <bool>,
          "artifact_exists":    <bool>,             # 0.6.8.2 alias of `exists`
          "endpoint_reachable": True | False | None,
          "endpoint_url":       "<url or empty>",
          "endpoint_error":     "<error string or empty>",
          "role":               "<first-role or empty>",
        }
    """
    role = entry.roles[0] if entry.roles else ""

    if not entry.artifact_path:
        base = {
            "id": entry.id,
            "status": "missing",
            "detail": "no artifact_path recorded",
            "size_bytes": 0,
            "exists": False,
            "artifact_exists": False,
            "role": role,
        }
    else:
        p = Path(entry.artifact_path).expanduser()
        if not p.exists():
            base = {
                "id": entry.id,
                "status": "missing",
                "detail": f"file not found at {p}",
                "size_bytes": 0,
                "exists": False,
                "artifact_exists": False,
                "role": role,
            }
        else:
            size = p.stat().st_size
            if size == 0:
                base = {
                    "id": entry.id,
                    "status": "empty",
                    "detail": "file exists but is zero bytes",
                    "size_bytes": 0,
                    "exists": True,
                    "artifact_exists": True,
                    "role": role,
                }
            else:
                base = {
                    "id": entry.id,
                    "status": "ok",
                    "detail": "artifact present",
                    "size_bytes": size,
                    "exists": True,
                    "artifact_exists": True,
                    "role": role,
                }

    probe = _probe_endpoint(entry.id)
    base["endpoint_reachable"] = probe["endpoint_reachable"]
    base["endpoint_url"] = probe["endpoint_url"]
    base["endpoint_error"] = probe["error"]
    return base


# ---------------------------------------------------------------------------
# Pull (wraps the marketplace package's pull_models.sh if installed)
# ---------------------------------------------------------------------------


def pull_model(model_id: str, *, package_root: Path | None = None) -> dict[str, Any]:
    """Invoke a marketplace package's pull_models.sh for ``model_id``.

    Returns a structured result with the script's exit code and stdout/stderr.
    This is the Python-side ergonomic wrapper around the bash primitive that a
    model-shipping package provides.

    ``package_root`` is the marketplace package directory containing
    ``scripts/pull_models.sh``. If None, walks
    ``<this_repo>/workspace-marketplace/*/scripts/pull_models.sh`` looking for the
    first one that lists ``model_id``. (Tests pass an explicit path so they
    don't need the marketplace layout.)
    """
    import subprocess

    if package_root is None:
        # Best-effort lookup — most callers should pass explicit path.
        # Walk up from this file to find ``workspace-marketplace/``.
        here = Path(__file__).resolve()
        candidate = None
        for ancestor in here.parents:
            mp = ancestor / "workspace-marketplace"
            if mp.is_dir():
                # Find the first package with a pull_models.sh
                for pkg in mp.iterdir():
                    script = pkg / "scripts" / "pull_models.sh"
                    if script.exists():
                        candidate = pkg
                        break
                if candidate:
                    break
        if candidate is None:
            return {
                "ok": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "no marketplace package with pull_models.sh found",
            }
        package_root = candidate

    script = package_root / "scripts" / "pull_models.sh"
    if not script.exists():
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"pull script not found at {script}",
        }

    try:
        result = subprocess.run(
            ["bash", str(script), "--only", model_id],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"subprocess error: {e}",
        }
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonicalise_role(role: str) -> str:
    r = role.strip()
    if r.lower() == "lock-tier-c":
        return "lock-tier-C"
    if r not in VALID_ROLES:
        raise InvalidRoleError(
            f"role must be one of {VALID_ROLES!r}; got {role!r}"
        )
    return r


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
