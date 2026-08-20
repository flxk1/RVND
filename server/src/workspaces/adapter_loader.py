# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Adapter loader for workspace-adapter.

Reads an adapter YAML file, validates it against the schema, instantiates the
right adapter class for the declared kind, and returns a dispatchable
callable. The orchestrator's `dispatch_skill` flow ultimately calls into the
returned callable; Workspace's wrap (lock, capture, audit) is applied at that
boundary, not inside the adapter.

Design intent:
- The loader is pure I/O + validation + dispatch routing. No side effects.
- Each supported adapter kind lives in its own module under `adapters/`.
- Missing or unknown kinds are rejected during declaration validation.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# YAML is the only external dep. We import it lazily so the test for "schema
# is loadable as JSON" can run in environments without PyYAML.
try:
    import yaml  # type: ignore
    _HAVE_YAML = True
except ImportError:
    yaml = None  # type: ignore
    _HAVE_YAML = False


# -----------------------------------------------------------------------------
# Adapter protocol — every adapter kind implements this.
# -----------------------------------------------------------------------------


class Adapter(Protocol):
    """Every adapter kind exposes a single dispatch method."""

    kind: str

    def dispatch(self, payload: dict[str, Any], *,
                 folder_context: str | None = None) -> dict[str, Any]:
        """Run the underlying capability with the given payload.

        Returns a dict that the orchestrator passes back as the skill's
        response. The orchestrator wraps lock + capture + audit around
        this call; do not perform any of those inside the adapter.
        """
        ...


# -----------------------------------------------------------------------------
# Adapter declaration (the result of loading + validating one YAML file).
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterDeclaration:
    """Parsed and validated adapter declaration. Frozen so the loader's
    output is immutable — adapter instances read from this snapshot, never
    mutate it."""

    name: str
    description: str
    kind: str
    input_schema: dict[str, Any]
    output_mapping: list[dict[str, str]] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)
    # Per-kind config, keyed by kind name (e.g. {"mcp_tool": {...}})
    kind_config: dict[str, Any] = field(default_factory=dict)
    # Source path for round-trip diff in the registry
    source_path: str | None = None


# -----------------------------------------------------------------------------
# Schema validation. Minimal, on-purpose — full JSON-Schema validation would
# pull a heavy dep. The loader checks the keys that matter for routing and
# leaves deeper input-schema validation to the adapter's runtime.
# -----------------------------------------------------------------------------


VALID_KINDS = {"mcp_tool", "local_llm"}

REQUIRED_TOP_LEVEL = {"name", "description", "kind", "input_schema"}


class AdapterValidationError(ValueError):
    """Raised when an adapter YAML fails schema validation."""


def _validate(raw: dict[str, Any], source_path: str | None) -> None:
    """Validate the parsed dict against the schema. Raises on failure."""
    if not isinstance(raw, dict):
        raise AdapterValidationError(
            f"adapter file must be a YAML mapping at top level "
            f"({source_path or '<unknown>'})"
        )

    missing = REQUIRED_TOP_LEVEL - set(raw.keys())
    if missing:
        raise AdapterValidationError(
            f"adapter missing required keys {sorted(missing)} "
            f"({source_path or '<unknown>'})"
        )

    kind = raw["kind"]
    if kind not in VALID_KINDS:
        raise AdapterValidationError(
            f"adapter kind {kind!r} not in {sorted(VALID_KINDS)} "
            f"({source_path or '<unknown>'})"
        )

    if kind not in raw:
        raise AdapterValidationError(
            f"adapter declared kind={kind!r} but no '{kind}' config block "
            f"is present ({source_path or '<unknown>'})"
        )

    if not isinstance(raw["input_schema"], dict):
        raise AdapterValidationError(
            f"adapter input_schema must be a mapping "
            f"({source_path or '<unknown>'})"
        )


# -----------------------------------------------------------------------------
# Loader entry points.
# -----------------------------------------------------------------------------


def load_yaml_file(path: str | Path) -> AdapterDeclaration:
    """Read + validate one adapter YAML file. Returns the declaration."""
    if not _HAVE_YAML:
        raise RuntimeError(
            "PyYAML is required to load adapter YAML files. "
            "Install: pip install pyyaml"
        )
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    return _from_raw(raw, source_path=str(p.resolve()))


def load_dict(raw: dict[str, Any],
              source_path: str | None = None) -> AdapterDeclaration:
    """Validate a pre-parsed dict and produce a declaration. Useful in tests
    where the YAML dep should not be required."""
    return _from_raw(raw, source_path=source_path)


def _from_raw(raw: dict[str, Any],
              *,
              source_path: str | None) -> AdapterDeclaration:
    _validate(raw, source_path)
    kind = raw["kind"]
    return AdapterDeclaration(
        name=raw["name"],
        description=raw["description"],
        kind=kind,
        input_schema=raw["input_schema"],
        output_mapping=raw.get("output_mapping", []) or [],
        audit=raw.get("audit", {}) or {},
        kind_config=raw.get(kind, {}) or {},
        source_path=source_path,
    )


# -----------------------------------------------------------------------------
# Dispatch routing. Given a declaration, instantiate the right adapter class.
# -----------------------------------------------------------------------------


def instantiate(decl: AdapterDeclaration) -> Adapter:
    """Return an Adapter instance for the declaration's kind."""
    # Absorbed into the workspaces package (was workspace-adapter/runtime/adapters/).
    module_name = f"workspaces.adapters.{decl.kind}"
    try:
        mod = importlib.import_module(module_name)
    except ImportError:
        # Legacy in-repo path (pre-absorption layout), kept as a fallback.
        mod = importlib.import_module(f"runtime.adapters.{decl.kind}")

    # Each adapter module exposes a `build(decl)` factory function.
    if not hasattr(mod, "build"):
        raise RuntimeError(
            f"adapter module {module_name} must export a `build(decl)` "
            f"factory function returning an Adapter instance"
        )
    return mod.build(decl)


# -----------------------------------------------------------------------------
# Convenience: load + instantiate in one call.
# -----------------------------------------------------------------------------


def load_and_instantiate(path: str | Path) -> tuple[AdapterDeclaration, Adapter]:
    """One-shot for the common case. Returns (declaration, adapter)."""
    decl = load_yaml_file(path)
    return decl, instantiate(decl)
