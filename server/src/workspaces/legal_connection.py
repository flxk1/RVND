# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-legal's connection algebra — RVND owns neither.

RVND's parallel legal-connection composition engine is RETIRED. The connection
vocabulary, the partial composition table, the inverse map, and the
connection → 5D projection now live in ``loomground-legal``
(``artifacts/connections.json``), and the composition **mechanism** is the
solver's ``RelationAlgebra`` (``loomground_legal.connection_algebra()``).

This module is a thin compatibility seam: it re-exports that algebra behind the
historical import surface (``Connection``, ``compose``, ``compose_path``,
``ESCALATE``, ``GOVERNING``, ``is_connection``, ``inverse``, ``dimension``) so
existing callers are unchanged, while carrying **no algebra of its own** — no
composition table, no left-fold, no escalate rule. Those are the consumed
package's, whole and entire. The seam exists only to keep the retirement a small
diff; callers migrate to ``import loomground_legal`` directly and this file is
deleted.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Union

# Upstream imports are confined to the adapters/ seam (RVND boundary rule):
# adapters/legal re-exports loomground-legal's connection algebra + the solver's
# ESCALATE sentinel and 5D Dimension.
from .adapters.legal import (
    ESCALATE,
    Dimension,
    GOVERNING as _GOVERNING_VALUES,
    connection_algebra as _connection_algebra,
    is_connection as _is_connection,
    load_connections as _load_connections,
)

__all__ = [
    "Connection", "ESCALATE", "Composed", "VOCABULARY", "GOVERNING",
    "is_connection", "compose", "compose_path", "inverse", "dimension",
    "ALGEBRA_LAWS",
]

_ALG = _connection_algebra()

# The connection vocabulary as an enum, BUILT FROM loomground-legal's data so
# RVND never re-declares it. Member name == value.upper() reproduces the
# historical enum exactly (``MEMBER_OF = "member_of"``, ...), so every existing
# ``Connection.MEMBER_OF`` reference and identity check is unchanged.
Connection = Enum(  # type: ignore[misc]
    "Connection",
    {name.upper(): name for name in sorted(_load_connections()["vocabulary"])},
)

#: A composed outcome: a Connection, the ESCALATE sentinel, or None.
Composed = Union["Connection", object, None]

#: The connection vocabulary as raw values (historical shape).
VOCABULARY = frozenset(c.value for c in Connection)

#: Relations that, as the result of a reach computation, mean a legal order
#: governs the entity — enum-typed (historical shape), sourced from legal's data.
GOVERNING = frozenset(Connection(v) for v in _GOVERNING_VALUES)


def _to_conn(result: object) -> Composed:
    """Map a solver-algebra result (vocab string | ESCALATE | None) back to the
    historical ``Composed`` shape (Connection | ESCALATE | None)."""
    if result is ESCALATE or result is None:
        return result
    return Connection(result)


def is_connection(name: str) -> bool:
    """True if ``name`` is a relation in the (consumed) connection vocabulary."""
    return _is_connection(name)


def compose(a: "Connection", b: "Connection") -> Composed:
    """Compose a two-step chain — delegated to the solver ``RelationAlgebra``."""
    return _to_conn(_ALG.compose(a.value, b.value))


def compose_path(chain: list) -> tuple:
    """Left-fold a path of connections — delegated whole to the solver algebra.
    Returns ``(result, escalated)`` in the historical shape."""
    result, escalated = _ALG.compose_path([c.value for c in chain])
    return _to_conn(result), escalated


def inverse(c: "Connection") -> Optional["Connection"]:
    """The dual relation where a clean one is declared, else None (consumed)."""
    inv = _ALG.inverse(c.value)
    return Connection(inv) if inv is not None else None


def dimension(c: "Connection") -> Dimension:
    """The 5D reasoning dimension a connection projects onto (consumed)."""
    return _ALG.dimension(c.value)


# The algebra's laws, for documentation/provenance. The laws HOLD because
# loomground-legal's table says so; nothing here computes them.
ALGEBRA_LAWS: tuple[tuple[str, str], ...] = (
    ("LC-1", "Composition is partial: a chain yields a Connection, ESCALATE "
             "(contested), or None (nothing follows) — never a guessed relation."),
    ("LC-2", "Establishment under a state + that state's membership of a higher "
             "order ⇒ SUBJECT_TO the higher order (Art 3(1) GDPR logic)."),
    ("LC-3", "Mere party_to a treaty does not reach a private party without "
             "incorporation/self-execution → ESCALATE."),
    ("LC-4", "Corporate-group reach (controls/parent_of ∘ subjection) is "
             "contested → ESCALATE, never auto-attributed."),
    ("LC-5", "Mutual recognition, adequacy and renvoi are not transitive → "
             "ESCALATE on chaining."),
)
