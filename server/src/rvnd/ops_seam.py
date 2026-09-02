# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The rvnd.ops bundle seam (ADR-0004): op-registry + lifecycle-hook contract.

Imported by the engine, imports nothing upward. Providers are zero-arg
factories returning an ``OpBundle``; the engine assembles ONE ``Registry`` from
every discovered bundle. Discovery is entry points (``group="rvnd.ops"``), so
the engine's source never names a plugin package. Fail-closed: a duplicate
``(facade, op)`` across providers raises at assembly.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Callable, Optional, Sequence

Handler = Callable[[dict, "HostCtx"], dict]
Hook = Callable[..., Any]


@dataclass(frozen=True)
class HostCtx:
    """The DI handle passed DOWN into every handler/hook. Phase 1 exposes only
    ``log_root()`` (the three governance handlers need it), built from the
    engine's existing ``_log_root`` seam so a test that patches it still takes
    effect here."""
    _log_root: Callable[[], Any]

    def log_root(self) -> Any:
        return self._log_root()


@dataclass(frozen=True)
class OpSpec:
    facade: str
    op: str
    handler: Handler
    required: Sequence[str] = ()
    optional: Sequence[str] = ()
    note: str = ""
    # None ⇒ omit from the help entry (an unmarked read-only op); an explicit
    # bool renders verbatim as today's literal does. Kept tri-state so help_for
    # round-trips the pre-seam payload byte-for-byte.
    mutates: Optional[bool] = None
    governance_layer: bool = False
    enabled_when: Optional[Callable[[], bool]] = None


@dataclass(frozen=True)
class ConnectCtx:
    """Stable connect-time primitives the engine owns and hands to on_connect."""
    connid: Optional[str]
    pid: Optional[int]
    session_id: str
    agent: str
    transport: str


@dataclass(frozen=True)
class OpBundle:
    provider_id: str
    specs: Sequence[OpSpec] = ()
    on_connect: Sequence[Hook] = ()
    on_initialize: Sequence[Hook] = ()
    on_disconnect: Sequence[Hook] = ()
    pre_op: Sequence[Hook] = ()          # runs at facade top (see Registry.pre_op_for)
    surface_fragment: Optional[str] = None
    capability_fragment: Optional[str] = None


class Registry:
    """Merged view over every provider's bundle. Assembled once; a duplicate
    ``(facade, op)`` fails closed at construction — op names are the MCP surface
    identity, ambiguity is not tolerable."""

    def __init__(self, bundles: Sequence[OpBundle]):
        self._specs: dict[tuple[str, str], OpSpec] = {}
        self._pre_op: list[Hook] = []
        self._on_connect: list[Hook] = []
        self._on_disconnect: list[Hook] = []
        for b in bundles:
            for spec in b.specs:
                key = (spec.facade, spec.op)
                if key in self._specs:
                    prior = self._specs[key]
                    raise ValueError(
                        f"duplicate op {key!r}: provider {b.provider_id!r} "
                        f"collides with {prior.handler.__module__}")
                self._specs[key] = spec
            self._pre_op.extend(b.pre_op)
            self._on_connect.extend(b.on_connect)
            self._on_disconnect.extend(b.on_disconnect)

    def lookup(self, facade: str, op: str) -> Optional[OpSpec]:
        return self._specs.get((facade, op))

    def help_for(self, facade: str) -> list[dict]:
        """Help entries for one facade, in the SAME shape as the hand-authored
        ``_ops`` literal: op/required always, optional/mutates only when set,
        note when non-empty — verbatim, so the composed payload round-trips."""
        out: list[dict] = []
        for (f, _op), spec in self._specs.items():
            if f != facade:
                continue
            entry: dict[str, Any] = {"op": spec.op, "required": list(spec.required)}
            if spec.optional:
                entry["optional"] = list(spec.optional)
            if spec.mutates is not None:
                entry["mutates"] = spec.mutates
            if spec.note:
                entry["note"] = spec.note
            out.append(entry)
        return out

    # Phase 1: pre_op is scoped by CALL SITE, not by facade — the engine invokes
    # pre_op_for only at workspace_workflow's top, exactly as today. Hoisting the
    # clientInfo capture to every facade is Phase 1b (a behavior change with its
    # own baseline bump), so the facade arg is reserved for that later filtering.
    def pre_op_for(self, facade: str) -> list[Hook]:
        return list(self._pre_op)

    def on_connect(self) -> list[Hook]:
        return list(self._on_connect)

    def on_disconnect(self) -> list[Hook]:
        return list(self._on_disconnect)


def load_plugins() -> list[OpBundle]:
    """Discover every ``rvnd.ops`` provider, call its zero-arg factory, return
    the bundles. Never crashes when no external plugins exist (core alone is a
    valid, complete composition)."""
    bundles: list[OpBundle] = []
    eps = entry_points(group="rvnd.ops")
    for ep in eps:
        bundles.append(ep.load()())
    return bundles


_REGISTRY: Optional[Registry] = None


def load_registry() -> Registry:
    """Assemble the process-wide registry once (idempotent). Callable from both
    the facade dispatch and main()."""
    global _REGISTRY
    if _REGISTRY is None:
        bundles = load_plugins()
        # Fail-closed: the in-tree rvnd-core provider MUST be discovered. A stale
        # editable install / missing entry point would otherwise SILENTLY drop the
        # core ops AND the clientInfo/presence lifecycle hooks with no error.
        if not any(b.provider_id == "rvnd-core" for b in bundles):
            raise RuntimeError(
                "rvnd.ops registry: core provider 'rvnd-core' not discovered — "
                "the 'rvnd.ops' entry point is missing (stale install?); refusing "
                "to serve a registry without the core ops and lifecycle hooks")
        _REGISTRY = Registry(bundles)
    return _REGISTRY
